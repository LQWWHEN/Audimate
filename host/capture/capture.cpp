#include "capture.h"

#include <windows.h>
#include <audioclient.h>
#if __has_include(<audioclientactivationparams.h>)
#include <audioclientactivationparams.h>
#endif
#include <mmdeviceapi.h>
#include <ks.h>
#include <ksmedia.h>
#include <wrl/client.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstring>
#include <memory>
#include <mutex>
#include <new>
#include <system_error>
#include <thread>
#include <vector>

namespace {
using Microsoft::WRL::ComPtr;

// SDK 19041 provides ActivateAudioInterfaceAsync but lacks the process-loopback
// parameter declarations. This is the same VT_BLOB layout as the newer SDK.
// Windows determines availability when we activate the virtual device.
#ifndef VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK
constexpr wchar_t VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK[] = L"VAD\\Process_Loopback";
constexpr DWORD AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK = 1;
constexpr DWORD PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE = 0;
struct AUDIOCLIENT_ACTIVATION_PARAMS {
    DWORD ActivationType;
    struct {
        DWORD TargetProcessId;
        DWORD ProcessLoopbackMode;
    } ProcessLoopbackParams;
};
#endif
static_assert(sizeof(AUDIOCLIENT_ACTIVATION_PARAMS) == 3 * sizeof(DWORD));

struct CloseHandleDeleter {
    void operator()(HANDLE handle) const { CloseHandle(handle); }
};
using Handle = std::unique_ptr<void, CloseHandleDeleter>;

HRESULT last_error() { return HRESULT_FROM_WIN32(GetLastError()); }
const HRESULT source_exited = HRESULT_FROM_WIN32(ERROR_PROCESS_ABORTED);
const HRESULT stopped = HRESULT_FROM_WIN32(ERROR_OPERATION_ABORTED);
constexpr AudimateFormat default_format{AUDIMATE_SAMPLE_RATE, 16};

bool valid_format(const AudimateFormat& fmt) {
    return fmt.sample_rate >= 8000 && fmt.sample_rate <= 192000 &&
        fmt.sample_rate % AUDIMATE_BLOCKS_PER_SECOND == 0 &&
        (fmt.bits_per_sample == 16 || fmt.bits_per_sample == 24 || fmt.bits_per_sample == 32);
}

WAVEFORMATEXTENSIBLE wave_format(const AudimateFormat& fmt) {
    WAVEFORMATEXTENSIBLE wave{};
    wave.Format.wFormatTag = WAVE_FORMAT_EXTENSIBLE;
    wave.Format.cbSize = sizeof(wave) - sizeof(WAVEFORMATEX);
    wave.Format.nChannels = AUDIMATE_CHANNELS;
    wave.Format.nSamplesPerSec = fmt.sample_rate;
    wave.Format.wBitsPerSample = static_cast<WORD>(fmt.bits_per_sample);
    wave.Format.nBlockAlign = static_cast<WORD>(AUDIMATE_CHANNELS * (fmt.bits_per_sample / 8));
    wave.Format.nAvgBytesPerSec = fmt.sample_rate * wave.Format.nBlockAlign;
    wave.Samples.wValidBitsPerSample = wave.Format.wBitsPerSample;
    wave.dwChannelMask = SPEAKER_FRONT_LEFT | SPEAKER_FRONT_RIGHT;
    wave.SubFormat = {STATIC_KSDATAFORMAT_SUBTYPE_PCM};
    return wave;
}

// The completion callback owns its event. Even after an activation timeout a
// late COM callback never references the capture session or the caller's stack.
class ActivationDone final : public IActivateAudioInterfaceCompletionHandler, public IAgileObject {
public:
    Handle event{CreateEventW(nullptr, FALSE, FALSE, nullptr)};

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** out) override {
        if (!out) return E_POINTER;
        if (IsEqualIID(iid, __uuidof(IUnknown)) ||
            IsEqualIID(iid, __uuidof(IActivateAudioInterfaceCompletionHandler))) {
            *out = static_cast<IActivateAudioInterfaceCompletionHandler*>(this);
        } else if (IsEqualIID(iid, __uuidof(IAgileObject))) {
            *out = static_cast<IAgileObject*>(this);
        } else {
            *out = nullptr;
            return E_NOINTERFACE;
        }
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++refs_; }
    ULONG STDMETHODCALLTYPE Release() override {
        auto remaining = --refs_;
        if (!remaining) delete this;
        return remaining;
    }
    HRESULT STDMETHODCALLTYPE ActivateCompleted(IActivateAudioInterfaceAsyncOperation*) override {
        SetEvent(event.get());
        return S_OK;
    }

private:
    std::atomic<ULONG> refs_{1};
};

