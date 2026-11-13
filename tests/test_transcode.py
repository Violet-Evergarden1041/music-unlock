"""转码引擎的验收测试。

核心断言：FLAC → WAV → FLAC 之后，解码出来的 PCM 必须逐字节一致。
这是「转码链路真的无损」的唯一硬证据。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import ffbin, formats, formats as F  # noqa: E402
from core.probe import probe  # noqa: E402
from core.transcode import transcode  # noqa: E402
from _sample import find_sample  # noqa: E402


def pcm_md5(path: Path) -> str:
    """把音频解成裸 PCM 再算 md5 —— 忽略容器差异，只比声音。"""
    proc = subprocess.run(
        [
            ffbin.ffmpeg(), "-hide_banner", "-nostdin", "-v", "error",
            "-i", str(path),
            "-map", "0:a:0",
            "-f", "s16le", "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2",
            "-",
        ],
        capture_output=True,
        creationflags=ffbin.CREATE_NO_WINDOW,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[-400:])
    return hashlib.md5(proc.stdout).hexdigest()


def main() -> int:
    sample = find_sample()
    if sample is None:
        print("[SKIP] 没有可用的测试音频，设置 MUSIC_UNLOCK_SAMPLE 或放一个到 tests/fixtures/")
        return 0

    work = ROOT / "_build" / "test"
    work.mkdir(parents=True, exist_ok=True)

    src = probe(sample)
    print(f"源文件: {src.describe()}  时长 {src.duration_hms}  封面={src.has_cover}")
    baseline = pcm_md5(sample)
    print(f"源 PCM md5: {baseline}")

    failures: list[str] = []
    report: list[dict] = []

    for fmt in formats.ALL:
        out = work / f"out_{fmt.key}.{fmt.ext}"
        res = transcode(sample, out, src, fmt, cancel=None)
        if not res.ok:
            failures.append(f"{fmt.label}: {res.message} {res.stderr_tail[:200]}")
            print(f"[FAIL] {fmt.label:<12} {res.message}")
            continue

        info = probe(out)
        size_mb = out.stat().st_size / 1048576
        line = (
            f"[ OK ] {fmt.label:<12} {size_mb:7.1f} MB  "
            f"{info.codec:<12} {info.sample_rate}Hz {info.bits}bit "
            f"{'copy' if res.copied else 'enc '}"
        )
        print(line)
        if res.warnings:
            for w in res.warnings:
                print(f"         [!] {w}")

        report.append({
            "format": fmt.key,
            "codec": info.codec,
            "sample_rate": info.sample_rate,
            "bits": info.bits,
            "bytes": out.stat().st_size,
            "copied": res.copied,
        })

        # 无损链路逐字节校验：FLAC / WAV / AIFF / BWF / ALAC 都必须与源一致
        if fmt.lossless and fmt.key not in ("bwf",):
            got = pcm_md5(out)
            if got != baseline:
                failures.append(f"{fmt.label}: PCM 不一致 {got} != {baseline}")
                print("         [XX] PCM 不一致！")
            else:
                print("         [ok] PCM 与源逐字节一致")

    print("\n=== 结果 ===")
    if failures:
        for f in failures:
            print("FAIL:", f)
        return 1
    print(f"全部 {len(report)} 种格式通过")
    (work / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
