/*
 * ab_audio.dll — AudioBench 的设备 I/O 与 EQ 内核
 *
 * 设计要点（对应 M0 方案 §6.1 / §1.2）：
 *   - 设备回调只做「从环形缓冲 memcpy 到设备」，不进入 Python、不分配、不加锁；
 *   - 生产者（Python 混音线程）通过 ab_write 写整块；超过水位就阻塞等待，
 *     于是生产者被设备「带着走」，既不会跑飞也不会堆延迟；
 *   - EQ 的逐采样递归放在这里（C），Python 只下发系数，避免 GIL 抖动进入音频路径。
 *
 * 后端：WASAPI 共享模式优先，失败自动退 WinMM（miniaudio 的 context 机制）。
 * 构建：见 native/build.ps1（MSVC x64 /MT，导出表见 ab_audio.def）。
 */
#define MA_NO_DECODING
#define MA_NO_ENCODING
#define MA_NO_RESOURCE_MANAGER
#define MA_NO_NODE_GRAPH
#define MA_NO_ENGINE
#define MA_NO_GENERATION
#define MINIAUDIO_IMPLEMENTATION
#include "miniaudio.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>

#define AB_RING_SLOTS 16
#define AB_EQ_SLOTS 64
#define AB_MAX_DEVICES 64
/* 稳态水位 4 块；设备启动瞬间会一次抓走近 2 轮缓冲，所以打开时先灌 10 块，
   稳态仍按 4 块跑（多出来的部分只影响开头，不影响延迟）。 */
#define AB_TARGET_SLOTS 4
#define AB_PREFILL_SLOTS 10

typedef struct {
    int sample_rate;
    int block_frames;
    int backend;          /* 0 none / 1 wasapi / 2 winmm / 3 other */
    float latency_ms;
    int xruns;
    uint64_t frames_played;
} ab_info_t;

static ma_context g_ctx;
static ma_device g_device;
static int g_ctx_ready = 0;
static int g_open = 0;

static float *g_ring = NULL;
static uint64_t g_ring_frames = 0;
static uint64_t g_target_frames = 0;
static volatile LONG64 g_wpos = 0;
static volatile LONG64 g_rpos = 0;
static volatile LONG g_xruns = 0;

static ab_info_t g_info = {0};
static char g_err[512] = {0};

typedef struct { float z1[2]; float z2[2]; } ab_biquad_t;
static ab_biquad_t g_eq[AB_EQ_SLOTS];

typedef struct { char name[256]; int is_default; ma_device_id id; } ab_dev_t;
static ab_dev_t g_devs[AB_MAX_DEVICES];
static int g_dev_count = 0;

/* 前向声明：ab_open 里会先 close 再重开 */
void ab_close(void);
int ab_start(void);
int ab_stop(void);
int ab_write(const float *interleaved, int frames);
int ab_flush(void);
int ab_set_target(int frames);
int ab_get_target(void);
int ab_info(ab_info_t *out);
int ab_eq_process(int slot, float *buf, int frames, int channels, const float *c);
void ab_eq_reset(int slot);
int ab_open(const char *device_utf8, int sample_rate, int block_frames, int backend_pref);
const char *ab_last_error(void);
const char *ab_version(void);

static void set_err(const char *msg)
{
    snprintf(g_err, sizeof(g_err), "%s", msg ? msg : "");
}

const char *ab_last_error(void) { return g_err; }
const char *ab_version(void) { return "ab_audio/1.0"; }

/* ---------------------------------------------------------------- 环形缓冲 */

static void ring_copy_out(float *dst, uint64_t pos, ma_uint32 frames)
{
    uint64_t cap = g_ring_frames;
    uint64_t off = pos % cap;
    uint64_t first = cap - off;
    if (first > frames) first = frames;
    memcpy(dst, g_ring + off * 2, (size_t)first * 2 * sizeof(float));
    if (frames > first)
        memcpy(dst + first * 2, g_ring, (size_t)(frames - first) * 2 * sizeof(float));
}

static void on_data(ma_device *dev, void *output, const void *input, ma_uint32 frame_count)
{
    (void)dev; (void)input;
    float *out = (float *)output;
    LONG64 w = (LONG64)InterlockedCompareExchange64(&g_wpos, 0, 0);
    LONG64 r = (LONG64)InterlockedCompareExchange64(&g_rpos, 0, 0);
    uint64_t avail = (uint64_t)(w - r);
    if (avail >= frame_count) {
        ring_copy_out(out, (uint64_t)r, frame_count);
        InterlockedExchange64(&g_rpos, (LONG64)(r + frame_count));
    } else {
        if (avail > 0) ring_copy_out(out, (uint64_t)r, (ma_uint32)avail);
        memset(out + (size_t)avail * 2, 0, (size_t)(frame_count - avail) * 2 * sizeof(float));
        InterlockedExchange64(&g_rpos, (LONG64)(r + avail));
        InterlockedIncrement(&g_xruns);
    }
    InterlockedExchangeAdd64((LONG64 *)&g_info.frames_played, (LONG64)frame_count);
}