HRESULT activate(DWORD pid, HANDLE process, IAudioClient** client) {
    AUDIOCLIENT_ACTIVATION_PARAMS params{};
    params.ActivationType = AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK;
    params.ProcessLoopbackParams.TargetProcessId = pid;
    params.ProcessLoopbackParams.ProcessLoopbackMode = PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE;
    PROPVARIANT blob{};
    blob.vt = VT_BLOB;
    blob.blob.cbSize = sizeof(params);
    blob.blob.pBlobData = reinterpret_cast<BYTE*>(&params);

    ComPtr<ActivationDone> done;
    done.Attach(new (std::nothrow) ActivationDone());
    if (!done) return E_OUTOFMEMORY;
    if (!done.Get()->event) return last_error();
    ComPtr<IActivateAudioInterfaceAsyncOperation> operation;
    HRESULT hr = ActivateAudioInterfaceAsync(VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK,
        __uuidof(IAudioClient), &blob, done.Get(), operation.GetAddressOf());
    if (FAILED(hr)) return hr;
    HANDLE events[] = {process, done.Get()->event.get()};
    switch (WaitForMultipleObjects(2, events, FALSE, 10000)) {
    case WAIT_OBJECT_0: return source_exited;
    case WAIT_OBJECT_0 + 1: break;
    case WAIT_TIMEOUT: return HRESULT_FROM_WIN32(ERROR_TIMEOUT);
    default: return last_error();
    }

    HRESULT activation_result = E_UNEXPECTED;
    ComPtr<IUnknown> result;
    hr = operation->GetActivateResult(&activation_result, result.GetAddressOf());
    if (FAILED(hr)) return hr;
    if (FAILED(activation_result)) return activation_result;
    if (!result) return E_UNEXPECTED;
    return result.CopyTo(client);
}
} // namespace

struct AudimateCapture {
    const WAVEFORMATEXTENSIBLE wave;
    const uint32_t block_bytes;
    Handle quit{CreateEventW(nullptr, TRUE, FALSE, nullptr)};
    std::thread worker;
    std::mutex mutex;
    std::condition_variable changed;
    bool initialized = false;
    HRESULT status = S_OK;

    // Single producer. Only complete blocks enter the shared 200 ms queue.
    struct Packet {
        AudimateBlockInfo info{};
        std::vector<BYTE> pcm;
    };
    Packet partial;
    size_t filled = 0;
    std::array<Packet, 20> blocks;
    size_t head = 0;
    size_t count = 0;

    explicit AudimateCapture(const AudimateFormat& fmt) : wave(wave_format(fmt)),
        block_bytes(fmt.sample_rate / AUDIMATE_BLOCKS_PER_SECOND * wave.Format.nBlockAlign) {
        partial.pcm.resize(block_bytes);
        for (auto& block : blocks) block.pcm.resize(block_bytes);
    }

    void set_status(HRESULT value) {
        {
            std::lock_guard<std::mutex> lock(mutex);
            initialized = true;
            status = value;
            count = 0;
        }
        changed.notify_all();
    }

    void append(const BYTE* data, size_t frames, DWORD flags) {
        if (flags & AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY) {
            ++partial.info.discontinuities;
            filled = 0;
        }
        size_t remaining = frames * wave.Format.nBlockAlign;
        while (remaining) {
            const size_t take = std::min(remaining, partial.pcm.size() - filled);
            auto dest = partial.pcm.data() + filled;
            if (flags & AUDCLNT_BUFFERFLAGS_SILENT) {
                std::memset(dest, 0, take);
            } else {
                std::memcpy(dest, data, take);
                data += take;
            }
            remaining -= take;
            filled += take;
            if (filled == block_bytes) {
                {
                    std::lock_guard<std::mutex> lock(mutex);
                    if (FAILED(status)) return;
                    if (count == blocks.size()) {
                        head = (head + 1) % blocks.size();
                        --count;
                    }
                    auto& slot = blocks[(head + count) % blocks.size()];
                    slot.pcm.swap(partial.pcm);
                    slot.info = partial.info;
                    ++count;
                }
                changed.notify_one();
                ++partial.info.sequence;
                filled = 0;
            }
        }
    }

