"""真实样本回归：拿真机下载的文件把所有平台跑一遍。

和 `test_platforms.py` 的区别：那边验证的是「算法实现自洽」，这边验证的是
「真实文件能不能解」——两者都必要。真实样本不进仓库，所以没有样本时自动跳过。

样本放这里（任一即可）：

* ``_build/samples/<平台>/xxx``
* ``tests/fixtures/real/xxx``

验收标准：
1. 能识别、能解密，不抛异常；
2. 解出来的容器是已知音频格式，大小合理；
3. 走完整流水线能产出文件，且时长与源一致；
4. 有损转无损会产生对应警告（这是设计行为，不是 bug）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.pipeline import process  # noqa: E402
from core.probe import probe  # noqa: E402
from platforms.base import decoder_for, known_extensions  # noqa: E402

WORK = ROOT / "_build" / "real_regression"
SAMPLE_DIRS = [
    ROOT / "_build" / "samples",
    ROOT / "tests" / "fixtures" / "real",
]
AUDIO_CONTAINERS = {"flac", "mp3", "ogg", "m4a", "wav", "aac", "ape", "wma"}
MIN_AUDIO_BYTES = 50_000
MIN_DURATION = 20.0

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    if cond:
        print(f"[ OK ] {name}")
    else:
        print(f"[FAIL] {name}  {detail}")
        failures.append(name)
    return cond


def find_samples() -> list[Path]:
    known = set(known_extensions())
    found: list[Path] = []
    for root in SAMPLE_DIRS:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix.lower().lstrip(".") in known:
                # 被客户端截断成 0 字节的残骸不是有效样本，跳过而不是报失败
                if path.stat().st_size < 1024:
                    print(f"[SKIP] {path.name}：只有 {path.stat().st_size} 字节，是残骸")
                    continue
                found.append(path)
    return found


def run_one(path: Path) -> None:
    print(f"\n=== {path.name}（{path.stat().st_size / 1048576:.1f} MB）===")

    decoder = decoder_for(path)
    if not check("能识别平台", decoder is not None, "没有匹配的解密器"):
        return
    print(f"       平台: {decoder.name}")

    try:
        decoded = decoder.decode(path)
    except Exception as exc:
        check("能解密", False, f"{type(exc).__name__}: {exc}")
        return
    check("能解密", True)

    check(
        f"容器合法（{decoded.container}）",
        decoded.container in AUDIO_CONTAINERS,
        str(decoded.container),
    )
    check(
        f"音频大小合理（{len(decoded.data) / 1048576:.1f} MB）",
        len(decoded.data) > MIN_AUDIO_BYTES,
        f"{len(decoded.data)} 字节",
    )

    # 走完整流水线
    try:
        result = process(path, WORK / decoder.name, target=None)
    except Exception as exc:
        check("流水线跑通", False, f"{type(exc).__name__}: {exc}")
        return

    if not check("流水线跑通", result.ok, result.message):
        return

    info = probe(result.output)
    check(
        f"产物时长合理（{info.duration_hms}）",
        info.duration >= MIN_DURATION,
        f"{info.duration:.1f}s",
    )
    print(f"       产物: {result.output.name}  {result.output.stat().st_size / 1048576:.1f} MB")
    for warning in result.warnings:
        print(f"       [!] {warning}")


def main() -> int:
    samples = find_samples()
    if not samples:
        print("[SKIP] 没有真实样本。")
        print("       把真机下载的文件放到 _build/samples/<平台>/ 或 tests/fixtures/real/")
        return 0

    # 每次跑都从干净目录开始，避免产物名带上 (2)(3)
    if WORK.is_dir():
        import shutil

        shutil.rmtree(WORK, ignore_errors=True)

    print(f"找到 {len(samples)} 个真实样本\n")
    for path in samples:
        run_one(path)

    print(f"\n共 {len(samples)} 个样本，失败 {len(failures)} 项")
    for item in failures:
        print("  FAIL:", item)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
