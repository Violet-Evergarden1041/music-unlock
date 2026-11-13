"""QQ 音乐 2026 版 `musicex` 容器解析（纯计算，不做 IO）。

格式
----
```
[加密的音频本体]
[192 字节元数据块]
[u32 192] [u32 1] "musicex\\0"
```

192 字节块内：
```
0  16   疑似密钥材料（每首不同）
16 4    u32，每首不同（用途未知，可能是校验）
20 4    u32 = 2
24 4    u32 = 5
28 ..   UTF-16LE 的 14 字符 id，例如 001yS0N33yPm1B
  ..    UTF-16LE 的原文件名，例如 F0M0002MX8Ea4e5R2DS.mflac
```

关键结论（实测得出）：**这不是新的密码学方案**。音频本体仍然是 QMCv2 的
RC4 流密码，只是 ekey 不再写在文件里，改由客户端向服务端取。所以拿到 ekey
之后，用原来的 `derive_key` + `QmcRC4Cipher` 就能离线解，速度还很快。
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass

MAGIC = b"musicex\0"
BLOCK_SIZE = 192
FOOTER_SIZE = 16          # [u32 块长][u32 版本]["musicex\0"]
TRAILER_SIZE = BLOCK_SIZE + FOOTER_SIZE   # 解密后要从尾部截掉的字节数

_ASCII_RUN = re.compile(r"[\x20-\x7e]{6,}")


@dataclass
class MusicExInfo:
    audio_size: int                 # 去掉尾部 208 字节后的音频长度
    file_id: str = ""               # 14 字符 id，用作密钥缓存的键
    original_name: str = ""         # 原文件名
    key_material: bytes = b""       # 块首 16 字节
    extra: int = 0                  # 块内那个每首不同的 u32


def is_musicex(data: bytes) -> bool:
    return len(data) > TRAILER_SIZE and data.endswith(MAGIC)


def parse(data: bytes) -> MusicExInfo:
    """解析尾部结构。不合法直接抛 ValueError。"""
    if not is_musicex(data):
        raise ValueError("尾部没有 musicex 标记")

    footer = data[-(FOOTER_SIZE):]
    block_len, version = struct.unpack("<II", footer[:8])
    if block_len != BLOCK_SIZE:
        # 未来版本可能变长，按实际长度处理，别硬拒绝
        block_len = block_len or BLOCK_SIZE

    start = len(data) - FOOTER_SIZE - block_len
    if start < 0 or start > len(data):
        raise ValueError("musicex 块长度非法，文件可能被截断")

    block = data[start:len(data) - FOOTER_SIZE]
    key_material = block[:16]
    extra = struct.unpack("<I", block[16:20])[0] if len(block) >= 20 else 0

    text = block[20:].decode("utf-16-le", "ignore")
    runs = _ASCII_RUN.findall(text)
    file_id = runs[0] if runs else ""
    original_name = next(
        (r for r in runs if r.lower().endswith((".mflac", ".mgg", ".mmp4"))),
        runs[1] if len(runs) > 1 else "",
    )

    return MusicExInfo(
        audio_size=len(data) - FOOTER_SIZE - block_len,
        file_id=file_id,
        original_name=original_name,
        key_material=key_material,
        extra=extra,
    )
