"""QQ 音乐 QMC 解密算法（纯计算，不做文件 IO）。

算法来源
--------
* 移植自 unlock-music  ``src/decrypt/qmc_cipher.ts`` / ``qmc_key.ts``
  MIT License, Copyright (c) 2019-2023 MengYX
* TEA 实现移植自 ``src/utils/tea.ts``，其源头是 golang.org/x/crypto/tea
  BSD-3-Clause, Copyright 2015 The Go Authors

三种密码
--------
* ``QmcStaticCipher``  QMCv1，固定表，密钥不存在文件里；
* ``QmcMapCipher``     QMCv2 短密钥（≤300 字节）；
* ``QmcRC4Cipher``     QMCv2 长密钥（>300 字节，分段 RC4）。
"""

from __future__ import annotations

import base64
import struct

MASK32 = 0xFFFFFFFF
BLOCK_SIZE = 8
SALT_LEN = 2
ZERO_LEN = 7


# --------------------------------------------------------------------------- #
# TEA
# --------------------------------------------------------------------------- #


class TeaCipher:
    """腾讯魔改版 TEA（16 字节密钥，默认 32 轮）。"""

    DELTA = 0x9E3779B9

    def __init__(self, key: bytes, rounds: int = 64) -> None:
        if len(key) != 16:
            raise ValueError("TEA 密钥必须是 16 字节")
        if rounds & 1:
            raise ValueError("TEA 轮数必须是偶数")
        self.k0, self.k1, self.k2, self.k3 = struct.unpack(">IIII", key)
        self.rounds = rounds

    def encrypt_block(self, block: bytes) -> bytes:
        v0, v1 = struct.unpack(">II", block)
        total = 0
        for _ in range(self.rounds // 2):
            total = (total + self.DELTA) & MASK32
            v0 = (v0 + ((((v1 << 4) + self.k0) ^ (v1 + total) ^ ((v1 >> 5) + self.k1)) & MASK32)) & MASK32
            v1 = (v1 + ((((v0 << 4) + self.k2) ^ (v0 + total) ^ ((v0 >> 5) + self.k3)) & MASK32)) & MASK32
        return struct.pack(">II", v0, v1)

    def decrypt_block(self, block: bytes) -> bytes:
        v0, v1 = struct.unpack(">II", block)
        total = (self.DELTA * self.rounds) // 2
        for _ in range(self.rounds // 2):
            v1 = (v1 - ((((v0 << 4) + self.k2) ^ (v0 + total) ^ ((v0 >> 5) + self.k3)) & MASK32)) & MASK32
            v0 = (v0 - ((((v1 << 4) + self.k0) ^ (v1 + total) ^ ((v1 >> 5) + self.k1)) & MASK32)) & MASK32
            total = (total - self.DELTA) & MASK32
        return struct.pack(">II", v0, v1)


# --------------------------------------------------------------------------- #
# 密钥派生
# --------------------------------------------------------------------------- #

_MIX_KEY1 = bytes([
    0x33, 0x38, 0x36, 0x5A, 0x4A, 0x59, 0x21, 0x40,
    0x23, 0x2A, 0x24, 0x25, 0x5E, 0x26, 0x29, 0x28,
])
_MIX_KEY2 = bytes([
    0x2A, 0x2A, 0x23, 0x21, 0x28, 0x23, 0x24, 0x25,
    0x26, 0x5E, 0x61, 0x31, 0x63, 0x5A, 0x2C, 0x54,
])
_ENC_V2_PREFIX = b"QQMusic EncV2,Key:"

_B64_BYTES = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
)


def _b64_tolerant(raw: bytes) -> bytes:
    """容忍非法字符的 base64 解码（Node 的 Buffer.from(x,'base64') 也这么干）。"""
    cleaned = bytes(b for b in raw if b in _B64_BYTES)
    cleaned = cleaned.rstrip(b"=")
    cleaned += b"=" * ((-len(cleaned)) % 4)
    try:
        return base64.b64decode(cleaned, validate=False)
    except Exception as exc:
        raise ValueError(f"base64 解码失败：{exc}") from exc


def simple_make_key(salt: int, length: int) -> bytes:
    """用 tan() 造 8 字节盐（腾讯的老把戏）。"""
    import math

    out = bytearray(length)
    for i in range(length):
        value = math.tan(salt + i * 0.1)
        out[i] = int(abs(value) * 100.0) & 0xFF
    return bytes(out)


def decrypt_tencent_tea(in_buf: bytes, key: bytes) -> bytes:
    """腾讯 TEA-CBC 解密：PadLen(1) + Padding(0-7) + Salt(2) + Body + Zero(7)。"""
    if len(in_buf) % BLOCK_SIZE != 0:
        raise ValueError("密文长度不是 8 的倍数")
    if len(in_buf) < 16:
        raise ValueError("密文太短")

    tea = TeaCipher(key, 32)
    tmp = bytearray(tea.decrypt_block(in_buf[:BLOCK_SIZE]))

    pad_len = tmp[0] & 0x07
    out_len = len(in_buf) - 1 - pad_len - SALT_LEN - ZERO_LEN
    if out_len <= 0:
        raise ValueError("密钥派生失败：输出长度非法")

    out = bytearray(out_len)
    iv_prev = bytes(BLOCK_SIZE)
    iv_cur = in_buf[:BLOCK_SIZE]
    in_pos = BLOCK_SIZE
    tmp_idx = 1 + pad_len

    def crypt_block() -> None:
        nonlocal iv_prev, iv_cur, in_pos, tmp_idx
        iv_prev = iv_cur
        iv_cur = in_buf[in_pos:in_pos + BLOCK_SIZE]
        for j in range(BLOCK_SIZE):
            tmp[j] ^= iv_cur[j]
        tmp[:] = tea.decrypt_block(bytes(tmp))
        in_pos += BLOCK_SIZE
        tmp_idx = 0

    skipped = 0
    while skipped < SALT_LEN:
        if tmp_idx < BLOCK_SIZE:
            tmp_idx += 1
            skipped += 1
        else:
            crypt_block()

    out_pos = 0
    while out_pos < out_len:
        if tmp_idx < BLOCK_SIZE:
            out[out_pos] = tmp[tmp_idx] ^ iv_prev[tmp_idx]
            out_pos += 1
            tmp_idx += 1
        else:
            crypt_block()

    # 原实现的 Zero 校验有个小 bug（索引不前进），这里忠实保留：
    # 效果等同于只校验一个字节，仍是有效的密钥正确性信号。
    if tmp[tmp_idx] != iv_prev[tmp_idx]:
        raise ValueError("zero check failed：密钥不正确")
    return bytes(out)


def decrypt_v2_key(key: bytes) -> bytes:
    """处理 'QQMusic EncV2,Key:' 包裹的新版密钥。"""
    if len(key) < 18 or key[:18] != _ENC_V2_PREFIX:
        return key
    out = decrypt_tencent_tea(key[18:], _MIX_KEY1)
    out = decrypt_tencent_tea(out, _MIX_KEY2)
    decoded = _b64_tolerant(out)
    if len(decoded) < 16:
        raise ValueError("EncV2 密钥解码失败")
    return decoded


def derive_key(raw: bytes) -> bytes:
    """把文件里带的原始 ekey 解成真正的流密钥。"""
    raw_dec = _b64_tolerant(raw)
    if len(raw_dec) < 16:
        raise ValueError("key length is too short")
    raw_dec = decrypt_v2_key(raw_dec)

    simple = simple_make_key(106, 8)
    tea_key = bytearray(16)
    for i in range(8):
        tea_key[i << 1] = simple[i]
        tea_key[(i << 1) + 1] = raw_dec[i]

    sub = decrypt_tencent_tea(raw_dec[8:], bytes(tea_key))
    return raw_dec[:8] + sub


# --------------------------------------------------------------------------- #
# 三种流密码
# --------------------------------------------------------------------------- #

_STATIC_BOX = bytes([
    0x77, 0x48, 0x32, 0x73, 0xDE, 0xF2, 0xC0, 0xC8,
    0x95, 0xEC, 0x30, 0xB2, 0x51, 0xC3, 0xE1, 0xA0,
    0x9E, 0xE6, 0x9D, 0xCF, 0xFA, 0x7F, 0x14, 0xD1,
    0xCE, 0xB8, 0xDC, 0xC3, 0x4A, 0x67, 0x93, 0xD6,
    0x28, 0xC2, 0x91, 0x70, 0xCA, 0x8D, 0xA2, 0xA4,
    0xF0, 0x08, 0x61, 0x90, 0x7E, 0x6F, 0xA2, 0xE0,
    0xEB, 0xAE, 0x3E, 0xB6, 0x67, 0xC7, 0x92, 0xF4,
    0x91, 0xB5, 0xF6, 0x6C, 0x5E, 0x84, 0x40, 0xF7,
    0xF3, 0x1B, 0x02, 0x7F, 0xD5, 0xAB, 0x41, 0x89,
    0x28, 0xF4, 0x25, 0xCC, 0x52, 0x11, 0xAD, 0x43,
    0x68, 0xA6, 0x41, 0x8B, 0x84, 0xB5, 0xFF, 0x2C,
    0x92, 0x4A, 0x26, 0xD8, 0x47, 0x6A, 0x7C, 0x95,
    0x61, 0xCC, 0xE6, 0xCB, 0xBB, 0x3F, 0x47, 0x58,
    0x89, 0x75, 0xC3, 0x75, 0xA1, 0xD9, 0xAF, 0xCC,
    0x08, 0x73, 0x17, 0xDC, 0xAA, 0x9A, 0xA2, 0x16,
    0x41, 0xD8, 0xA2, 0x06, 0xC6, 0x8B, 0xFC, 0x66,
    0x34, 0x9F, 0xCF, 0x18, 0x23, 0xA0, 0x0A, 0x74,
    0xE7, 0x2B, 0x27, 0x70, 0x92, 0xE9, 0xAF, 0x37,
    0xE6, 0x8C, 0xA7, 0xBC, 0x62, 0x65, 0x9C, 0xC2,
    0x08, 0xC9, 0x88, 0xB3, 0xF3, 0x43, 0xAC, 0x74,
    0x2C, 0x0F, 0xD4, 0xAF, 0xA1, 0xC3, 0x01, 0x64,
    0x95, 0x4E, 0x48, 0x9F, 0xF4, 0x35, 0x78, 0x95,
    0x7A, 0x39, 0xD6, 0x6A, 0xA0, 0x6D, 0x40, 0xE8,
    0x4F, 0xA8, 0xEF, 0x11, 0x1D, 0xF3, 0x1B, 0x3F,
    0x3F, 0x07, 0xDD, 0x6F, 0x5B, 0x19, 0x30, 0x19,
    0xFB, 0xEF, 0x0E, 0x37, 0xF0, 0x0E, 0xCD, 0x16,
    0x49, 0xFE, 0x53, 0x47, 0x13, 0x1A, 0xBD, 0xA4,
    0xF1, 0x40, 0x19, 0x60, 0x0E, 0xED, 0x68, 0x09,
    0x06, 0x5F, 0x4D, 0xCF, 0x3D, 0x1A, 0xFE, 0x20,
    0x77, 0xE4, 0xD9, 0xDA, 0xF9, 0xA4, 0x2B, 0x76,
    0x1C, 0x71, 0xDB, 0x00, 0xBC, 0xFD, 0x0C, 0x6C,
    0xA5, 0x47, 0xF7, 0xF6, 0x00, 0x79, 0x4A, 0x11,
])
assert len(_STATIC_BOX) == 256


class StreamCipher:
    def decrypt(self, buf: bytearray, offset: int = 0) -> None:
        raise NotImplementedError


def _xor_into(buf: bytearray, start: int, mask: bytes) -> None:
    """用大整数一次性 XOR 一整段，比逐字节循环快一个数量级。"""
    n = len(mask)
    if n == 0:
        return
    left = int.from_bytes(buf[start:start + n], "big")
    buf[start:start + n] = (left ^ int.from_bytes(mask, "big")).to_bytes(n, "big")


class _PeriodicCipher(StreamCipher):
    """周期性 mask 的公共实现。

    原实现的回绕规则是「offset > 0x7fff 才取模」，所以 0..0x7fff 共
    0x8000 个下标全部有效，之后按 0x7fff 循环（注意 0xfffe % 0x7fff == 0，
    绕回后会回到 0，不是 0x7fff）。
    预生成 0x8000 长的 mask 表 + 大整数 XOR，比逐字节计算快一个数量级。
    """

    PERIOD = 0x7FFF

    def _build_table(self) -> bytes:
        raise NotImplementedError

    def __init__(self) -> None:
        self._table = self._build_table()
        if len(self._table) != self.PERIOD + 1:
            raise ValueError("mask 表长度必须是 0x8000")
        self._cycle = self._table[: self.PERIOD]

    def decrypt(self, buf: bytearray, offset: int = 0) -> None:
        table, cycle, period = self._table, self._cycle, self.PERIOD
        total = len(buf)
        i = 0
        while i < total:
            off = offset + i
            if off <= period:
                length = min(total - i, period - off + 1)
                _xor_into(buf, i, table[off:off + length])
            else:
                pos = off % period
                length = min(total - i, period - pos)
                _xor_into(buf, i, cycle[pos:pos + length])
            i += length


class QmcStaticCipher(_PeriodicCipher):
    """QMCv1：固定表，纯 XOR，密钥不在文件里。"""

    def _build_table(self) -> bytes:
        box = _STATIC_BOX
        return bytes(box[((i * i) + 27) & 0xFF] for i in range(self.PERIOD + 1))


class QmcMapCipher(_PeriodicCipher):
    """QMCv2 短密钥：按 (offset²+71214) mod n 取 key，再做循环移位。"""

    def __init__(self, key: bytes) -> None:
        if not key:
            raise ValueError("qmc/map: 密钥为空")
        self.key = key
        self.n = len(key)
        super().__init__()

    @staticmethod
    def _rotate(value: int, bits: int) -> int:
        shift = (bits + 4) % 8
        left = (value << shift) & 0xFF
        right = value >> shift
        return (left | right) & 0xFF

    def _build_table(self) -> bytes:
        key, n = self.key, self.n
        rotate = self._rotate
        out = bytearray(self.PERIOD + 1)
        for i in range(self.PERIOD + 1):
            idx = (i * i + 71214) % n
            out[i] = rotate(key[idx], idx & 0x07)
        return bytes(out)

    def get_mask(self, offset: int) -> int:
        if offset > self.PERIOD:
            offset %= self.PERIOD
        return self._table[offset]


class QmcRC4Cipher(StreamCipher):
    """QMCv2 长密钥：首段 128 字节特殊处理，之后按 5120 字节分段 RC4。"""

    FIRST_SEGMENT_SIZE = 0x80
    SEGMENT_SIZE = 5120

    def __init__(self, key: bytes) -> None:
        if not key:
            raise ValueError("qmc/rc4: 密钥为空")
        self.key = key
        self.n = n = len(key)

        box = bytearray(n)
        for i in range(n):
            box[i] = i & 0xFF
        j = 0
        for i in range(n):
            j = (box[i] + j + key[i % n]) % n
            box[i], box[j] = box[j], box[i]
        self._box = bytes(box)

        h = 1
        for i in range(n):
            value = key[i]
            if not value:
                continue
            nxt = (h * value) & 0xFFFFFFFF
            if nxt == 0 or nxt <= h:
                break
            h = nxt
        self.hash = h

    def get_segment_key(self, seg_id: int) -> int:
        seed = self.key[seg_id % self.n]
        if seed == 0:
            return 0
        idx = int((self.hash / ((seg_id + 1) * seed)) * 100.0)
        return idx % self.n

    def _first_segment(self, out: bytearray, start: int, length: int, offset: int) -> None:
        key, n = self.key, self.n
        for i in range(length):
            out[start + i] = key[self.get_segment_key(offset + i)]

    def _segment(self, out: bytearray, start: int, length: int, offset: int) -> None:
        """把一段 keystream 写进 out，不在这里做 XOR。

        每段都新建缓冲会把 Windows 堆打碎，同一进程连续解密会越跑越慢
        （实测 5.2 → 1.6 MB/s），所以改成共用一块大缓冲、最后一次性 XOR。
        """
        n = self.n
        box = bytearray(self._box)
        skip = (offset % self.SEGMENT_SIZE) + self.get_segment_key(offset // self.SEGMENT_SIZE)

        j = 0
        k = 0
        for _ in range(skip):
            j += 1
            if j == n:
                j = 0
            value = box[j]
            k += value
            if k >= n:
                k -= n
            box[j] = box[k]
            box[k] = value

        for i in range(length):
            j += 1
            if j == n:
                j = 0
            value = box[j]
            k += value
            if k >= n:
                k -= n
            box[j] = box[k]
            box[k] = value
            total = box[j] + box[k]
            if total >= n:
                total -= n
            out[start + i] = box[total]

    def decrypt(self, buf: bytearray, offset: int = 0) -> None:
        total = len(buf)
        if total == 0:
            return

        stream = bytearray(total)
        pos = 0

        if offset < self.FIRST_SEGMENT_SIZE:
            length = min(total, self.FIRST_SEGMENT_SIZE - offset)
            self._first_segment(stream, 0, length, offset)
            pos += length
            offset += length

        if pos < total:
            rem = offset % self.SEGMENT_SIZE
            if rem:
                length = min(self.SEGMENT_SIZE - rem, total - pos)
                self._segment(stream, pos, length, offset)
                pos += length
                offset += length

            while total - pos > self.SEGMENT_SIZE:
                self._segment(stream, pos, self.SEGMENT_SIZE, offset)
                pos += self.SEGMENT_SIZE
                offset += self.SEGMENT_SIZE

            if total - pos > 0:
                self._segment(stream, pos, total - pos, offset)

        _xor_into(buf, 0, stream)


# --------------------------------------------------------------------------- #
# 容器嗅探
# --------------------------------------------------------------------------- #

_HEADERS = (
    (b"fLaC", "flac"),
    (b"OggS", "ogg"),
    (b"RIFF", "wav"),
    (b"\x30\x26\xB2\x75", "wma"),
    (b"MAC ", "ape"),
    (b"FORM", "aiff"),
    (b"DFF", "dff"),
    (b"ID3", "mp3"),
)


def sniff_container(data: bytes, fallback: str = "mp3") -> str:
    """看文件头判断明文到底是什么容器。"""
    return sniff_container_strict(data) or fallback


def sniff_container_strict(data: bytes) -> str | None:
    """严格版：认不出来就返回 None，不猜。

    解密之后必须用它校验——否则遇到新加密格式会静默产出一个几十 MB
    的垃圾文件，比直接报错糟糕得多。
    """
    if len(data) < 12:
        return None
    for magic, name in _HEADERS:
        if data.startswith(magic):
            return name
    if data[4:8] == b"ftyp":
        return "m4a"
    # MP3 帧同步：0xFF 后三位为 111
    if data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
        return "mp3"
    # ADTS AAC
    if data[0] == 0xFF and (data[1] & 0xF0) == 0xF0:
        return "aac"
    return None
