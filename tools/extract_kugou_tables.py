"""从参考实现的 `kgm.hpp` 里提取酷狗解密用的三张表，生成 Python 模块。

手抄 272 个十六进制数极易出错，直接解析 C++ 源更可靠。
只在开发时用一次，产物是 `platforms/_kugou_tables.py`。

用法::

    python tools/extract_kugou_tables.py <path/to/kgm.hpp>
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "platforms" / "_kugou_tables.py"

ARRAYS = {
    "VPR_HEADER": "VprHeader",
    "KGM_HEADER": "KgmHeader",
    "VPR_MASK_DIFF": "VprMaskDiff",
    "TABLE1": "table1",
    "TABLE2": "table2",
    "MASK_V2_PREDEF": "MaskV2PreDef",
}

_TOKEN = re.compile(r"0x[0-9A-Fa-f]{2}")


def extract(source: str, cpp_name: str) -> list[int]:
    pattern = re.compile(
        r"std::vector<uint8_t>\s+" + re.escape(cpp_name) + r"\s*=\s*\{(.*?)\};",
        re.S,
    )
    match = pattern.search(source)
    if not match:
        raise SystemExit(f"没找到数组 {cpp_name}")
    return [int(token, 16) for token in _TOKEN.findall(match.group(1))]


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    source = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")

    lines = [
        '"""酷狗 kgm / vpr 解密表。',
        "",
        "本文件由 `tools/extract_kugou_tables.py` 从 unlock-music 的",
        "`src/KgmWasm/kgm.hpp` 自动生成，请勿手工编辑。",
        "",
        "版权：MIT License, Copyright (c) 2019-2023 MengYX",
        '"""',
        "",
        "from __future__ import annotations",
        "",
    ]

    for py_name, cpp_name in ARRAYS.items():
        values = extract(source, cpp_name)
        expected = 17 if py_name == "VPR_MASK_DIFF" else (16 if "HEADER" in py_name else 272)
        marker = "" if len(values) == expected else f"  # 注意：实际 {len(values)} 字节"
        print(f"{py_name:<16} {len(values):>4} 字节{marker}")
        lines.append(f"{py_name}: bytes = bytes([")
        for start in range(0, len(values), 16):
            chunk = ", ".join(f"0x{v:02X}" for v in values[start:start + 16])
            lines.append(f"    {chunk},")
        lines.append("])")
        lines.append("")

    text = "\n".join(lines).rstrip() + "\n"
    OUT.write_text(text, encoding="utf-8")
    print(f"\n已生成 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
