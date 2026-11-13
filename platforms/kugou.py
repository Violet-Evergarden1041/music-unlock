"""酷狗音乐 kgm / kgma / vpr 解密。

算法来自 unlock-music 的 ``src/KgmWasm/kgm.hpp``（那套 wasm 的 C++ 源码）：

```
headerLen = u32le(data[0x10:0x14])       # 头部长度，音频从这之后开始
key       = data[0x1C:0x2C] + b"\\x00"    # 16 字节文件密钥 + 补零，共 17 字节

对每个字节 i：
    med8 = key[i % 17] ^ cipher[i]
    med8 ^= (med8 & 0x0F) << 4
    msk8 = getMask(i)
    msk8 ^= (msk8 & 0x0F) << 4
    plain[i] = med8 ^ msk8
    vpr 格式再 ^ VPR_MASK_DIFF[i % 17]
```

`getMask` 是个按 16 字节分块的分层查表：`MASK_V2_PREDEF[pos % 272]` 再异或上一串
`table1` / `table2` 的项，且高层的项在同一个 16 字节块内恒定——所以可以整块算一次，
不用逐字节跑那个 while 循环。

注意：**新版 `kgg` 不在支持范围内。** 它的密钥机制和 kgm 不同，公开实现里也没有，
本项目会明确报错而不是产出垃圾。
"""

from __future__ import annotations

from pathlib import Path

from . import _kugou_tables as T
from .base import Decoded, DecryptError, register
from .qmc_algo import sniff_container_strict

MASK_PERIOD = 272
KEY_LEN = 17
BLOCK = 16


def _build_s_table() -> bytes:
    """S(x) = x ^ ((x & 0x0F) << 4)，是个对合（自己乘自己等于恒等）。"""
    return bytes(x ^ ((x & 0x0F) << 4) for x in range(256))


_S_TABLE = _build_s_table()


def _xor(left: bytes, right: bytes) -> bytes:
    n = len(left)
    if n == 0:
        return b""
    return (
        int.from_bytes(left, "big") ^ int.from_bytes(right[:n], "big")
    ).to_bytes(n, "big")


def _mask_value(pos: int) -> int:
    """``getMask`` 里那个 while 循环算出来的部分（同一 16 字节块内恒定）。

    入参是**字节位置**，不是 C++ 里的 ``offset``——``offset = pos >> 4``
    这一步必须在这里做，否则从第 32 字节开始就会算错。
    """
    offset = pos >> 4
    value = 0
    while offset >= 0x11:
        value ^= T.TABLE1[offset % MASK_PERIOD]
        offset >>= 4
        value ^= T.TABLE2[offset % MASK_PERIOD]
        offset >>= 4
    return value


def _mask_stream(n: int, is_vpr: bool) -> bytes:
    """预生成整段 mask：``S(getMask(i))``，vpr 再叠一个差分。

    注意 ``getMask`` 出来的值还要过一道 ``S(x) = x ^ ((x & 0x0F) << 4)``，
    vpr 的 ``VprMaskDiff`` 是在 S 之后才异或的——顺序不能反。
    """
    if n <= 0:
        return b""

    predef = (T.MASK_V2_PREDEF * (n // MASK_PERIOD + 1))[:n]

    blocks = (n + BLOCK - 1) // BLOCK
    tier = bytearray(n)
    for b in range(blocks):
        start = b * BLOCK
        tier[start:start + BLOCK] = bytes([_mask_value(start)]) * BLOCK

    mask = _xor(predef, bytes(tier)).translate(_S_TABLE)
    if is_vpr:
        diff = (T.VPR_MASK_DIFF * (n // KEY_LEN + 1))[:n]
        mask = _xor(mask, diff)
    return mask


def _tile(unit: bytes, n: int) -> bytes:
    if not unit:
        return bytes(n)
    return (unit * (n // len(unit) + 1))[:n]


class KgmCipher:
    """酷狗 kgm/kgma/vpr 的流密码。"""

    def __init__(self, key16: bytes, is_vpr: bool = False) -> None:
        if len(key16) != 16:
            raise ValueError("kgm 密钥必须是 16 字节")
        self.key = key16 + b"\x00"           # 补一个 0 → 17 字节
        self.is_vpr = is_vpr

    def decrypt(self, data: bytes) -> bytes:
        n = len(data)
        if n == 0:
            return b""
        mixed = _xor(data, _tile(self.key, n))       # key[i % 17] ^ cipher[i]
        substituted = mixed.translate(_S_TABLE)      # S(key ^ cipher)
        return _xor(substituted, _mask_stream(n, self.is_vpr))

    def encrypt(self, data: bytes) -> bytes:
        """仅供测试：按同一套参数反向生成密文。"""
        n = len(data)
        if n == 0:
            return b""
        mask = _mask_stream(n, self.is_vpr)
        # plain = S(key ^ c) ^ mask  →  key ^ c = S(plain ^ mask)  →  c = key ^ S(...)
        inner = _xor(data, mask).translate(_S_TABLE)
        return _xor(inner, _tile(self.key, n))


def parse_header(data: bytes) -> tuple[int, bytes]:
    if len(data) < 0x2C:
        raise DecryptError("文件太小，不是有效的 kgm 文件")
    header_len = int.from_bytes(data[0x10:0x14], "little")
    key16 = data[0x1C:0x2C]
    if header_len < 0x2C or header_len > len(data):
        raise DecryptError("kgm 头部长度字段异常，文件可能损坏")
    return header_len, key16


class KugouDecoder:
    #: 文件名是「歌手 - 歌名」
    filename_order = "artist-title"
    name = "kugou"
    extensions = ("kgm", "kgma", "vpr", "kgg")

    def matches(self, path: Path) -> bool:
        return path.suffix.lower().lstrip(".") in self.extensions

    def guess_container(self, path: Path) -> str | None:
        return None  # 里面可能是 mp3 / flac / ogg / m4a，不看内容猜不出

    def decode(self, path: Path) -> Decoded:
        ext = path.suffix.lower().lstrip(".")
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise DecryptError(f"读不了文件：{exc}") from exc

        if ext == "kgg":
            raise DecryptError(
                "这个文件是酷狗的新版 kgg 加密，密钥机制和 kgm 不同，本项目不支持。\n"
                "能处理的是 kgm / kgma / vpr。"
            )

        is_vpr = ext == "vpr"
        expected = T.VPR_HEADER if is_vpr else T.KGM_HEADER
        if not data.startswith(expected):
            raise DecryptError(
                f"文件头不匹配，不是有效的 {'vpr' if is_vpr else 'kgm'} 文件"
            )

        header_len, key16 = parse_header(data)
        plain = KgmCipher(key16, is_vpr).decrypt(data[header_len:])

        container = sniff_container_strict(plain)
        if container is None:
            raise DecryptError(
                "解出来的数据不是已知音频格式，可能不是标准 kgm 文件。已中止。"
            )

        # 实测：酷狗解出来的 FLAC 自带 TITLE / ARTIST / ALBUM 标签，
        # 流水线会把它们原样带到输出文件里，不用从文件名猜。
        return Decoded(data=plain, container=container)


register(KugouDecoder())
