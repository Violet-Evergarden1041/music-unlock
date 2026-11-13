"""网易云 ncm 解密验收。

真值来源分两层：

1. AES 用 FIPS-197 官方测试向量锁死（外部权威真值）；
2. ncm 的容器布局用「按文档造一个 ncm → 解回来」验证，音频必须逐字节相同。

第 2 层验证的是布局/密钥流/元数据解析，不依赖任何真实音乐文件。
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import ffbin  # noqa: E402
from core.pipeline import process  # noqa: E402
from core.probe import probe  # noqa: E402
from platforms import _aes, netease  # noqa: E402
from platforms.base import decoder_for  # noqa: E402
from _sample import find_sample  # noqa: E402

WORK = ROOT / "_build" / "ncm"
META_PREFIX = b"163 key(Don't modify):"  # 必须是 22 字节，解码器按这个偏移切

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"[ OK ] {name}")
    else:
        print(f"[FAIL] {name}  {detail}")
        failures.append(name)


# --------------------------------------------------------------------------- #
# AES
# --------------------------------------------------------------------------- #


def test_aes() -> None:
    key = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    plain = bytes.fromhex("00112233445566778899aabbccddeeff")
    want = "69c4e0d86a7b0430d8cdb78070b4c55a"

    rounds = _aes.expand_key(key)
    got = _aes.encrypt_block(plain, rounds).hex()
    check("AES-128 加密符合 FIPS-197", got == want, got)

    back = _aes.decrypt_block(bytes.fromhex(want), rounds)
    check("AES-128 解密可逆", back == plain, back.hex())

    assert len(META_PREFIX) == 22, f"元数据前缀应为 22 字节，实际 {len(META_PREFIX)}"


# --------------------------------------------------------------------------- #
# 构造 ncm
# --------------------------------------------------------------------------- #


def build_ncm(audio: bytes, meta: dict, cover: bytes, key_data: bytes) -> bytes:
    """按 ncm 格式拼一个完整文件。"""
    # 密钥段：明文前 17 字节会被解码器丢掉
    key_plain = b"\x9c" * 17 + key_data
    key_cipher = _aes.encrypt_ecb(key_plain, netease.CORE_KEY)
    key_cipher = bytes(b ^ 0x64 for b in key_cipher)

    # 元数据段：22 字节前缀 + base64(AES(json))，整体再 ^0x63
    payload = b"music:" + json.dumps(meta, ensure_ascii=False).encode("utf-8")
    body = base64.b64encode(_aes.encrypt_ecb(payload, netease.META_KEY))
    meta_cipher = bytes(b ^ 0x63 for b in META_PREFIX + body)

    # 音频本体：与 keybox 循环异或
    key_box = netease.NcmDecoder._key_box(key_data)
    scrambled = bytes(b ^ key_box[i & 0xFF] for i, b in enumerate(audio))

    # 真实文件的封面块前面还有个 4 字节长度前缀，这里照做，用来覆盖 normalize_cover
    cover_block = (4 + len(cover)).to_bytes(4, "little") + cover

    out = bytearray()
    out += netease.MAGIC + b"\x01\x70"
    out += len(key_cipher).to_bytes(4, "little") + key_cipher
    out += len(meta_cipher).to_bytes(4, "little") + meta_cipher
    out += b"\x00" * 4                      # CRC（解码器不校验）
    out += b"\x01"                          # 1 字节间隔
    out += len(cover_block).to_bytes(4, "little") + cover_block
    out += b"\x00" * 4                      # 图片后的间隔
    out += scrambled
    return bytes(out)


def make_excerpt(src: Path, seconds: int = 20) -> Path:
    dst = WORK / "excerpt.flac"
    if dst.is_file() and dst.stat().st_size > 0:
        return dst
    subprocess.run(
        [ffbin.ffmpeg(), "-hide_banner", "-nostdin", "-v", "error", "-y",
         "-i", str(src), "-t", str(seconds), "-map", "0:a:0",
         "-c:a", "flac", str(dst)],
        check=True, creationflags=ffbin.CREATE_NO_WINDOW,
    )
    return dst


def make_cover(src: Path) -> bytes:
    dst = WORK / "cover.jpg"
    if dst.is_file() and dst.stat().st_size > 0:
        return dst.read_bytes()
    subprocess.run(
        [ffbin.ffmpeg(), "-hide_banner", "-nostdin", "-v", "error", "-y",
         "-i", str(src), "-map", "0:v:0", "-c:v", "copy", "-frames:v", "1",
         str(dst)],
        check=True, creationflags=ffbin.CREATE_NO_WINDOW,
    )
    return dst.read_bytes()


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #


def main() -> int:
    print("=== AES ===")
    test_aes()

    sample = find_sample()
    if sample is None:
        print("\n[SKIP] 没有可用的测试音频，设置 MUSIC_UNLOCK_SAMPLE 或放一个到 tests/fixtures/")
        return 0 if not failures else 1

    WORK.mkdir(parents=True, exist_ok=True)
    audio = make_excerpt(sample).read_bytes()
    cover = make_cover(sample)
    print(f"\n测试音频 {len(audio) / 1048576:.1f} MB，封面 {len(cover) // 1024} KB")

    meta = {
        "musicName": "星降る海 (Starry Sea)",
        "artist": [["Aqu3ra", 123456], ["早見沙織", 654321]],
        "album": "测试专辑",
        "format": "flac",
    }
    key_data = bytes(range(1, 129))  # 任意长度都行，取 128
    blob = build_ncm(audio, meta, cover, key_data)

    ncm_path = WORK / "test.ncm"
    ncm_path.write_bytes(blob)
    print(f"已构造 {ncm_path.name}  {len(blob) / 1048576:.1f} MB")

    # ---- 1. 解码器本身 ----
    print("\n=== 解码 ===")
    decoder = decoder_for(ncm_path)
    check("能识别 .ncm", decoder is not None and decoder.name == "netease")

    decoded = decoder.decode(ncm_path)
    check("音频逐字节一致", decoded.data == audio,
          f"{len(decoded.data)} vs {len(audio)}")
    check("容器识别为 flac", decoded.container == "flac", decoded.container)
    check("歌名", decoded.meta.get("title") == meta["musicName"], decoded.meta.get("title"))
    check("歌手合并", decoded.meta.get("artist") == "Aqu3ra / 早見沙織",
          decoded.meta.get("artist"))
    check("专辑", decoded.meta.get("album") == meta["album"], decoded.meta.get("album"))
    check("封面提取", decoded.cover == cover,
          f"{len(decoded.cover or b'')} vs {len(cover)}")
    check("封面已剥掉长度前缀",
          (decoded.cover or b"").startswith(b"\xff\xd8\xff"),
          (decoded.cover or b"")[:4].hex())

    # ---- 2. 走完整流水线 ----
    print("\n=== 流水线 ===")
    result = process(ncm_path, WORK / "out", target="flac")
    check("处理成功", result.ok, result.message)
    if result.ok:
        info = probe(result.output)
        check("产物是 flac", info.codec == "flac", info.codec)
        check("产物带封面", info.has_cover, "没有封面")
        check("产物带标签", info.tags.get("title") == meta["musicName"],
              str(info.tags.get("title")))
        print(f"        {result.output.name}  {result.output.stat().st_size / 1048576:.1f} MB")
        for warning in result.warnings:
            print(f"        [!] {warning}")

        # 再转一次 MP3，确认封面能塞进 ID3
        mp3 = process(ncm_path, WORK / "out_mp3", target="mp3")
        check("转 MP3 成功", mp3.ok, mp3.message)
        if mp3.ok:
            check("MP3 带封面", probe(mp3.output).has_cover, "没有封面")

    print(f"\n失败 {len(failures)} 项")
    for item in failures:
        print("  FAIL:", item)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
