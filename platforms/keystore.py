"""极小的 ekey 缓存。

musicex 文件的密钥不在文件里，只能从客户端拿一次。拿到之后存起来，
同一首歌再转就不用再开客户端了。

存在程序目录下的 ``keys.json``，键是 musicex 块里的 14 字符 id。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

_FILENAME = "keys.json"


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


_cached: dict[str, str] | None = None


def _path() -> Path:
    candidates = [_base_dir() / _FILENAME]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "MusicUnlock" / _FILENAME)
    candidates.append(Path(tempfile.gettempdir()) / f"MusicUnlock-{_FILENAME}")
    for candidate in candidates:
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            return candidate
        except OSError:
            continue
    return Path(tempfile.gettempdir()) / _FILENAME


def _load() -> dict[str, str]:
    global _cached
    if _cached is not None:
        return _cached
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
        _cached = {str(k): str(v) for k, v in raw.items() if v}
    except (OSError, json.JSONDecodeError, AttributeError):
        _cached = {}
    return _cached


def get(file_id: str) -> str | None:
    if not file_id:
        return None
    return _load().get(file_id)


def put(file_id: str, ekey: str) -> None:
    if not file_id or not ekey:
        return
    data = _load()
    if data.get(file_id) == ekey:
        return
    data[file_id] = ekey
    try:
        _path().write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    except OSError:
        pass


def count() -> int:
    return len(_load())


def path() -> Path:
    return _path()
