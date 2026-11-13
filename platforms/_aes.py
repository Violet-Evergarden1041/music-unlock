"""极简 AES-128 ECB 解密。

ncm 里用 AES 的只有两处：密钥段（几百字节）和元数据段（几 KB）。
数据量这么小，纯 Python 实现完全够用，省掉 pycryptodome / cryptography
依赖，打包体积也不用再涨。

正确性由 FIPS-197 官方测试向量保证（见 tests/test_netease.py）。
"""

from __future__ import annotations

_NB = 4  # 列数
_NK = 4  # 128bit = 4 个字
_NR = 10  # 10 轮


def _xtime(value: int) -> int:
    value <<= 1
    if value & 0x100:
        value ^= 0x11B
    return value & 0xFF


def _gmul(a: int, b: int) -> int:
    """GF(2^8) 乘法，模 x^8+x^4+x^3+x+1。"""
    result = 0
    for _ in range(8):
        if b & 1:
            result ^= a
        a = _xtime(a)
        b >>= 1
    return result


def _build_sbox() -> tuple[list[int], list[int]]:
    """用「乘法逆元 + 仿射变换」现算 S 盒，避免手抄 256 个常量出错。"""

    def inverse(value: int) -> int:
        if value == 0:
            return 0
        for candidate in range(1, 256):
            if _gmul(value, candidate) == 1:
                return candidate
        raise AssertionError("GF(256) 逆元不存在")

    box = [0] * 256
    for value in range(256):
        inv = inverse(value)
        acc = inv
        shifted = inv
        for _ in range(4):
            shifted = ((shifted << 1) | (shifted >> 7)) & 0xFF
            acc ^= shifted
        box[value] = acc ^ 0x63

    inverse_box = [0] * 256
    for index, value in enumerate(box):
        inverse_box[value] = index
    return box, inverse_box


_SBOX, _INV_SBOX = _build_sbox()
_RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)


def expand_key(key: bytes) -> list[list[int]]:
    """AES-128 密钥扩展，返回 11 组轮密钥（每组 16 字节）。"""
    if len(key) != 16:
        raise ValueError("AES-128 密钥必须 16 字节")

    words = [list(key[i * 4:(i + 1) * 4]) for i in range(_NK)]
    for i in range(_NK, _NB * (_NR + 1)):
        temp = list(words[i - 1])
        if i % _NK == 0:
            temp = temp[1:] + temp[:1]                     # RotWord
            temp = [_SBOX[b] for b in temp]                # SubWord
            temp[0] ^= _RCON[i // _NK - 1]
        words.append([words[i - _NK][j] ^ temp[j] for j in range(4)])

    rounds = []
    for rnd in range(_NR + 1):
        flat: list[int] = []
        for word in words[rnd * 4:(rnd + 1) * 4]:
            flat.extend(word)
        rounds.append(flat)
    return rounds


def _add_round_key(state: list[int], round_key: list[int]) -> None:
    for i in range(16):
        state[i] ^= round_key[i]


def _inv_shift_rows(state: list[int]) -> None:
    for row in range(1, 4):
        values = [state[row + 4 * col] for col in range(4)]
        values = values[-row:] + values[:-row]
        for col in range(4):
            state[row + 4 * col] = values[col]


def _inv_mix_columns(state: list[int]) -> None:
    for col in range(4):
        a = state[col * 4:col * 4 + 4]
        state[col * 4 + 0] = _gmul(a[0], 14) ^ _gmul(a[1], 11) ^ _gmul(a[2], 13) ^ _gmul(a[3], 9)
        state[col * 4 + 1] = _gmul(a[0], 9) ^ _gmul(a[1], 14) ^ _gmul(a[2], 11) ^ _gmul(a[3], 13)
        state[col * 4 + 2] = _gmul(a[0], 13) ^ _gmul(a[1], 9) ^ _gmul(a[2], 14) ^ _gmul(a[3], 11)
        state[col * 4 + 3] = _gmul(a[0], 11) ^ _gmul(a[1], 13) ^ _gmul(a[2], 9) ^ _gmul(a[3], 14)


def decrypt_block(block: bytes, round_keys: list[list[int]]) -> bytes:
    state = list(block)
    _add_round_key(state, round_keys[_NR])

    for rnd in range(_NR - 1, 0, -1):
        _inv_shift_rows(state)
        state = [_INV_SBOX[b] for b in state]
        _add_round_key(state, round_keys[rnd])
        _inv_mix_columns(state)

    _inv_shift_rows(state)
    state = [_INV_SBOX[b] for b in state]
    _add_round_key(state, round_keys[0])
    return bytes(state)


def _unpad(data: bytes) -> bytes:
    if not data or len(data) % 16:
        raise ValueError("AES 密文长度不是 16 的倍数")
    pad = data[-1]
    if pad < 1 or pad > 16 or data[-pad:] != bytes([pad]) * pad:
        raise ValueError("PKCS7 填充非法（密钥可能不对）")
    return data[:-pad]


def decrypt_ecb(data: bytes, key: bytes) -> bytes:
    """AES-128-ECB 解密并去掉 PKCS7 填充。"""
    if len(data) % 16:
        raise ValueError("AES 密文长度不是 16 的倍数")
    rounds = expand_key(key)
    out = bytearray()
    for offset in range(0, len(data), 16):
        out += decrypt_block(data[offset:offset + 16], rounds)
    return _unpad(bytes(out))


def _shift_rows(state: list[int]) -> None:
    for row in range(1, 4):
        values = [state[row + 4 * col] for col in range(4)]
        values = values[row:] + values[:row]
        for col in range(4):
            state[row + 4 * col] = values[col]


def _mix_columns(state: list[int]) -> None:
    for col in range(4):
        a = state[col * 4:col * 4 + 4]
        state[col * 4 + 0] = _gmul(a[0], 2) ^ _gmul(a[1], 3) ^ a[2] ^ a[3]
        state[col * 4 + 1] = a[0] ^ _gmul(a[1], 2) ^ _gmul(a[2], 3) ^ a[3]
        state[col * 4 + 2] = a[0] ^ a[1] ^ _gmul(a[2], 2) ^ _gmul(a[3], 3)
        state[col * 4 + 3] = _gmul(a[0], 3) ^ a[1] ^ a[2] ^ _gmul(a[3], 2)


def encrypt_block(block: bytes, round_keys: list[list[int]]) -> bytes:
    state = list(block)
    _add_round_key(state, round_keys[0])

    for rnd in range(1, _NR):
        state = [_SBOX[b] for b in state]
        _shift_rows(state)
        _mix_columns(state)
        _add_round_key(state, round_keys[rnd])

    state = [_SBOX[b] for b in state]
    _shift_rows(state)
    _add_round_key(state, round_keys[_NR])
    return bytes(state)


def _pad(data: bytes) -> bytes:
    pad = 16 - (len(data) % 16)
    return data + bytes([pad]) * pad


def encrypt_ecb(data: bytes, key: bytes) -> bytes:
    """AES-128-ECB 加密（PKCS7 填充）。只用于构造测试夹具。"""
    rounds = expand_key(key)
    padded = _pad(data)
    out = bytearray()
    for offset in range(0, len(padded), 16):
        out += encrypt_block(padded[offset:offset + 16], rounds)
    return bytes(out)
