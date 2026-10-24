"""NCM 容器解码（格式转换用；不含任何下载/破解逻辑，§8 合规红线）。

算法来自公开格式描述与 ncmdump 的流程（MIT，仅读算法不抄代码）：
  magic "CTENFDAM" → 2B 版本 → u32 key_len → XOR 0x64 → AES-128-ECB(core key)
  → 前 17B 为 "neteasecloudmusic"，其余是 keybox 的 RC4 key
  → u32 meta_len → XOR 0x63 → 去 22B 前缀 → base64 → AES-128-ECB(modify key)
  → 去 6B "music:" → JSON
  → crc32(4) + 版本(1) + u32 frame_len + u32 cover_len + cover + pad
  → 音频载荷逐字节 XOR keybox 派生流（周期 256）
"""
from __future__ import annotations

import base64
import json
import logging
import struct
import zlib

import numpy as np
from Crypto.Cipher import AES

log = logging.getLogger("ab.ncm")

MAGIC = b"CTENFDAM"
CORE_KEY = bytes.fromhex("687A4852416D736F356B496E62617857")       # "hzHRAmso5kInbaxW"
MODIFY_KEY = bytes.fromhex("2331346C6A6B5F215C5D2630553C2728")
META_PREFIX = b"163 key(Don't modify):"
MUSIC_PREFIX = b"music:"
KEY_PLAIN_PREFIX = b"neteasecloudmusic"


class NcmFormatError(Exception):
    """用户可见错误：带文件名与原因。"""


def _u32(buf: bytes, off: int) -> int:
    if off + 4 > len(buf):
        raise NcmFormatError(f"文件在偏移 {off} 处意外结束")
    return struct.unpack_from("<I", buf, off)[0]


def _xor(data: bytes, mask: int) -> bytes:
    if not data:
        return b""
    return (np.frombuffer(data, dtype=np.uint8) ^ np.uint8(mask)).tobytes()


def _aes_decrypt(key: bytes, data: bytes) -> bytes:
    if not data or len(data) % 16:
        raise NcmFormatError(f"AES 密文长度非法：{len(data)}")
    out = AES.new(key, AES.MODE_ECB).decrypt(data)
    pad = out[-1]
    if pad > 16:
        pad = 0
    return out[: len(out) - pad]


def build_key_box(key: bytes) -> bytes:
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
    return (np.frombuffer(payload, dtype=np.uint8)
            ^ np.frombuffer(stream, dtype=np.uint8)).tobytes()


def sniff_container(payload: bytes) -> str:
    if payload[:3] == b"ID3" or payload[:2] == b"\xff\xfb":
        return "mp3"
    if payload[:4] == b"fLaC":
        return "flac"
    return "unknown"


def decode_ncm(blob: bytes, *, name: str = "<memory>") -> dict:
    """返回 {payload, metadata, cover, container, crc_ok, audio_offset}。"""
    try:
        return _decode(blob)
    except NcmFormatError as e:
        raise NcmFormatError(f"{name}: {e}") from None
    except Exception as e:
        raise NcmFormatError(f"{name}: 解析失败（{type(e).__name__}: {e}）") from None


def _decode(blob: bytes) -> dict:
    if len(blob) < 10:
        raise NcmFormatError("文件太小，不是合法的 ncm")
    if blob[:8] != MAGIC:
        raise NcmFormatError("magic 不匹配（不是 ncm 文件）")

    off = 10
    key_len = _u32(blob, off)
    off += 4
    if key_len <= 0 or key_len % 16 or off + key_len > len(blob):
        raise NcmFormatError(f"key 段长度非法：{key_len}")
    key_plain = _aes_decrypt(CORE_KEY, _xor(blob[off:off + key_len], 0x64))
    off += key_len
    if not key_plain.startswith(KEY_PLAIN_PREFIX):
        raise NcmFormatError("key 段解密后缺少 neteasecloudmusic 前缀")
    box = build_key_box(key_plain[len(KEY_PLAIN_PREFIX):])

    meta_len = _u32(blob, off)
    off += 4
    metadata = None
    if meta_len:
        if off + meta_len > len(blob):
            raise NcmFormatError("meta 段越界")
        meta = _xor(blob[off:off + meta_len], 0x63)
        off += meta_len
        if not meta.startswith(META_PREFIX):
            raise NcmFormatError("meta 段缺少 163 key 前缀")
        raw = _aes_decrypt(MODIFY_KEY, base64.b64decode(meta[len(META_PREFIX):]))
        if not raw.startswith(MUSIC_PREFIX):
            raise NcmFormatError("meta 解密后缺少 music: 前缀")
        metadata = json.loads(raw[len(MUSIC_PREFIX):].decode("utf-8"))

    crc = _u32(blob, off)
    off += 4
    off += 1
    cover_frame_len = _u32(blob, off)
    off += 4
    cover_len = _u32(blob, off)
    off += 4
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
        raise NcmFormatError("音频段为空（文件被截断）")
    crc_actual = zlib.crc32(cover) & 0xFFFFFFFF if cover else 0
    return {
        "payload": payload,
        "metadata": metadata,
        "cover": cover,
        "container": sniff_container(payload),
        "crc_ok": crc_actual == crc,
        "audio_offset": off,
    }


def read_file(path) -> dict:
    with open(path, "rb") as f:
        return decode_ncm(f.read(), name=str(path))


def flac_streaminfo(payload: bytes) -> dict | None:
    """读 FLAC 载荷的 STREAMINFO（不解码）。非 FLAC 或头部不完整时返回 None。

    高解析音源（96 kHz / 24 bit）很常见，转成 mp3 后采样率会变，
    所以调用方必须用**时长**比较，不能直接比样本数。
    """
    if not payload.startswith(b"fLaC") or len(payload) < 4 + 4 + 34:
        return None
    if payload[4] != 0x00:                 # 第一个 metadata block 必须是 STREAMINFO
        return None
    si = payload[8:8 + 34]
    b = si[10:18]
    rate = (b[0] << 12) | (b[1] << 4) | (b[2] >> 4)
    channels = ((b[2] >> 1) & 0x07) + 1
    bits = (((b[2] & 1) << 4) | (b[3] >> 4)) + 1
    total = ((b[3] & 0x0F) << 32) | (b[4] << 24) | (b[5] << 16) | (b[6] << 8) | b[7]
    return {"sample_rate": int(rate), "channels": channels, "bits": bits,
            "total_samples": int(total)}


def expected_samples(payload: bytes) -> int | None:
    """FLAC 载荷声明的样本数（不解码）；用于发现载荷被截断。"""
    info = flac_streaminfo(payload)
    return (info or {}).get("total_samples") or None


def friendly_meta(metadata: dict | None) -> dict:
    """把网易的 JSON 元数据映射成 ffmpeg 的 -metadata 键。"""
    if not metadata:
        return {}
    artists = []
    for a in metadata.get("artist") or []:
        if isinstance(a, (list, tuple)) and a:
            artists.append(str(a[0]))
        elif isinstance(a, str):
            artists.append(a)
    return {
        "title": metadata.get("musicName") or "",
        "artist": "/".join(artists),
        "album": metadata.get("album") or "",
        "comment": "Converted by AudioBench (local format conversion only)",
    }
