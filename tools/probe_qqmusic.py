"""只读探测：列出 QQ 音乐客户端里可用的解密函数。

用途：客户端每个版本都可能改导出函数名，硬编码 mangled name 是这类工具
最常见的失效原因。这个脚本只做三件事——挂上去、枚举模块导出表、立刻 detach。
不读音频、不写文件、不调用任何解密函数。

用法::

    python tools/probe_qqmusic.py
"""

from __future__ import annotations

import json
import sys
import threading

import frida

TARGET_KEYWORD = "qqmusic"
TIMEOUT = 30

SCRIPT = r"""
(function () {
  var out = { arch: Process.arch, pointer_size: Process.pointerSize, modules: [],
              target: null, export_count: 0, matched: [], sample: [], error: null };

  var mods = null;
  try {
    mods = Process.enumerateModules();
  } catch (e) {
    out.error = "enumerateModules 失败: " + e;
    send(out);
    return;
  }

  for (var i = 0; i < mods.length; i++) {
    var m = mods[i];
    if (/QQMusic|Mde|Enc/i.test(m.name)) {
      out.modules.push({ name: m.name, size: m.size, base: m.base.toString() });
    }
  }

  var target = null;
  for (var j = 0; j < mods.length; j++) {
    if (/QQMusicCommon/i.test(mods[j].name)) { target = mods[j]; break; }
  }
  if (!target) {
    for (var k = 0; k < mods.length; k++) {
      if (/QQMusic/i.test(mods[k].name) && /\.dll$/i.test(mods[k].path)) { target = mods[k]; break; }
    }
  }
  if (!target) {
    out.error = "没找到 QQMusicCommon.dll，也未找到其它 QQMusic 相关 DLL";
    send(out);
    return;
  }

  out.target = { name: target.name, path: target.path, size: target.size };

  try {
    // Frida 17 起 enumerateExports 是 Module 实例方法，不再是静态方法
    var exps = target.enumerateExports();
    out.export_count = exps.length;
    for (var n = 0; n < exps.length; n++) {
      var e = exps[n];
      if (/EncAndDes|MediaFile|Decrypt|Encrypt|Mde|Crypt/i.test(e.name)) {
        out.matched.push({ name: e.name, type: e.type, address: e.address.toString() });
      }
    }
    var limit = Math.min(exps.length, 15);
    for (var s = 0; s < limit; s++) out.sample.push(exps[s].name);
  } catch (err) {
    out.error = "enumerateExports 失败: " + err;
  }

  send(out);
})();
"""


def main() -> int:
    device = frida.get_local_device()
    processes = device.enumerate_processes()
    matches = [p for p in processes if TARGET_KEYWORD in p.name.lower()]
    if not matches:
        print("没有找到正在运行的 QQ 音乐，请先启动客户端。")
        return 2

    process = matches[0]
    print(f"目标进程: {process.name} (PID {process.pid})")

    session = device.attach(process.pid)
    print("已附加，开始枚举（只读）…")

    done = threading.Event()
    result: dict = {}

    def on_message(message, data):  # noqa: ARG001
        if message.get("type") == "send":
            result.update(message["payload"])
        else:
            result["error"] = message.get("description") or str(message)
        done.set()

    script = session.create_script(SCRIPT)
    script.on("message", on_message)
    script.load()

    if not done.wait(TIMEOUT):
        print(f"超时：{TIMEOUT} 秒内没拿到结果")
        session.detach()
        return 3

    script.unload()
    session.detach()
    print("已 detach。\n")

    if result.get("error"):
        print("错误:", result["error"])

    print(f"目标架构: {result.get('arch')} / {result.get('pointer_size') * 8} 位")
    print(f"相关模块 ({len(result.get('modules') or [])}):")
    for module in result.get("modules") or []:
        print(f"  - {module['name']}  {module['size'] // 1024} KB")

    target = result.get("target")
    if target:
        print(f"\n解密器所在模块: {target['name']}")
        print(f"  {target['path']}")
        print(f"  导出函数总数: {result.get('export_count')}")

    matched = result.get("matched") or []
    print(f"\n匹配到的疑似解密函数 ({len(matched)}):")
    for item in matched:
        print(f"  [{item['type']}] {item['address']}  {item['name']}")

    if result.get("sample"):
        print("\n导出表前几个（用于确认命名风格）:")
        for name in result["sample"]:
            print("  ", name)

    out_file = f"qqmusic_exports_{process.pid}.json"
    with open(out_file, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(f"\n完整结果已写入 {out_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
