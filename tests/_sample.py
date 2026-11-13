"""测试样本的查找逻辑。

优先顺序：

1. 环境变量 ``MUSIC_UNLOCK_SAMPLE`` 指向的音频文件；
2. ``tests/fixtures/`` 下任意一个 FLAC / WAV / M4A；
3. 都没有就返回 None，调用方据此「跳过」而不是「失败」。

真实音乐样本不进仓库，所以找不到时测试应当跳过。
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"

_AUDIO_EXTS = {".flac", ".wav", ".m4a", ".alac", ".aiff", ".aif", ".mp3"}


def find_sample() -> Path | None:
    env = os.environ.get("MUSIC_UNLOCK_SAMPLE")
    if env:
        candidate = Path(env)
        if candidate.is_file():
            return candidate

    if FIXTURES.is_dir():
        for path in sorted(FIXTURES.iterdir()):
            if path.is_file() and path.suffix.lower() in _AUDIO_EXTS:
                return path

    return None
