"""监听 QQ 音乐自己的解密动作。

思路：不去猜密钥，也不自己调 Open/Read，而是让客户端正常播放一首加密歌曲，
把它内部真正用到的「设置密钥 → 解密 → 读出明文」全过程拦下来。

跑起来之后，请立刻在 QQ 音乐里播放一首加密的歌（例如《海阔天空》），
播放 10 秒左右即可。脚本会在 60 秒后自动汇总。

用法::

    python tools/hook_play.py
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import frida

CAPTURE_SECONDS = 60
PROCESS_KEYWORD = "qqmusic"

JS = r"""
(function () {
  var mod = Process.getModuleByName("QQMusicCommon.dll");
  function sym(n) {
    try { return mod.getExportByName(n); } catch (e) { return null; }
  }

  var aRead  = sym("?Read@EncAndDesMediaFile@@QAEKPAEK_J@Z");
  var aOpen  = sym("?Open@EncAndDesMediaFile@@QAE_NPB_W_N1@Z");
  var aEkey  = sym("?SetEkey@EncAndDesMediaFile@@QAE_NPBDH@Z");
  var aDec   = sym("?DecryptData@qqmusic@@YAHHPADH@Z");
  var aCache = sym("?DecryptCacheFile@qqmusic@@YAHAAV?$CStringT@_WV?$StrTraitATL@_WV?$ChTraitsCRT@_W@ATL@@@ATL@@@ATL@@0@Z");
  var aKey   = sym("?GetKeyForDecrypt@qqmusic@@YAPADPBD@Z");

  function hex(ptr, n) {
    try { return Array.from(new Uint8Array(ptr.readByteArray(n))); } catch (e) { return null; }
  }

  // ---- 打开文件：记录路径 ----
  if (aOpen) Interceptor.attach(aOpen, {
    onEnter: function (args) {
      var p = null;
      try { p = args[0].readUtf16String(); } catch (e) {}
      send({ tag: "open", path: p });
    },
    onLeave: function (ret) { send({ tag: "open_ret", ret: ret.toInt32() }); }
  });

  // ---- 设置密钥：这里能直接拿到密钥字符串 ----
  if (aEkey) Interceptor.attach(aEkey, {
    onEnter: function (args) {
      var s = null;
      try { s = args[0].readUtf8String(); } catch (e) {
        try { s = args[0].readCString(); } catch (e2) {}
      }
      send({ tag: "set_ekey", ekey: s, len: args[1].toInt32() });
    }
  });

  // ---- 读明文：记录参数，并把读出来的字节带出来 ----
  if (aRead) Interceptor.attach(aRead, {
    onEnter: function (args) {
      this.buf = args[0];
      this.len = args[1].toInt32();
      this.pos = args[2].toString();
    },
    onLeave: function (ret) {
      var got = ret.toInt32();
      if (got > 0) {
        send({ tag: "read", pos: this.pos, want: this.len, got: got },
             this.buf.readByteArray(Math.min(got, 256)));
      }
    }
  });

  // ---- 通用解密函数 ----
  if (aDec) Interceptor.attach(aDec, {
    onEnter: function (args) {
      this.a = args[0].toInt32();
      this.buf = args[1];
      this.n = args[2].toInt32();
      send({ tag: "decrypt_data", a: this.a, n: this.n });
    },
    onLeave: function (ret) {
      if (ret.toInt32() > 0) send({ tag: "decrypt_data_done", n: this.n }, this.buf.readByteArray(Math.min(this.n, 128)));
    }
  });

  if (aKey) Interceptor.attach(aKey, {
    onEnter: function (args) {
      try { send({ tag: "get_key_in", s: args[0].readCString() }); } catch (e) {}
    },
    onLeave: function (ret) {
      var s = null;
      try { if (!ret.isNull()) s = ret.readCString(); } catch (e) {}
      send({ tag: "get_key_out", s: s });
    }
  });

  send({ tag: "ready", hooks: {
    open: !!aOpen, set_ekey: !!aEkey, read: !!aRead,
    decrypt_data: !!aDec, decrypt_cache: !!aCache, get_key: !!aKey
  }});
})();
"""


def main() -> int:
    device = frida.get_local_device()
    matches = [p for p in device.enumerate_processes()
               if PROCESS_KEYWORD in p.name.lower()]
    if not matches:
        print("QQ 音乐没有运行，请先打开它。")
        return 2

    process = matches[0]
    print(f"附加到 {process.name} (PID {process.pid})")

    session = device.attach(process.pid)
    events: list[dict] = []
    plain_chunks: list[tuple[str, bytes]] = []
    ready = threading.Event()

    def on_message(message, data):
        if message.get("type") == "error":
            print("脚本报错:", message.get("description"))
            return
        payload = message.get("payload") or {}
        tag = payload.get("tag")
        if tag == "ready":
            print("钩子就绪:", payload.get("hooks"))
            ready.set()
            return
        if tag == "read" and data:
            plain_chunks.append((payload.get("pos"), bytes(data)))
            print(f"  [read] pos={payload.get('pos')} 要={payload.get('want')} "
                  f"得到={payload.get('got')} 前16={bytes(data)[:16].hex()}")
            return
        if tag == "set_ekey":
            print(f"  [ekey] {payload.get('ekey')!r} len={payload.get('len')}")
        elif tag == "open":
            print(f"  [open] {payload.get('path')}")
        elif tag in ("decrypt_data", "decrypt_data_done", "get_key_in", "get_key_out"):
            print(f"  [{tag}] {json.dumps(payload, ensure_ascii=False)}")
        events.append(payload)

    script = session.create_script(JS)
    script.on("message", on_message)
    script.load()

    if not ready.wait(10):
        print("钩子没有就绪，可能模块或函数名变了。")
        session.detach()
        return 3

    print()
    print("=" * 62)
    print("  现在请去 QQ 音乐里播放一首加密歌曲（比如《海阔天空》）")
    print(f"  我会监听 {CAPTURE_SECONDS} 秒。")
    print("=" * 62)
    print()

    deadline = time.time() + CAPTURE_SECONDS
    while time.time() < deadline:
        time.sleep(1)

    try:
        script.unload()
    except Exception:
        pass
    session.detach()

    print()
    print(f"捕获结束：{len(events)} 条事件，{len(plain_chunks)} 次成功读取")
    if plain_chunks:
        out = Path("_build") / "captured_plain.bin"
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "wb") as handle:
            for _, chunk in sorted(plain_chunks, key=lambda item: int(item[0] or 0)):
                handle.write(chunk)
        print(f"明文片段已写入 {out}（{out.stat().st_size} 字节）")
        print("前 4 字节:", out.read_bytes()[:4])
    elif events:
        print("有调用但没读到数据，看上面的事件日志。")
    else:
        print("一条事件都没抓到——播放时用的可能不是这套函数。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
