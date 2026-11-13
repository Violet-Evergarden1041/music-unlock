"""酷狗 kgm/vpr、酷我 kwm 的往返验证。

思路：按各自格式**造一个加密文件**，再让解码器解回来，比对原始音频。
这几个算法都是确定性的、可逆的，所以往返一致就说明实现没写错。

注意：这只能证明「算法实现自洽」，不能证明「真实下载文件长这样」——
真实文件必须等拿到样本再验（`tests/test_real_samples.py` 会做那件事）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from platforms import _kugou_tables as T  # noqa: E402
from platforms.base import decoder_for  # noqa: E402
from platforms.kugou import KgmCipher  # noqa: E402
from platforms.kuwo import (  # noqa: E402
    MAGIC_TME, _xor_with_mask, create_mask,
)

WORK = ROOT / "_build" / "platforms"
failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"[ OK ] {name}")
    else:
        print(f"[FAIL] {name}  {detail}")
        failures.append(name)


def audio_sample(size: int = 200_000) -> bytes:
    """拿真实 FLAC 的一小段当载荷，保证解出来能被嗅探成音频。"""
    src = ROOT / "_build" / "e2e" / "excerpt.flac"
    if src.is_file():
        return src.read_bytes()[:size]
    return b"fLaC" + bytes((i * 37) & 0xFF for i in range(size - 4))


# --------------------------------------------------------------------------- #
# 酷狗
# --------------------------------------------------------------------------- #


def build_kgm(audio: bytes, key16: bytes, is_vpr: bool) -> bytes:
    header_len = 0x400
    buf = bytearray(header_len)
    buf[0:16] = T.VPR_HEADER if is_vpr else T.KGM_HEADER
    buf[0x10:0x14] = header_len.to_bytes(4, "little")
    buf[0x1C:0x2C] = key16
    return bytes(buf) + KgmCipher(key16, is_vpr).encrypt(audio)


def test_kugou(audio: bytes) -> None:
    print("=== 酷狗 ===")
    for is_vpr, ext in ((False, "kgm"), (False, "kgma"), (True, "vpr")):
        key16 = bytes(range(0x10, 0x20))
        blob = build_kgm(audio, key16, is_vpr)
        path = WORK / f"sample.{ext}"
        path.write_bytes(blob)

        decoder = decoder_for(path)
        check(f"{ext} 能被识别", decoder is not None and decoder.name == "kugou")
        try:
            decoded = decoder.decode(path)
        except Exception as exc:
            check(f"{ext} 解码往返", False, f"{type(exc).__name__}: {exc}")
            continue
        check(f"{ext} 解码往返", decoded.data == audio,
              f"{len(decoded.data)} vs {len(audio)}")
        check(f"{ext} 容器识别", decoded.container == "flac", decoded.container)

    # kgg 必须明确拒绝，而不是默默产出垃圾
    kgg = WORK / "sample.kgg"
    kgg.write_bytes(build_kgm(audio, bytes(16), False))
    try:
        decoder_for(kgg).decode(kgg)
        check("kgg 明确拒绝", False, "居然解出来了")
    except Exception as exc:
        check("kgg 明确拒绝", "kgg" in str(exc), str(exc)[:60])


# --------------------------------------------------------------------------- #
# 酷我
# --------------------------------------------------------------------------- #


def build_kwm(audio: bytes, key8: bytes) -> bytes:
    buf = bytearray(0x400)
    buf[0:16] = MAGIC_TME
    buf[0x18:0x20] = key8
    return bytes(buf) + _xor_with_mask(audio, create_mask(key8))


def test_kuwo(audio: bytes) -> None:
    print("\n=== 酷我 ===")
    key8 = bytes.fromhex("0123456789ABCDEF")
    blob = build_kwm(audio, key8)
    path = WORK / "sample.kwm"
    path.write_bytes(blob)

    decoder = decoder_for(path)
    check("kwm 能被识别", decoder is not None and decoder.name == "kuwo")
    try:
        decoded = decoder.decode(path)
        check("kwm 解码往返", decoded.data == audio,
              f"{len(decoded.data)} vs {len(audio)}")
        check("kwm 容器识别", decoded.container == "flac", decoded.container)
    except Exception as exc:
        check("kwm 解码往返", False, f"{type(exc).__name__}: {exc}")

    # mask 生成规则单独验一下：同样的密钥必须稳定得到同样的 mask
    check("kwm mask 长度", len(create_mask(key8)) == 32)
    check("kwm mask 可复现", create_mask(key8) == create_mask(key8))


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    audio = audio_sample()
    print(f"载荷 {len(audio):,} 字节\n")
    test_kugou(audio)
    test_kuwo(audio)
    print(f"\n失败 {len(failures)} 项")
    for item in failures:
        print("  FAIL:", item)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())