/* ---------------------------------------------------------------- 后端/设备 */

static int backend_of(ma_backend b)
{
    switch (b) {
    case ma_backend_wasapi: return 1;
    case ma_backend_winmm:  return 2;
    default:                return 3;
    }
}

static int init_context(int pref)  /* pref: 0 自动 / 1 强制 wasapi / 2 强制 winmm */
{
    if (g_ctx_ready) return 0;
    ma_context_config cfg = ma_context_config_init();
    ma_backend order[2];
    int n = 0;
    if (pref == 1) {
        order[n++] = ma_backend_wasapi;
    } else if (pref == 2) {
        order[n++] = ma_backend_winmm;
    } else {
        order[n++] = ma_backend_wasapi;
        order[n++] = ma_backend_winmm;
    }
    for (int i = 0; i < n; i++) {
        ma_backend one[1];
        one[0] = order[i];
        if (ma_context_init(one, 1, &cfg, &g_ctx) == MA_SUCCESS) {
            g_ctx_ready = 1;
            g_info.backend = backend_of(one[0]);
            return 0;
        }
    }
    /* 兜底：交给 miniaudio 自己的优先级（可能退到 DirectSound） */
    if (ma_context_init(NULL, 0, &cfg, &g_ctx) == MA_SUCCESS) {
        g_ctx_ready = 1;
        g_info.backend = backend_of(g_ctx.backend);
        return 0;
    }
    set_err("audio context init failed (wasapi & winmm)");
    return -1;
}

static void refresh_devices(void)
{
    g_dev_count = 0;
    if (!g_ctx_ready) return;
    ma_device_info *playback = NULL;
    ma_uint32 n = 0;
    if (ma_context_get_devices(&g_ctx, &playback, &n, NULL, NULL) != MA_SUCCESS) return;
    if (n > AB_MAX_DEVICES) n = AB_MAX_DEVICES;
    for (ma_uint32 i = 0; i < n; i++) {
        snprintf(g_devs[i].name, sizeof(g_devs[i].name), "%s", playback[i].name);
        g_devs[i].is_default = playback[i].isDefault ? 1 : 0;
        g_devs[i].id = playback[i].id;
    }
    g_dev_count = (int)n;
}

int ab_device_count(void)
{
    if (!g_ctx_ready && init_context(0) != 0) return 0;
    refresh_devices();
    return g_dev_count;
}

int ab_device_name(int index, char *buf, int buflen)
{
    if (!buf || buflen <= 0 || !g_ctx_ready) return -1;
    if (index < 0 || index >= g_dev_count) { buf[0] = 0; return -1; }
    snprintf(buf, (size_t)buflen, "%s", g_devs[index].name);
    return 0;
}

int ab_device_default(void)
{
    for (int i = 0; i < g_dev_count; i++) if (g_devs[i].is_default) return i;
    return 0;
}

/* ---------------------------------------------------------------- 开关 */

int ab_open(const char *device_utf8, int sample_rate, int block_frames, int backend_pref)
{
    if (g_open) ab_close();
    g_xruns = 0;
    InterlockedExchange64(&g_wpos, 0);
    InterlockedExchange64(&g_rpos, 0);

    if (block_frames < 32) block_frames = 256;
    if (sample_rate < 8000) sample_rate = 48000;
    if (init_context(backend_pref) != 0) return -2;
    refresh_devices();

    g_ring_frames = (uint64_t)block_frames * AB_RING_SLOTS;
    if (g_ring) { free(g_ring); g_ring = NULL; }
    g_ring = (float *)calloc((size_t)g_ring_frames * 2, sizeof(float));
    if (!g_ring) { set_err("out of memory for ring buffer"); return -3; }

    uint64_t prefill = (uint64_t)block_frames * AB_PREFILL_SLOTS;
    if (prefill > g_ring_frames) prefill = g_ring_frames;
    memset(g_ring, 0, (size_t)prefill * 2 * sizeof(float));
    InterlockedExchange64(&g_wpos, (LONG64)prefill);
    InterlockedExchange64(&g_rpos, 0);

    ma_device_config cfg = ma_device_config_init(ma_device_type_playback);
    cfg.playback.format = ma_format_f32;
    cfg.playback.channels = 2;
    cfg.sampleRate = (ma_uint32)sample_rate;
    cfg.periodSizeInFrames = (ma_uint32)block_frames;
    cfg.dataCallback = on_data;
    cfg.pUserData = NULL;
    cfg.performanceProfile = ma_performance_profile_low_latency;

    if (device_utf8 && device_utf8[0]) {
        for (int i = 0; i < g_dev_count; i++) {
            if (strcmp(g_devs[i].name, device_utf8) == 0) {
                cfg.playback.pDeviceID = &g_devs[i].id;
                break;
            }
        }
    }

    if (ma_device_init(&g_ctx, &cfg, &g_device) != MA_SUCCESS) {
        set_err("ma_device_init failed");
        if (g_ring) { free(g_ring); g_ring = NULL; g_ring_frames = 0; }
        return -4;
    }
    g_info.sample_rate = (int)g_device.sampleRate;
    g_info.block_frames = (int)block_frames;
    g_info.latency_ms = (float)((double)((AB_TARGET_SLOTS + 2) * block_frames) * 1000.0
                                / (double)g_info.sample_rate);
    g_info.backend = backend_of(g_ctx.backend);
    g_info.xruns = 0;
    g_info.frames_played = 0;
    g_target_frames = (uint64_t)block_frames * AB_TARGET_SLOTS;
    g_open = 1;
    set_err("");
    return 0;
}

