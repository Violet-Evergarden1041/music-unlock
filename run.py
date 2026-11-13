"""程序入口。图形界面与命令行共用一套核心。

打包成 ``--windowed`` 之后有一堆坑，这里统一处理：

* ``sys.stdout`` 可能是 ``None``，命令行模式得自己开控制台；
* 从控制台启动时 stdout 是 GBK 码页，写 ``✔`` 这类字符会直接抛异常；
* 日志目录可能没权限（未签名的 exe 容易被系统/杀软拦），所以要有多级兜底。

任何一处失败都只能降级，绝不能因此崩掉整个程序。
"""

from __future__ import annotations

import multiprocessing
import os
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FROZEN = getattr(sys, "frozen", False)


# --------------------------------------------------------------------------- #
# 日志：优先放 exe 旁边，找不到可写位置就退到临时目录
# --------------------------------------------------------------------------- #


def _log_candidates() -> list[Path]:
    items: list[Path] = []
    if FROZEN:
        items.append(Path(sys.executable).resolve().parent / "MusicUnlock.log")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        items.append(Path(local) / "MusicUnlock" / "run.log")
    items.append(Path(tempfile.gettempdir()) / "MusicUnlock.log")
    return items


_cached_log: Path | None = None


def log_path() -> Path:
    """返回第一个可写的日志路径。绝不抛异常。"""
    global _cached_log
    if _cached_log is not None:
        return _cached_log
    for candidate in _log_candidates():
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            with open(candidate, "a", encoding="utf-8"):
                pass
            _cached_log = candidate
            return candidate
        except OSError:
            continue
    _cached_log = Path(os.devnull)
    return _cached_log


def trace(message: str) -> None:
    """打包后的故障排查日志。源码运行时什么都不做。"""
    if not FROZEN:
        return
    try:
        with open(log_path(), "a", encoding="utf-8") as handle:
            handle.write(message.rstrip() + "\n")
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# 标准输出：保证能写、不乱码、不崩
# --------------------------------------------------------------------------- #


def _prepare_stdio() -> None:
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            if sys.stdout is None:
                kernel32.AllocConsole()
            # 控制台按 UTF-8 渲染，否则中文是乱码、符号会编码失败
            kernel32.SetConsoleOutputCP(65001)
            kernel32.SetConsoleCP(65001)
        except Exception:
            trace("准备控制台失败：\n" + traceback.format_exc())

    if sys.stdout is None:
        try:
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
            sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        except OSError:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")
            sys.stderr = sys.stdout

    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main() -> int:
    multiprocessing.freeze_support()  # PyInstaller 打包后必须
    args = sys.argv[1:]
    trace(f"启动 argv={sys.argv!r}")

    if args and args[0] in ("-c", "--cli"):
        _prepare_stdio()
        try:
            from app.cli import main as cli_main

            return cli_main(args[1:])
        except Exception:
            detail = traceback.format_exc()
            trace("命令行模式异常：\n" + detail)
            print("启动失败，详情见 " + str(log_path()), file=sys.stderr)
            print(detail, file=sys.stderr)
            return 1

    try:
        from app.gui import main as gui_main

        return gui_main()
    except Exception:
        detail = traceback.format_exc()
        trace("图形界面异常：\n" + detail)
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox

            app = QApplication.instance() or QApplication(sys.argv)
            QMessageBox.critical(
                None, "启动失败",
                f"程序无法启动。\n\n{detail[-1200:]}\n\n日志：{log_path()}",
            )
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
