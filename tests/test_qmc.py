"""QQ 音乐解密验收测试。

真值来源：unlock-music 的 testdata 夹具（真实文件的密文 + 解密后明文）。
夹具不在本仓库内（`.gitignore` 掉了 `_ref/`），缺失时自动跳过。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from platforms import qmc_algo  # noqa: E402
from platforms.qqmusic import QmcDecoder  # noqa: E402

FIXTURE_CANDIDATES = [
    ROOT / "tests" / "fixtures" / "qmc",
    ROOT / "_ref" / "try_22a53f" / "testdata",
]

FULL_FILE_CASES = ["mflac0_rc4", "mflac_rc4", "mflac_map", "mgg_map", "qmc0_static"]
PASSPHRASE_CASES = ["mflac_map", "mgg_map", "mflac_rc4", "mflac0_rc4"]

failures: list[str] = []
checks = 0


def fixture_dir() -> Path | None:
    for cand in FIXTURE_CANDIDATES:
        if cand.is_dir() and (cand / "qmc0_static_raw.bin").is_file():
            return cand
    return None


def check(name: str, cond: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if cond:
        print(f"[ OK ] {name}")
    else:
        print(f"[FAIL] {name}  {detail}")
        failures.append(name)


def test_cipher_vectors() -> None:
    """静态表 / map 表的固定向量，锁住算法不被改坏。"""
    c = qmc_algo.QmcStaticCipher()

    buf = bytearray(16)
    c.decrypt(buf, 0x7FF8)
    check(
        "static cipher @0x7ff8",
        bytes(buf) == bytes([
            0xD8, 0x52, 0xF7, 0x67, 0x90, 0xCA, 0xD6, 0x4A,
            0x4A, 0xD6, 0xCA, 0x90, 0x67, 0xF7, 0x52, 0xD8,
        ]),
        bytes(buf).hex(),
    )

    buf = bytearray(16)
    c.decrypt(buf, 0)
    check(
        "static cipher @0",
        bytes(buf) == bytes([
            0xC3, 0x4A, 0xD6, 0xCA, 0x90, 0x67, 0xF7, 0x52,
            0xD8, 0xA1, 0x66, 0x62, 0x9F, 0x5B, 0x09, 0x00,
        ]),
        bytes(buf).hex(),
    )

    key = bytes(range(256))
    buf = bytearray(16)
    qmc_algo.QmcMapCipher(key).decrypt(buf, 0)
    check(
        "map cipher mask",
        bytes(buf) == bytes([
            0xBB, 0x7D, 0x80, 0xBE, 0xFF, 0x38, 0x81, 0xFB,
            0xBB, 0xFF, 0x82, 0x3C, 0xFF, 0xBA, 0x83, 0x79,
        ]),
        bytes(buf).hex(),
    )


def test_key_derivation(root: Path) -> None:
    for name in PASSPHRASE_CASES:
        raw = (root / f"{name}_key_raw.bin").read_bytes()
        want = (root / f"{name}_key.bin").read_bytes()
        try:
            got = qmc_algo.derive_key(raw)
        except Exception as exc:
            check(f"derive_key {name}", False, f"异常 {exc}")
            continue
        check(f"derive_key {name}", got == want, f"{got[:12].hex()} != {want[:12].hex()}")


def test_full_files(root: Path) -> None:
    for name in FULL_FILE_CASES:
        body = (root / f"{name}_raw.bin").read_bytes()
        suffix = (root / f"{name}_suffix.bin").read_bytes()
        want = (root / f"{name}_target.bin").read_bytes()
        blob = body + suffix
        try:
            got = QmcDecoder(blob).decrypt()
        except Exception as exc:
            check(f"full file {name}", False, f"异常 {exc}")
            continue
        if got == want:
            check(f"full file {name}", True)
        else:
            diff = next(
                (i for i, (a, b) in enumerate(zip(got, want)) if a != b), None
            )
            check(f"full file {name}", False, f"长度 {len(got)}/{len(want)} 首个差异@{diff}")


def main() -> int:
    print("=== 密码学固定向量 ===")
    test_cipher_vectors()

    root = fixture_dir()
    if root is None:
        print("\n[SKIP] 没有找到 testdata 夹具，跳过真实文件测试")
        print("       把 unlock-music 的 testdata 放到 tests/fixtures/qmc 即可启用")
    else:
        print(f"\n=== 真实文件（夹具目录：{root}）===")
        test_key_derivation(root)
        test_full_files(root)

    print(f"\n共 {checks} 项检查，失败 {len(failures)} 项")
    if failures:
        for f in failures:
            print("  FAIL:", f)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
