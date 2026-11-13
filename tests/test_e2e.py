"""端到端验收：伪造一个真实的 .mflac，走完 解密 → 转码 → 校验 全链路。

手法：拿 unlock-music 夹具里**真实**的 ekey 与派生密钥，把一段真 FLAC
用 QMCv2 的 Map / RC4 密码加密，再按 QMC 的文件布局拼上密钥尾部。
这样解出来的明文必须与原始 FLAC 逐字节一致。
"""

from __future__ import annotations

import hashlib
import struct
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import ffbin, formats  # noqa: E402
from core.formats import EncodeOptions  # noqa: E402
from core.pipeline import process  # noqa: E402
from core.probe import probe  # noqa: E402
from platforms import qmc_algo  # noqa: E402
from platforms.qqmusic import QmcDecoder  # noqa: E402
from _sample import find_sample  # noqa: E402

FIXTURES = ROOT / "_ref" / "try_22a53f" / "testdata"
WORK = ROOT / "_build" / "e2e"

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"[ OK ] {name}")
    else:
        print(f"[FAIL] {name}  {detail}")
        failures.append(name)


def pcm_md5(path: Path) -> str:
    proc = subprocess.run(
        [
            ffbin.ffmpeg(), "-hide_banner", "-nostdin", "-v", "error", "-i", str(path),
            "-map", "0:a:0", "-f", "s16le", "-acodec", "pcm_s16le",
            "-ar", "44100", "-ac", "2", "-",
        ],
        capture_output=True,
        creationflags=ffbin.CREATE_NO_WINDOW,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[-300:])
    return hashlib.md5(proc.stdout).hexdigest()


def make_excerpt(src: Path, seconds: int = 45) -> Path:
    dst = WORK / "excerpt.flac"
    if dst.is_file() and dst.stat().st_size > 0:
        return dst
    subprocess.run(
        [
            ffbin.ffmpeg(), "-hide_banner", "-nostdin", "-v", "error", "-y",
            "-i", str(src), "-t", str(seconds), "-map", "0:a:0",
            "-c:a", "flac", "-f", "flac", str(dst),
        ],
        check=True,
        creationflags=ffbin.CREATE_NO_WINDOW,
    )
    return dst


def build_fake_mflac(plain: bytes, key_name: str, cipher_cls) -> tuple[bytes, object]:
    """用真实 ekey 加密，并按 QMC v2 布局拼出 .mflac。"""
    key_raw = (FIXTURES / f"{key_name}_key_raw.bin").read_bytes()
    key = (FIXTURES / f"{key_name}_key.bin").read_bytes()

    buf = bytearray(plain)
    cipher_cls(key).decrypt(buf, 0)  # XOR 对称，加密即解密
    blob = bytes(buf) + key_raw + struct.pack("<I", len(key_raw))
    return blob, key


def main() -> int:
    sample = find_sample()
    if sample is None:
        print("[SKIP] 没有可用的测试音频，设置 MUSIC_UNLOCK_SAMPLE 或放一个到 tests/fixtures/")
        return 0
    if not (FIXTURES / "mflac_map_key_raw.bin").is_file():
        print("[SKIP] 需要 unlock-music 的 testdata 夹具")
        return 0

    WORK.mkdir(parents=True, exist_ok=True)
    excerpt = make_excerpt(sample)
    plain = excerpt.read_bytes()
    print(f"测试源：{excerpt.name}  {len(plain) / 1048576:.1f} MB")

    # ---- 1. 解密正确性 + 速度 ----
    print("\n=== 解密链路 ===")
    for name, cls in (("mflac_map", qmc_algo.QmcMapCipher),
                      ("mflac_rc4", qmc_algo.QmcRC4Cipher)):
        blob, key = build_fake_mflac(plain, name, cls)
        target = WORK / f"fake_{name}.mflac"
        target.write_bytes(blob)

        started = time.perf_counter()
        got = QmcDecoder(blob).decrypt()
        elapsed = time.perf_counter() - started
        speed = len(plain) / 1048576 / max(elapsed, 1e-6)

        check(f"{name} 解密逐字节一致", got == plain, f"{len(got)} vs {len(plain)}")
        print(f"        {len(plain) / 1048576:.1f} MB / {elapsed:.2f}s = {speed:.1f} MB/s")

        # 整条流水线也要能用这个文件
        result = process(target, WORK / "out_single", target="flac", on_stage=None)
        check(f"{name} 流水线跑通", result.ok, result.message)

    # ---- 2. 十种输出格式 ----
    print("\n=== 十种输出格式 ===")
    blob, _ = build_fake_mflac(plain, "mflac_map", qmc_algo.QmcMapCipher)
    src = WORK / "input.mflac"
    src.write_bytes(blob)

    baseline = pcm_md5(excerpt)
    print(f"源 PCM md5: {baseline}")

    for fmt in formats.ALL:
        result = process(
            src, WORK / f"fmt_{fmt.key}", target=fmt,
            opts=EncodeOptions(bitrate_kbps=320),
            naming="{title}",
        )
        if not result.ok:
            check(f"{fmt.label}", False, result.message)
            continue

        info = probe(result.output)
        size_mb = result.output.stat().st_size / 1048576
        note = "copy" if result.copied else "enc "
        check(
            f"{fmt.label:<12} {size_mb:6.1f}MB {info.codec:<11} "
            f"{info.sample_rate}Hz {info.bits}bit {note}",
            True,
        )
        if fmt.lossless and fmt.key != "bwf":
            same = pcm_md5(result.output) == baseline
            check(f"  └ {fmt.label} PCM 与源一致", same)

    print(f"\n失败 {len(failures)} 项")
    for f in failures:
        print("  FAIL:", f)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