    HRESULT capture(DWORD pid) {
        Handle process(OpenProcess(SYNCHRONIZE, FALSE, pid));
        if (!process) return last_error();
        Handle ready(CreateEventW(nullptr, FALSE, FALSE, nullptr));
        if (!ready) return last_error();
        ComPtr<IAudioClient> client;
        HRESULT hr = activate(pid, process.get(), client.GetAddressOf());
        if (FAILED(hr)) return hr;

        hr = client->Initialize(AUDCLNT_SHAREMODE_SHARED, AUDCLNT_STREAMFLAGS_LOOPBACK |
            AUDCLNT_STREAMFLAGS_EVENTCALLBACK | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM |
            AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY, 0, 0, &wave.Format, nullptr);
        if (FAILED(hr)) return hr;
        ComPtr<IAudioCaptureClient> reader;
        hr = client->GetService(IID_PPV_ARGS(reader.GetAddressOf()));
        if (FAILED(hr)) return hr;
        hr = client->SetEventHandle(ready.get());
        if (FAILED(hr)) return hr;
        hr = client->Start();
        if (FAILED(hr)) return hr;
        set_status(S_OK);

        HANDLE events[] = {quit.get(), process.get(), ready.get()};
        for (;;) {
            DWORD signal = WaitForMultipleObjects(3, events, FALSE, INFINITE);
            if (signal != WAIT_OBJECT_0 + 2) {
                hr = signal == WAIT_OBJECT_0 ? stopped :
                    signal == WAIT_OBJECT_0 + 1 ? source_exited : last_error();
                break;
            }
            // Drain all packets from this event. A silent packet may have no data pointer.
            for (;;) {
                BYTE* data = nullptr;
                UINT32 frames = 0;
                DWORD flags = 0;
                hr = reader->GetBuffer(&data, &frames, &flags, nullptr, nullptr);
                if (hr == AUDCLNT_S_BUFFER_EMPTY || FAILED(hr)) break;
                append(data, frames, flags);
                hr = reader->ReleaseBuffer(frames);
                if (FAILED(hr) || WaitForSingleObject(quit.get(), 0) == WAIT_OBJECT_0) break;
            }
            if (FAILED(hr)) break;
        }
        client->Stop();
        return hr;
    }

    void run(DWORD pid) {
        HRESULT hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
        if (SUCCEEDED(hr)) {
            hr = capture(pid);
            CoUninitialize();
        }
        set_status(hr);
    }
};

int32_t __cdecl audimate_open(uint32_t pid, AudimateCapture** out) {
    return audimate_open_format(pid, &default_format, out);
}

int32_t __cdecl audimate_open_format(uint32_t pid, const AudimateFormat* format, AudimateCapture** out) {
    if (!out) return E_POINTER;
    *out = nullptr;
    if (!format) return E_POINTER;
    if (!pid || !valid_format(*format)) return E_INVALIDARG;
    std::unique_ptr<AudimateCapture> session;
    try {
        session = std::make_unique<AudimateCapture>(*format);
        if (!session->quit) return last_error();
        session->worker = std::thread(&AudimateCapture::run, session.get(), pid);
    } catch (const std::system_error&) {
        return HRESULT_FROM_WIN32(ERROR_NO_SYSTEM_RESOURCES);
    } catch (const std::bad_alloc&) {
        return E_OUTOFMEMORY;
    }
    std::unique_lock<std::mutex> lock(session->mutex);
    session->changed.wait(lock, [&] { return session->initialized; });
    const HRESULT hr = session->status;
    lock.unlock();
    if (FAILED(hr)) {
        session->worker.join();
        return hr;
    }
    *out = session.release();
    return S_OK;
}

int32_t __cdecl audimate_read(AudimateCapture* session, AudimateBlock* out, uint32_t timeout_ms) {
    if (!session || !out) return E_POINTER;
    if (session->wave.Format.nSamplesPerSec != default_format.sample_rate ||
        session->wave.Format.wBitsPerSample != default_format.bits_per_sample) return E_INVALIDARG;
    AudimateBlockInfo info{};
    const HRESULT hr = audimate_read_pcm(session, out->pcm, sizeof(out->pcm), &info, timeout_ms);
    if (hr == S_OK) {
        out->sequence = info.sequence;
        out->discontinuities = info.discontinuities;
    }
    return hr;
}

int32_t __cdecl audimate_read_pcm(AudimateCapture* session, void* pcm, uint32_t capacity_bytes,
    AudimateBlockInfo* info, uint32_t timeout_ms) {
    if (!session || !pcm || !info) return E_POINTER;
    if (capacity_bytes < session->block_bytes) return HRESULT_FROM_WIN32(ERROR_INSUFFICIENT_BUFFER);
    std::unique_lock<std::mutex> lock(session->mutex);
    if (!session->changed.wait_for(lock, std::chrono::milliseconds(timeout_ms),
        [&] { return session->count || FAILED(session->status); })) return S_FALSE;
    if (FAILED(session->status)) return session->status;
    const auto& block = session->blocks[session->head];
    std::memcpy(pcm, block.pcm.data(), session->block_bytes);
    *info = block.info;
    session->head = (session->head + 1) % session->blocks.size();
    --session->count;
    return S_OK;
}

void __cdecl audimate_stop(AudimateCapture* session) {
    if (!session || !session->worker.joinable()) return;
    session->set_status(stopped);
    SetEvent(session->quit.get());
    session->worker.join();
}

void __cdecl audimate_close(AudimateCapture* session) {
    audimate_stop(session);
    delete session;
}
