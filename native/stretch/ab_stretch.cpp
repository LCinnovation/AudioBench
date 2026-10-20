/*
 * ab_stretch.dll — Signalsmith Stretch 的 C ABI 包装（MIT）
 *
 * 上游是 header-only C++；本文件只做三件事：
 *   1. 把交错 f32 拆成 planars 交给 SignalsmithStretch；
 *   2. 处理 reset 后的预热（丢掉 outputLatency 个样本）；
 *   3. 给出 inputLatency/outputLatency，便于调用方做对齐。
 *
 * 时间伸缩是「输入/输出样本数之比」，所以接口是 process(in_frames, out_frames)。
 */
#include "signalsmith-stretch.h"

#include <cmath>
#include <cstring>
#include <vector>

struct AbStretch {
    signalsmith::stretch::SignalsmithStretch<float> st;
    int channels = 2;
    int max_frames = 0;
    std::vector<float> in_buf;      // channels * max_frames
    std::vector<float> out_buf;     // channels * max_frames
    std::vector<float*> in_ptrs;
    std::vector<float*> out_ptrs;
};

static void make_ptrs(AbStretch* s, int frames, std::vector<float*>& ptrs, std::vector<float>& buf)
{
    ptrs.resize((size_t)s->channels);
    for (int c = 0; c < s->channels; ++c)
        ptrs[(size_t)c] = buf.data() + (size_t)c * (size_t)frames;
}

extern "C" {

__declspec(dllexport) void* ab_stretch_create(int channels, int sample_rate, int max_block)
{
    if (channels < 1 || channels > 2) channels = 2;
    if (sample_rate < 8000) sample_rate = 48000;
    if (max_block < 64) max_block = 1024;
    AbStretch* s = new AbStretch();
    s->channels = channels;
    s->max_frames = max_block * 4 + 4096;
    s->in_buf.assign((size_t)channels * s->max_frames, 0.0f);
    s->out_buf.assign((size_t)channels * s->max_frames, 0.0f);
    s->st.presetDefault(channels, (float)sample_rate);
    s->st.reset();
    return s;
}

__declspec(dllexport) void ab_stretch_destroy(void* h)
{
    delete static_cast<AbStretch*>(h);
}

__declspec(dllexport) void ab_stretch_set_pitch(void* h, float semitones)
{
    AbStretch* s = static_cast<AbStretch*>(h);
    if (!s) return;
    if (semitones < -24.0f) semitones = -24.0f;
    if (semitones > 24.0f) semitones = 24.0f;
    s->st.setTransposeSemitones(semitones);
}

__declspec(dllexport) int ab_stretch_input_latency(void* h)
{
    AbStretch* s = static_cast<AbStretch*>(h);
    return s ? s->st.inputLatency() : 0;
}

__declspec(dllexport) int ab_stretch_output_latency(void* h)
{
    AbStretch* s = static_cast<AbStretch*>(h);
    return s ? s->st.outputLatency() : 0;
}

/* 重置并预热：喂 inputLatency 个零、丢掉 outputLatency 个输出，之后 process 即对齐 */
__declspec(dllexport) void ab_stretch_reset(void* h)
{
    AbStretch* s = static_cast<AbStretch*>(h);
    if (!s) return;
    s->st.reset();
    const int lat_in = s->st.inputLatency();
    const int lat_out = s->st.outputLatency();
    if (lat_in <= 0 || lat_out <= 0) return;
    const int n = lat_in + lat_out;
    if (n > s->max_frames) return;
    std::fill(s->in_buf.begin(), s->in_buf.end(), 0.0f);
    make_ptrs(s, n, s->in_ptrs, s->in_buf);
    make_ptrs(s, n, s->out_ptrs, s->out_buf);
    s->st.process((float**)s->in_ptrs.data(), lat_in, (float**)s->out_ptrs.data(), lat_out);
}

/* 返回 0 成功；越界返回 -1。in/out 都是交错立体声。 */
__declspec(dllexport) int ab_stretch_process(void* h,
                                             const float* in, int in_frames,
                                             float* out, int out_frames)
{
    AbStretch* s = static_cast<AbStretch*>(h);
    if (!s || !in || !out || in_frames <= 0 || out_frames <= 0) return -1;
    const int need = (in_frames > out_frames ? in_frames : out_frames);
    if (need > s->max_frames) return -2;

    make_ptrs(s, in_frames, s->in_ptrs, s->in_buf);
    make_ptrs(s, out_frames, s->out_ptrs, s->out_buf);

    for (int c = 0; c < s->channels; ++c) {
        float* dst = s->in_ptrs[(size_t)c];
        const float* src = in + c;
        for (int i = 0; i < in_frames; ++i)
            dst[i] = src[(size_t)i * (size_t)s->channels];
    }
    s->st.process((float**)s->in_ptrs.data(), in_frames, (float**)s->out_ptrs.data(), out_frames);
    for (int c = 0; c < s->channels; ++c) {
        const float* src = s->out_ptrs[(size_t)c];
        float* dst = out + c;
        for (int i = 0; i < out_frames; ++i)
            dst[(size_t)i * (size_t)s->channels] = src[i];
    }
    return 0;
}

__declspec(dllexport) const char* ab_stretch_version(void) { return "ab_stretch/1.0 (signalsmith)"; }

}  // extern "C"