int ab_start(void)
{
    if (!g_open) return -1;
    return ma_device_start(&g_device) == MA_SUCCESS ? 0 : -2;
}

int ab_stop(void)
{
    if (!g_open) return -1;
    ma_device_stop(&g_device);
    return 0;
}

/* 重新开始播放前调用：清空环形缓冲、灌到预热水位、清零 xrun 统计。
   设备启动瞬间会一次抓走近 2 轮缓冲，所以这里按 PREFILL 灌，不按 target。 */
int ab_flush(void)
{
    if (!g_open || !g_ring) return -1;
    memset(g_ring, 0, (size_t)g_ring_frames * 2 * sizeof(float));
    uint64_t pre = (uint64_t)g_info.block_frames * AB_PREFILL_SLOTS;
    if (pre > g_ring_frames) pre = g_ring_frames;
    InterlockedExchange64(&g_rpos, 0);
    InterlockedExchange64(&g_wpos, (LONG64)pre);
    InterlockedExchange(&g_xruns, 0);
    return 0;
}

void ab_close(void)
{
    if (g_open) {
        ma_device_uninit(&g_device);
        g_open = 0;
    }
    if (g_ring) { free(g_ring); g_ring = NULL; g_ring_frames = 0; }
}

/* ---------------------------------------------------------------- 写入 */

int ab_write(const float *interleaved, int frames)
{
    if (!g_open || !g_ring || frames <= 0) return -1;
    if ((uint64_t)frames > g_ring_frames) return -2;

    const uint64_t target = g_target_frames ? g_target_frames : g_ring_frames;
    LONG64 w = 0, r = 0;
    uint64_t used = 0;
    int waited = 0;

    /* 水位控制：缓冲超过 target 就等设备吃掉一块（160ms 上限）。
       等不到也不丢块——只要没到物理容量就照写；丢块只发生在真溢出时。 */
    for (;;) {
        w = (LONG64)InterlockedCompareExchange64(&g_wpos, 0, 0);
        r = (LONG64)InterlockedCompareExchange64(&g_rpos, 0, 0);
        used = (uint64_t)(w - r);
        if (used + (uint64_t)frames <= target) break;
        if (waited >= 160) break;
        Sleep(1);
        waited++;
    }
    if (used + (uint64_t)frames > g_ring_frames) {
        InterlockedIncrement(&g_xruns);
        return 1;
    }
    uint64_t off = (uint64_t)w % g_ring_frames;
    uint64_t first = g_ring_frames - off;
    if (first > (uint64_t)frames) first = (uint64_t)frames;
    memcpy(g_ring + off * 2, interleaved, (size_t)first * 2 * sizeof(float));
    if ((uint64_t)frames > first)
        memcpy(g_ring, interleaved + first * 2, (size_t)(frames - first) * 2 * sizeof(float));
    InterlockedExchange64(&g_wpos, (LONG64)(w + frames));
    return 0;
}

int ab_set_target(int frames)
{
    if (frames <= 0) return -1;
    if (g_ring_frames && (uint64_t)frames > g_ring_frames) return -2;
    g_target_frames = (uint64_t)frames;
    return 0;
}

int ab_get_target(void) { return (int)g_target_frames; }

int ab_info(ab_info_t *out)
{
    if (!out) return -1;
    g_info.xruns = (int)g_xruns;
    *out = g_info;
    return 0;
}

/* ---------------------------------------------------------------- EQ */

int ab_eq_process(int slot, float *buf, int frames, int channels, const float *c)
{
    if (!buf || !c || channels != 2 || frames <= 0) return -1;
    if (slot < 0 || slot >= AB_EQ_SLOTS) return -1;
    ab_biquad_t *s = &g_eq[slot];
    const float b0 = c[0], b1 = c[1], b2 = c[2], a1 = c[3], a2 = c[4];
    for (int ch = 0; ch < 2; ch++) {
        float z1 = s->z1[ch], z2 = s->z2[ch];
        float *p = buf + ch;
        for (int i = 0; i < frames; i++) {
            float x = p[i * 2];
            float y = b0 * x + z1;
            z1 = b1 * x - a1 * y + z2;
            z2 = b2 * x - a2 * y;
            p[i * 2] = y;
        }
        s->z1[ch] = z1; s->z2[ch] = z2;
    }
    return 0;
}

void ab_eq_reset(int slot)
{
    if (slot < 0 || slot >= AB_EQ_SLOTS) {
        memset(g_eq, 0, sizeof(g_eq));
        return;
    }
    memset(&g_eq[slot], 0, sizeof(ab_biquad_t));
}
