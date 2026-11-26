/* One independent native FLAC stream per HTTPS packet. The caller supplies
 * the format announced by the room. All exported sizes are bytes. */
#include <stdint.h>
#include <string.h>
#include <FLAC/stream_encoder.h>
#include <FLAC/stream_decoder.h>

enum { MAX_PCM = 192000 * 2 * 4 / 2, CAPACITY = MAX_PCM + 8192 };
static uint8_t input[CAPACITY], output[CAPACITY];
static int32_t samples[4096 * 2];
static size_t pos, length, limit;
static unsigned rate, depth;
static int invalid;

uint8_t *packet_input(void) { return input; }
uint8_t *packet_output(void) { return output; }

static int start(unsigned size, unsigned hz, unsigned bits) {
    pos = length = 0;
    invalid = 0;
    rate = hz;
    depth = bits;
    if (!size || size > CAPACITY || hz < 8000 || hz > 192000 ||
        (bits != 16 && bits != 24 && bits != 32)) return 0;
    limit = hz * 2 * (bits / 8) / 2;
    return 1;
}

static FLAC__StreamEncoderWriteStatus write_bytes(const FLAC__StreamEncoder *e,
        const FLAC__byte data[], size_t n, uint32_t frames, uint32_t index, void *ctx) {
    if (n > CAPACITY - pos) return FLAC__STREAM_ENCODER_WRITE_STATUS_FATAL_ERROR;
    memcpy(output + pos, data, n);
    pos += n;
    if (pos > length) length = pos;
    return FLAC__STREAM_ENCODER_WRITE_STATUS_OK;
}
static FLAC__StreamEncoderSeekStatus seek_output(const FLAC__StreamEncoder *e, FLAC__uint64 offset, void *ctx) {
    if (offset > length) return FLAC__STREAM_ENCODER_SEEK_STATUS_ERROR;
    pos = (size_t)offset;
    return FLAC__STREAM_ENCODER_SEEK_STATUS_OK;
}
static FLAC__StreamEncoderTellStatus tell_output(const FLAC__StreamEncoder *e, FLAC__uint64 *offset, void *ctx) {
    *offset = pos;
    return FLAC__STREAM_ENCODER_TELL_STATUS_OK;
}

unsigned encode_packet(unsigned size, unsigned hz, unsigned bits) {
    if (!start(size, hz, bits)) return 0;
    const unsigned width = bits / 8, frames = size / (2 * width);
    if (size > limit || size != frames * 2 * width) return 0;
    FLAC__StreamEncoder *enc = FLAC__stream_encoder_new();
    if (!enc) return 0;
    FLAC__stream_encoder_set_compression_level(enc, 1);
    FLAC__stream_encoder_set_channels(enc, 2);
    FLAC__stream_encoder_set_bits_per_sample(enc, bits);
    FLAC__stream_encoder_set_sample_rate(enc, hz);
    FLAC__stream_encoder_set_total_samples_estimate(enc, frames);
    int ok = FLAC__stream_encoder_init_stream(enc, write_bytes, seek_output, tell_output, NULL, NULL) ==
             FLAC__STREAM_ENCODER_INIT_STATUS_OK;
    for (unsigned f = 0; ok && f < frames;) {
        unsigned n = frames - f < 4096 ? frames - f : 4096;
        for (unsigned i = 0; i < n * 2; ++i) {
            uint32_t value = 0;
            memcpy(&value, input + (f * 2 + i) * width, width);
            samples[i] = (int32_t)(value << (32 - bits)) >> (32 - bits);
        }
        ok = FLAC__stream_encoder_process_interleaved(enc, samples, n);
        f += n;
    }
    ok = FLAC__stream_encoder_finish(enc) && ok;
    FLAC__stream_encoder_delete(enc);
    return ok ? length : 0;
}

static size_t available;
static FLAC__StreamDecoderReadStatus read_bytes(const FLAC__StreamDecoder *d, FLAC__byte buf[], size_t *n, void *ctx) {
    if (*n > available - pos) *n = available - pos;
    if (!*n) return FLAC__STREAM_DECODER_READ_STATUS_END_OF_STREAM;
    memcpy(buf, input + pos, *n);
    pos += *n;
    return FLAC__STREAM_DECODER_READ_STATUS_CONTINUE;
}
static FLAC__StreamDecoderWriteStatus write_pcm(const FLAC__StreamDecoder *d, const FLAC__Frame *frame,
        const FLAC__int32 *const channels[], void *ctx) {
    const FLAC__FrameHeader *h = &frame->header;
    const unsigned width = depth / 8;
    if (h->channels != 2 || h->sample_rate != rate || h->bits_per_sample != depth ||
        h->blocksize > (limit - length) / (width * 2)) return FLAC__STREAM_DECODER_WRITE_STATUS_ABORT;
    for (unsigned i = 0; i < h->blocksize; ++i) {
        for (unsigned c = 0; c < 2; ++c) {
            memcpy(output + length, &channels[c][i], width);
            length += width;
        }
    }
    return FLAC__STREAM_DECODER_WRITE_STATUS_CONTINUE;
}
static void decode_error(const FLAC__StreamDecoder *d, FLAC__StreamDecoderErrorStatus status, void *ctx) {
    invalid = 1;
}

unsigned decode_packet(unsigned size, unsigned hz, unsigned bits) {
    if (!start(size, hz, bits) || size < 42 || memcmp(input, "fLaC", 4) ||
        (input[4] & 127) != 0 || input[5] || input[6] || input[7] != 34) return 0;
    uint32_t format = ((uint32_t)input[18] << 24) | ((uint32_t)input[19] << 16) |
                      ((uint32_t)input[20] << 8) | input[21];
    uint32_t frames = ((uint32_t)input[22] << 24) | ((uint32_t)input[23] << 16) |
                      ((uint32_t)input[24] << 8) | input[25];
    if (format >> 12 != hz || ((format >> 9) & 7) != 1 || ((format >> 4) & 31) + 1 != bits ||
        (format & 15) || !frames || frames > hz / 2) return 0;
    limit = frames * 2 * (bits / 8);
    available = size;
    FLAC__StreamDecoder *dec = FLAC__stream_decoder_new();
    if (!dec) return 0;
    FLAC__stream_decoder_set_md5_checking(dec, true);
    int ok = FLAC__stream_decoder_init_stream(dec, read_bytes, NULL, NULL, NULL, NULL,
        write_pcm, NULL, decode_error, NULL) == FLAC__STREAM_DECODER_INIT_STATUS_OK;
    ok = ok && FLAC__stream_decoder_process_until_end_of_stream(dec);
    ok = FLAC__stream_decoder_finish(dec) && ok && !invalid && length == limit;
    FLAC__stream_decoder_delete(dec);
    return ok ? length : 0;
}
