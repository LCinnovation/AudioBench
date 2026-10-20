"""AudioBench · R4 POC —— NCM 容器 encode→decode 往返验证（自造 fixture）

目的（对应任务书 §9 R4）：
  1. 在没有真实 .ncm 样本的前提下，用「正向打包」生成结构合法的 fixture；
  2. 断言 decode(encode(x)) == x（音频载荷逐字节一致）；
  3. 断言元数据 / 封面 / 格式嗅探 / 异常路径全部符合预期。

实现依据（公开格式描述 + ncmdump 的算法流程，MIT，只读算法不抄代码）：
  - magic "CTENFDAM"（两个小端 u32: 0x4E455443, 0x4D414446），后跟 2 字节版本
  - u32 key_len → key_len 字节 XOR 0x64 → AES-128-ECB(CORE_KEY)
    → 明文前 17 字节为 "neteasecloudmusic"，其余为 keybox 的 RC4 key
  - u32 meta_len → meta_len 字节 XOR 0x63 → 去前缀 "163 key(Don't modify):"
    → base64 decode → AES-128-ECB(MODIFY_KEY) → 去前缀 "music:" → JSON
  - 4B crc32 + 1B 版本 + u32 cover_frame_len + u32 cover_len + cover + 16 字节对齐 pad
  - 音频载荷逐字节 XOR keybox 派生流（周期 256，故与 0x8000 分块无关）

注意（必须写进交付说明）：往返一致只证明「编解码器自洽」，不能证明「与网易真实文件兼容」。
真实兼容性由 §7-10 的手工验证清单兜底，本 POC 用「结构 + 常量断言」弥补：
magic、两个前缀、AES 对齐、keybox 变体都被写死断言。

用法：
    python ncm_roundtrip.py                        # 跑全部用例
    python ncm_roundtrip.py decode x.ncm outdir/   # 解真实文件（M1 手工验证用）
    python ncm_roundtrip.py sample out.ncm         # 生成可用于手工验证的 fixture
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import struct
import sys
import zlib

import numpy as np
from Crypto.Cipher import AES

MAGIC = b"CTENFDAM"
VERSION = b"\x01\x70"
CORE_KEY = bytes.fromhex("687A4852416D736F356B496E62617857")       # "hzHRAmso5kInbaxW"
MODIFY_KEY = bytes.fromhex("2331346C6A6B5F215C5D2630553C2728")
META_PREFIX = b"163 key(Don't modify):"                            # 22 bytes
MUSIC_PREFIX = b"music:"                                           # 6 bytes
KEY_PLAIN_PREFIX = b"neteasecloudmusic"                            # 17 bytes
STREAM_BLOCK = 0x8000                                              # ncmdump 的分块大小


class NcmFormatError(Exception):
    """用户可见错误：必须同时给出文件名与原因（§3-P0-1）。"""


# ---------------------------------------------------------------- primitives

def _u32(n: int) -> bytes:
    return struct.pack("<I", n)


def _read_u32(buf: bytes, off: int) -> int:
    if off + 4 > len(buf):
        raise NcmFormatError(f"文件在偏移 {off} 处意外结束（应读 4 字节长度字段）")
    return struct.unpack_from("<I", buf, off)[0]


def _pkcs7(data: bytes) -> bytes:
    pad = 16 - (len(data) % 16)
    return data + bytes([pad]) * pad


def _aes_encrypt(key: bytes, data: bytes) -> bytes:
    return AES.new(key, AES.MODE_ECB).encrypt(_pkcs7(data))


def _aes_decrypt(key: bytes, data: bytes) -> bytes:
    if not data or len(data) % 16:
        raise NcmFormatError(f"AES 密文长度非法：{len(data)}（必须是 16 的正整数倍）")
    out = AES.new(key, AES.MODE_ECB).decrypt(data)
    pad = out[-1]
    if pad > 16:
        pad = 0
    return out[: len(out) - pad]


def build_key_box(key: bytes) -> bytes:
    """ncmdump 的 keybox 变体（不是标准 RC4 KSA：每轮额外累加上一轮结果）。"""
    if not key:
        raise NcmFormatError("keybox key 为空")
    box = list(range(256))
    last = 0
    off = 0
    for i in range(256):
        swap = box[i]
        c = (swap + last + key[off]) & 0xFF
        off += 1
        if off >= len(key):
            off = 0
        box[i] = box[c]
        box[c] = swap
        last = c
    return bytes(box)


def keystream(box: bytes) -> bytes:
    """周期 256 的 XOR 流；STREAM_BLOCK 是 256 的整数倍，因此分块处理与整块等价。"""
    ks = bytearray(256)
    for i in range(256):
        j = (i + 1) & 0xFF
        ks[i] = box[(box[j] + box[(box[j] + j) & 0xFF]) & 0xFF]
    return bytes(ks)


def _xor_stream(payload: bytes, ks: bytes) -> bytes:
    if not payload:
        return b""
    n = len(ks)
    stream = (ks * ((len(payload) + n - 1) // n))[: len(payload)]
    arr = np.frombuffer(payload, dtype=np.uint8)
    return (arr ^ np.frombuffer(stream, dtype=np.uint8)).tobytes()


def _xor_bytes(data: bytes, mask: int) -> bytes:
    return (np.frombuffer(data, dtype=np.uint8) ^ np.uint8(mask)).tobytes()


def sniff_container(payload: bytes) -> str:
    if payload[:3] == b"ID3" or payload[:2] == b"\xff\xfb":
        return "mp3"
    if payload[:4] == b"fLaC":
        return "flac"
    return "unknown"


# ---------------------------------------------------------------- codec

def encode_ncm(payload: bytes,
               *,
               rc4_key: bytes | None = None,
               metadata: dict | None = None,
               cover: bytes | None = None) -> bytes:
    """把一段 mp3/flac 字节打包成 .ncm（仅测试/fixture；产品内不存在加密功能）。"""
    if not payload:
        raise ValueError("payload 为空")
    rc4_key = rc4_key or os.urandom(64)

    key_blob = _xor_bytes(_aes_encrypt(CORE_KEY, KEY_PLAIN_PREFIX + rc4_key), 0x64)

    out = bytearray(MAGIC + VERSION)
    out += _u32(len(key_blob)) + key_blob

    if metadata is not None:
        raw = json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        meta_blob = META_PREFIX + base64.b64encode(_aes_encrypt(MODIFY_KEY, MUSIC_PREFIX + raw))
        meta_blob = _xor_bytes(meta_blob, 0x63)
        out += _u32(len(meta_blob)) + meta_blob
    else:
        out += _u32(0)

    out += _u32(zlib.crc32(cover) & 0xFFFFFFFF if cover else 0)
    out += b"\x01"
    if cover:
        pad = (16 - len(cover) % 16) % 16
        out += _u32(len(cover) + pad) + _u32(len(cover)) + cover + b"\x00" * pad
    else:
        out += _u32(0) + _u32(0)

    out += _xor_stream(payload, keystream(build_key_box(rc4_key)))
    return bytes(out)


def decode_ncm(blob: bytes, *, name: str = "<memory>") -> dict:
    """返回 {payload, metadata, cover, container, crc_ok}；失败抛 NcmFormatError。"""
    try:
        return _decode(blob)
    except NcmFormatError as e:
        raise NcmFormatError(f"{name}: {e}") from None
    except Exception as e:  # 任何解析异常都必须变成可读错误，不允许冒泡成崩溃
        raise NcmFormatError(f"{name}: 解析失败（{type(e).__name__}: {e}）") from None


def _decode(blob: bytes) -> dict:
    if len(blob) < 10:
        raise NcmFormatError("文件太小，不是合法的 ncm")
    if blob[:8] != MAGIC:
        raise NcmFormatError(f"magic 不匹配：期望 {MAGIC!r}，实际 {blob[:8]!r}")

    off = 10  # magic(8) + version(2)

    key_len = _read_u32(blob, off); off += 4
    if key_len <= 0 or key_len % 16 or off + key_len > len(blob):
        raise NcmFormatError(f"key 段长度非法：{key_len}")
    key_plain = _aes_decrypt(CORE_KEY, _xor_bytes(blob[off:off + key_len], 0x64))
    off += key_len
    if not key_plain.startswith(KEY_PLAIN_PREFIX):
        raise NcmFormatError("key 段解密后缺少 neteasecloudmusic 前缀（core key 不匹配）")
    box = build_key_box(key_plain[len(KEY_PLAIN_PREFIX):])

    meta_len = _read_u32(blob, off); off += 4
    metadata = None
    if meta_len:
        if off + meta_len > len(blob):
            raise NcmFormatError("meta 段越界")
        meta = _xor_bytes(blob[off:off + meta_len], 0x63)
        off += meta_len
        if not meta.startswith(META_PREFIX):
            raise NcmFormatError("meta 段缺少 163 key(Don't modify): 前缀")
        raw = _aes_decrypt(MODIFY_KEY, base64.b64decode(meta[len(META_PREFIX):]))
        if not raw.startswith(MUSIC_PREFIX):
            raise NcmFormatError("meta 解密后缺少 music: 前缀（modify key 不匹配）")
        metadata = json.loads(raw[len(MUSIC_PREFIX):].decode("utf-8"))

    crc = _read_u32(blob, off); off += 4
    off += 1  # image version
    cover_frame_len = _read_u32(blob, off); off += 4
    cover_len = _read_u32(blob, off); off += 4
    cover = b""
    if cover_len:
        if cover_len > cover_frame_len or off + cover_len > len(blob):
            raise NcmFormatError("封面段长度非法")
        cover = blob[off:off + cover_len]
        off += cover_len
    if cover_frame_len > cover_len:
        off += cover_frame_len - cover_len
    if off > len(blob):
        raise NcmFormatError("封面段越界")

    payload = _xor_stream(blob[off:], keystream(box))
    if not payload:
        raise NcmFormatError("音频段为空（文件在头部之后被截断）")
    crc_actual = zlib.crc32(cover) & 0xFFFFFFFF if cover else 0
    return {
        "payload": payload,
        "metadata": metadata,
        "cover": cover,
        "container": sniff_container(payload),
        "crc_ok": crc_actual == crc,
        "audio_offset": off,
    }


# ---------------------------------------------------------------- fixtures

def make_flac(seconds: float = 1.0, sr: int = 44100) -> bytes:
    import soundfile as sf
    t = np.arange(int(sr * seconds), dtype=np.float32) / sr
    left = 0.4 * np.sin(2 * np.pi * 440 * t)
    right = 0.4 * np.sin(2 * np.pi * 554.37 * t)
    buf = io.BytesIO()
    sf.write(buf, np.stack([left, right], axis=1), sr, format="FLAC", subtype="PCM_16")
    return buf.getvalue()


def make_mp3(seconds: float = 1.0, sr: int = 44100) -> bytes | None:
    import soundfile as sf
    t = np.arange(int(sr * seconds), dtype=np.float32) / sr
    buf = io.BytesIO()
    try:
        sf.write(buf, 0.4 * np.sin(2 * np.pi * 330 * t), sr, format="MP3", subtype="MPEG_LAYER_III")
    except Exception:
        return None
    return buf.getvalue()


def make_cover(size: int = 5000) -> bytes:
    """构造一个真 PNG（1x1 图 + 随机 tEXt 垫到指定大小），不引入 Pillow。"""
    def u32be(n: int) -> bytes:
        return struct.pack(">I", n)

    def chunk(tag: bytes, data: bytes) -> bytes:
        # PNG 的长度与 CRC 都是大端（这里曾经写成小端，ffmpeg 会报 chunk too big）
        return u32be(len(data)) + tag + data + u32be(zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\x00\x00\x00")
    pad = chunk(b"tEXt", b"pad\x00" + os.urandom(max(0, size - 100)))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + pad + chunk(b"IEND", b"")


META = {
    "musicId": 1234567,
    "musicName": "夏日的旧磁带",
    "artist": [["测试歌手", 1001], ["第二歌手", 1002]],
    "album": "Sample Album",
    "albumId": 999,
    "bitrate": 320000,
    "duration": 1000,
    "format": "flac",
}


# ---------------------------------------------------------------- tests

def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def case_structure() -> dict:
    """常量/结构断言：往返自洽之外，还必须证明结构与公开格式一致。"""
    blob = encode_ncm(make_flac(0.1), metadata=META, cover=make_cover())
    _assert(blob[:8] == MAGIC, "magic 不对")
    _assert(blob[8:10] == VERSION, "版本字段不对")
    key_len = struct.unpack_from("<I", blob, 10)[0]
    _assert(key_len % 16 == 0, "key 密文未按 16 字节对齐")
    meta_len = struct.unpack_from("<I", blob, 14 + key_len)[0]
    meta_raw = _xor_bytes(blob[18 + key_len:18 + key_len + meta_len], 0x63)
    _assert(meta_raw.startswith(META_PREFIX), "meta 前缀不是 163 key(Don't modify):")
    _assert((len(META_PREFIX), len(MUSIC_PREFIX), len(KEY_PLAIN_PREFIX)) == (22, 6, 17),
            "前缀长度常量错")
    ks = keystream(build_key_box(b"abc"))
    _assert(len(ks) == 256 and ks != bytes(range(256)), "keystream 未正确生成")
    _assert(_xor_stream(b"abcdef", b"\x01\x02") == b"``bfdd", "XOR 流实现有误")
    return {"case": "结构/常量断言（magic、前缀、对齐、keystream）"}


def case_minimal() -> dict:
    payload = make_flac(0.25)
    blob = encode_ncm(payload)  # 无元数据、无封面
    got = decode_ncm(blob, name="fixture_minimal.ncm")
    _assert(got["payload"] == payload, "最小用例载荷往返不一致")
    _assert(got["metadata"] is None, "最小用例不应有元数据")
    _assert(got["cover"] == b"", "最小用例不应有封面")
    return {"case": "无元数据 + 无封面", "ncm_bytes": len(blob)}


def case_flac_full() -> dict:
    payload = make_flac()
    cover = make_cover()
    blob = encode_ncm(payload, metadata=META, cover=cover)
    got = decode_ncm(blob, name="fixture_full.ncm")
    _assert(got["payload"] == payload, "FLAC 载荷往返不一致")
    _assert(got["metadata"] == META, f"元数据不一致: {got['metadata']}")
    _assert(got["cover"] == cover, "封面不一致")
    _assert(got["crc_ok"], "CRC 校验失败")
    _assert(got["container"] == "flac", f"格式嗅探错误: {got['container']}")
    return {"case": "flac + 中文元数据 + PNG 封面",
            "ncm_bytes": len(blob),
            "payload_sha256": hashlib.sha256(payload).hexdigest()[:16]}


def case_mp3_full() -> dict | None:
    payload = make_mp3()
    if payload is None:
        print("SKIP mp3 用例：当前 libsndfile 不支持写 MP3")
        return None
    blob = encode_ncm(payload, metadata={**META, "format": "mp3"}, cover=make_cover())
    got = decode_ncm(blob, name="fixture_mp3.ncm")
    _assert(got["payload"] == payload, "MP3 载荷往返不一致")
    _assert(got["container"] == "mp3", f"格式嗅探错误: {got['container']}")
    _assert(got["crc_ok"], "MP3 用例 CRC 校验失败")
    return {"case": "mp3 + 元数据 + 封面", "ncm_bytes": len(blob),
            "payload_sha256": hashlib.sha256(payload).hexdigest()[:16]}


def case_large_payload() -> dict:
    """跨多个 0x8000 分块（验证 keystream 相位与分块无关）。"""
    payload = make_flac(3.0)
    _assert(len(payload) > 3 * STREAM_BLOCK, "样本太小，无法覆盖分块场景")
    blob = encode_ncm(payload, metadata=META)
    got = decode_ncm(blob, name="fixture_large.ncm")
    _assert(got["payload"] == payload, f"分块用例载荷不一致（{len(payload)} 字节）")
    return {"case": "跨 0x8000 分块（>3 块）", "payload_bytes": len(payload)}


def case_truncated() -> dict:
    """头部区截断必须报错；音频区截断允许（无长度字段，ncmdump 亦不校验），
    但空音频必须报错——产品层还需用时长/尾部 CRC 兜底，见交付说明。"""
    full = encode_ncm(make_flac(0.25), metadata=META, cover=make_cover())
    head_end = decode_ncm(full, name="x.ncm")["audio_offset"]
    for cut in (4, 9, 20, head_end - 1, head_end):
        try:
            decode_ncm(full[:cut], name=f"trunc_{cut}.ncm")
        except NcmFormatError as e:
            _assert(f"trunc_{cut}" in str(e), "错误信息缺少文件名")
        else:
            raise AssertionError(f"头部区截断到 {cut} 字节仍被接受")
    mid = decode_ncm(full[: head_end + 500], name="trunc_audio.ncm")
    _assert(len(mid["payload"]) == 500, "音频区截断后载荷长度不对")
    return {"case": "头部区截断报错 / 音频区截断可解（需产品层兜底）",
            "header_bytes": head_end, "variants": 6}


def case_bad_magic() -> dict:
    try:
        decode_ncm(b"NOTANCMFILE" + b"\x00" * 32, name="bad.ncm")
    except NcmFormatError as e:
        _assert("magic" in str(e), "magic 错误信息不明确")
    else:
        raise AssertionError("错误 magic 未被拒绝")
    return {"case": "错误 magic 被拒绝"}


def run_all() -> int:
    cases = [case_structure, case_minimal, case_flac_full, case_mp3_full,
             case_large_payload, case_truncated, case_bad_magic]
    rows = []
    for fn in cases:
        try:
            rows.append(fn())
        except AssertionError as e:
            print(f"FAIL {fn.__name__}: {e}")
            return 1
    for r in rows:
        if r:
            print("PASS " + json.dumps(r, ensure_ascii=False))
    print(f"\n{len([r for r in rows if r])} cases passed")
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        return run_all()
    cmd = argv[0]
    if cmd == "decode" and len(argv) >= 3:
        with open(argv[1], "rb") as f:
            blob = f.read()
        got = decode_ncm(blob, name=os.path.basename(argv[1]))
        os.makedirs(argv[2], exist_ok=True)
        stem = os.path.splitext(os.path.basename(argv[1]))[0]
        out = os.path.join(argv[2], f"{stem}.{got['container']}")
        with open(out, "wb") as f:
            f.write(got["payload"])
        if got["cover"]:
            with open(os.path.join(argv[2], f"{stem}.cover"), "wb") as f:
                f.write(got["cover"])
        print(json.dumps({k: v for k, v in got.items() if k not in ("payload", "cover")},
                         ensure_ascii=False, indent=2))
        print(f"写出 {out} ({len(got['payload'])} bytes)")
        return 0
    if cmd == "sample" and len(argv) >= 2:
        payload = make_flac(2.0)
        blob = encode_ncm(payload, metadata=META, cover=make_cover())
        with open(argv[1], "wb") as f:
            f.write(blob)
        print(f"写出 {argv[1]} ({len(blob)} bytes), 期望容器 {sniff_container(payload)}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
