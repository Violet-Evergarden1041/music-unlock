"""QQ 音乐「借客户端取密钥」后端（Frida）。

2026 版客户端把 ekey 从文件里挪走了，改由服务端下发。所以首次处理某个
musicex 文件时，需要让客户端自己走一遍正常打开流程，我们趁机把密钥抓下来；
之后就是纯离线解密（QMCv2 的 RC4），跟播放进度无关。

设计要点
--------
* 只读：只挂 ``SetEkey`` 一个函数，不改任何指令，不 patch 内存逻辑；
* 不猜：窗口期抓到的每个候选密钥都拿去试解文件头，能解出已知容器魔数的才采用，
  这样即便客户端同时在放别的歌也不会认错；
* 拿不到就是拿不到：返回 None，由上层给出人话错误，绝不返回可疑密钥。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

MODULE_NAME = "QQMusicCommon.dll"
PROCESS_KEYWORD = "qqmusic"
CAPTURE_TIMEOUT = 30.0

#: ?SetEkey@EncAndDesMediaFile@@QAE_NPBDH@Z —— 客户端把每首歌的密钥
#: 交给解密对象时必经此处，签名是 (const char* ekey, int len)
_EKEY_SYMBOL = "?SetEkey@EncAndDesMediaFile@@QAE_NPBDH@Z"

_CAPTURE_JS = r"""
(function () {
  var mod = Process.getModuleByName(%(module)s);
  var aEkey = null;
  try { aEkey = mod.getExportByName(%(symbol)s); } catch (e) { aEkey = null; }

  var seen = {};

  if (aEkey) Interceptor.attach(aEkey, {
    onEnter: function (args) {
      var len = args[1].toInt32();
      if (len <= 0 || len > 8192) return;
      var s = null;
      try { s = args[0].readUtf8String(len); } catch (e) {}
      if (!s || seen[s]) return;
      seen[s] = 1;
      send({ tag: "ekey", ekey: s });
    }
  });

  rpc.exports = { ready: function () { return !!aEkey; } };
})();
"""


def _js_str(value: str) -> str:
    """把 Python 字符串转成 JS 字面量。"""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _find_process(frida_module):
    device = frida_module.get_local_device()
    matches = [
        p for p in device.enumerate_processes()
        if PROCESS_KEYWORD in p.name.lower()
    ]
    return device, (matches[0] if matches else None)


def available() -> bool:
    """frida 装了没有、客户端在不在跑。"""
    try:
        import frida
    except ImportError:
        return False
    try:
        _, process = _find_process(frida)
        return process is not None
    except Exception:
        return False


def describe() -> str:
    """给界面/日志用的一句话状态。"""
    try:
        import frida
    except ImportError:
        return "未安装 frida（pip install frida）"
    try:
        _, process = _find_process(frida)
    except Exception as exc:
        return f"frida 不可用：{exc}"
    if process is None:
        return "QQ 音乐没有运行"
    return f"可借用 {process.name} (PID {process.pid})"


def _key_matches(ekey: str, head: bytes, prefixes: tuple[bytes, ...]) -> bool:
    """拿候选密钥试解文件头——能解出已知容器魔数才算数。"""
    from . import qmc_algo

    try:
        key = qmc_algo.derive_key(ekey.encode("utf-8"))
    except ValueError:
        return False
    cipher = (
        qmc_algo.QmcRC4Cipher(key) if len(key) > 300 else qmc_algo.QmcMapCipher(key)
    )
    probe = bytearray(head)
    cipher.decrypt(probe, 0)
    return any(bytes(probe).startswith(prefix) for prefix in prefixes)


def capture_ekey(
    src: str | Path,
    timeout: float = CAPTURE_TIMEOUT,
    prefixes: tuple[bytes, ...] = (b"fLaC", b"OggS", b"ID3", b"ftyp"),
) -> str | None:
    """让客户端打开一次这个文件，把能解开它的那个 ekey 抓下来。

    返回 None 表示没抓到——上层应当据此给出提示，而不是硬着头皮继续。
    """
    src = Path(src)

    try:
        import frida
    except ImportError:
        return None

    try:
        head = src.read_bytes()[:16]
    except OSError:
        return None

    device, process = _find_process(frida)
    if process is None:
        return None

    session = device.attach(process.pid)
    found: list[str] = []

    def on_message(message, data):  # noqa: ARG001
        payload = message.get("payload") or {}
        ekey = payload.get("ekey")
        if payload.get("tag") == "ekey" and ekey:
            if not found and _key_matches(ekey, head, prefixes):
                found.append(ekey)

    try:
        script = session.create_script(
            _CAPTURE_JS % {
                "module": _js_str(MODULE_NAME),
                "symbol": _js_str(_EKEY_SYMBOL),
            }
        )
        script.on("message", on_message)
        script.load()
        if not script.exports_sync.ready():
            return None

        # 用系统关联打开文件，等于让 QQ 音乐自己走一遍正常打开流程
        try:
            os.startfile(str(src))  # noqa: S606
        except OSError:
            return None

        deadline = timeout
        while deadline > 0 and not found:
            threading.Event().wait(0.2)
            deadline -= 0.2
        return found[0] if found else None
    except Exception:
        return None
    finally:
        try:
            session.detach()
        except Exception:
            pass
