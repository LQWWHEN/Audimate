#pragma once
#include <stdint.h>

#ifdef AUDIMATE_CAPTURE_EXPORTS
#define AUDIMATE_API __declspec(dllexport)
#else
#define AUDIMATE_API __declspec(dllimport)
#endif

#ifdef __cplusplus
extern "C" {
#endif

typedef struct AudimateCapture AudimateCapture;

enum {
    /* Fixed layout for the original audimate_open/audimate_read ABI. */
    AUDIMATE_SAMPLE_RATE = 48000,
    AUDIMATE_CHANNELS = 2,
    AUDIMATE_BLOCKS_PER_SECOND = 100,
    AUDIMATE_FRAMES_PER_BLOCK = AUDIMATE_SAMPLE_RATE / AUDIMATE_BLOCKS_PER_SECOND
};

typedef struct AudimateFormat {
    uint32_t sample_rate;     /* 8000..192000 Hz, divisible by 100 for 10 ms blocks */
    uint32_t bits_per_sample; /* Signed little-endian integer PCM: 16, 24 or 32 */
} AudimateFormat;

typedef struct AudimateBlockInfo {
    uint64_t sequence;
    uint32_t discontinuities;
} AudimateBlockInfo;

typedef struct AudimateBlock {
    uint64_t sequence;  /* Starts at 0 per session. Gaps indicate queue overflow. */
    uint32_t discontinuities; /* Cumulative count of WASAPI discontinuity flags. */
    int16_t pcm[AUDIMATE_FRAMES_PER_BLOCK * AUDIMATE_CHANNELS]; /* L, R */
} AudimateBlock;

/* All results are signed HRESULTs. open waits for WASAPI startup and sets
   *out to NULL on failure. Each handle is bound to one root PID for its life. */
AUDIMATE_API int32_t __cdecl audimate_open(uint32_t pid, AudimateCapture** out);

/* Copies the requested format. Successful sessions deliver that exact format. */
AUDIMATE_API int32_t __cdecl audimate_open_format(uint32_t pid,
    const AudimateFormat* format, AudimateCapture** out);

/* Single consumer. S_OK = one complete 10 ms block, S_FALSE = timeout.
   A negative HRESULT is terminal. out is only written on S_OK. */
AUDIMATE_API int32_t __cdecl audimate_read(AudimateCapture* capture,
    AudimateBlock* out, uint32_t timeout_ms);

/* Same read semantics, for any session format. Provide storage for at least
   sample_rate / 100 * 2 * (bits_per_sample / 8) bytes. A short buffer
   returns HRESULT_FROM_WIN32(ERROR_INSUFFICIENT_BUFFER) without consuming a block.
   audimate_read only accepts sessions using the original 48000 Hz / 16-bit format. */
AUDIMATE_API int32_t __cdecl audimate_read_pcm(AudimateCapture* capture,
    void* pcm, uint32_t capacity_bytes, AudimateBlockInfo* info, uint32_t timeout_ms);

/* May run concurrently with read. Discards buffered audio and wakes readers.
   Serialize stop/close calls. After all readers have returned, close the handle. */
AUDIMATE_API void __cdecl audimate_stop(AudimateCapture* capture);
AUDIMATE_API void __cdecl audimate_close(AudimateCapture* capture);

#ifdef __cplusplus
}
#endif
