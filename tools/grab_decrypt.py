"""抓取一次真实的「客户端解密」过程，把 ekey 和明文都存到磁盘。

用法::

    python tools/grab_decrypt.py "C:\\path\\to\\song.mflac"

会调用系统关联打开这个文件（= 让 QQ 音乐播放它），同时监听
SetEkey / Read，把密钥和读出的明文片段写入 _build/。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import frida

DURATION = 40

JS = r"""
(function () {
  var mod = Process.getModuleByName("QQMusicCommon.dll");
  function sym(n) { try { return mod.getExportByName(n); } catch (e) { return null; } }

  var aRead = sym("?Read@EncAndDesMediaFile@@QAEKPAEK_J@Z");
  var aEkey = sym("?SetEkey@EncAndDesMediaFile@@QAE_NPBDH@Z");
  var aSeek = sym("?Seek@EncAndDesMediaFile@@QAE_N_JK@Z");

  if (aEkey) Interceptor.attach(aEkey, {
    onEnter: function (args) {
      var len = args[1].toInt32();
      var s = null;
      try { s = args[0].readUtf8String(len); } catch (e) {
        try { s = args[0].readCString(); } catch (e2) {}
      }
      send({ tag: "ekey", ekey: s, len: len });
    }
  });

  if (aSeek) Interceptor.attach(aSeek, {
    onEnter: function (args) { send({ tag: "seek", pos: args[0].toString() }); }
  });

  if (aRead) Interceptor.attach(aRead, {
    onEnter: function (args) {
      this.buf = args[0];
      this.len = args[1].toInt32();
      this.pos = args[2].toString();
    },
    onLeave: function (ret) {
      var got = ret.toInt32();
      if (got > 0) {
        send({ tag: "read", pos: this.pos, got: got }, this.buf.readByteArray(got));
      }
    }
  });

  send({ tag: "ready", hooks: { read: !!aRead, ekey: !!aEkey, seek: !!aSeek } });
})();
"""


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    target = Path(sys.argv[1]).resolve()
    if not target.is_file():
        print(f"文件不存在: {target}")
        return 2

    device = frida.get_local_device()
    matches = [p for p in device.enumerate_processes() if "qqmusic" in p.name.lower()]
    if not matches:
        print("QQ 音乐没在运行")
        return 2

    session = device.attach(matches[0].pid)
    ekey: str | None = None
    chunks: list[bytes] = []
    ready = threading.Event()

    def on_message(message, data):
        nonlocal ekey
        if message.get("type") == "error":
            print("脚本错误:", message.get("description"))
            return
        payload = message.get("payload") or {}
        tag = payload.get("tag")
        if tag == "ready":
            print("钩子就绪:", payload.get("hooks"))
            ready.set()
        elif tag == "ekey":
            ekey = payload.get("ekey")
            print(f"抓到 ekey: {len(ekey or '')} 字节")
        elif tag == "read" and data:
            chunks.append(bytes(data))

    script = session.create_script(JS)
    script.on("message", on_message)
    script.load()
    if not ready.wait(10):
        print("钩子没就绪")
        session.detach()
        return 3

    out_dir = Path("_build")
    out_dir.mkdir(exist_ok=True)

    print("请求系统打开文件，让 QQ 音乐播放它 …")
    os.startfile(str(target))  # noqa: S606

    print(f"监听 {DURATION} 秒（请让它播一会儿）…")
    time.sleep(DURATION)

    try:
        script.unload()
    except Exception:
        pass
    session.detach()

    if ekey:
        (out_dir / "ekey.txt").write_text(ekey, encoding="utf-8")
        print(f"ekey 已保存: _build/ekey.txt ({len(ekey)} 字节)")

    if chunks:
        blob = b"".join(chunks)
        (out_dir / "plain_capture.bin").write_bytes(blob)
        print(f"明文已保存: _build/plain_capture.bin ({len(blob):,} 字节)")
        print("前 8 字节:", blob[:8].hex(), blob[:4])
    else:
        print("没抓到明文")

    (out_dir / "capture_meta.json").write_text(
        json.dumps({"target": str(target), "ekey_len": len(ekey or ""),
                    "chunks": len(chunks), "bytes": sum(len(c) for c in chunks)},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
