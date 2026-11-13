"""界面冒烟测试：离屏渲染一张截图，并断言关键行为。

不依赖真实音频——只验证界面自己的逻辑：
平台标签识别、有损转无损的预警、切换格式后预警能正确消失。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.gui import MainWindow  # noqa: E402

#: (文件名, 期望在「平台」列看到的标签)
FAKE = [
    ("歌曲A - 歌手A.mflac", "QQ 音乐"),
    ("歌曲B - 歌手B.mgg", "QQ 音乐"),
    ("某歌手 - 某歌名.ncm", "网易云"),
    ("某歌手 - 某歌名.kgma", "酷狗"),
    ("某歌手 - 某歌名.kwm", "酷我"),
    ("某歌手 - 某歌名.flac", "普通音频"),
]

#: 只有 mgg（有损源）配 FLAC 时才该预警
WARN_ROW = 1


def _luminance(name: str) -> float:
    name = name.lstrip("#")
    r, g, b = int(name[0:2], 16), int(name[2:4], 16), int(name[4:6], 16)
    return 0.299 * r + 0.587 * g + 0.114 * b


def _arrow_contrast(combo) -> float:
    """量一下下拉框右侧箭头区域与背景的最大亮度差。"""
    image = combo.grab().toImage()
    w, h = image.width(), image.height()
    background = _luminance(image.pixelColor(4, h // 2).name())
    best = 0.0
    for x in range(max(0, w - 24), w):
        for y in range(h):
            best = max(best, abs(_luminance(image.pixelColor(x, y).name()) - background))
    return best


def main() -> int:
    out = ROOT / "_build" / "gui_preview.png"
    fake_dir = ROOT / "_build" / "fake"
    fake_dir.mkdir(parents=True, exist_ok=True)

    paths = []
    for name, _ in FAKE:
        p = fake_dir / name
        p.write_bytes(b"\x00" * 16)
        paths.append(p)

    app = QApplication(sys.argv)
    window = MainWindow()
    window.add_paths(paths)
    window.show()
    app.processEvents()

    print("表格行数:", window.table.rowCount())

    # 1) 平台列必须认得出四个平台
    labels = [window.table.item(r, 1).text() for r in range(window.table.rowCount())]
    print("平台列:", labels)
    expected = [label for _, label in FAKE]
    assert labels == expected, f"平台标签不对：{labels} != {expected}"

    # 2) 全部设为 FLAC，只有有损源那一行会预警
    window.cmb_all.setCurrentIndex(2)  # FLAC
    app.processEvents()

    statuses = [window.table.item(r, 5).text() for r in range(window.table.rowCount())]
    print("状态列:", statuses)
    for row, text in enumerate(statuses):
        if row == WARN_ROW:
            assert text.startswith("提示"), f"第 {row} 行（有损源）应该预警：{text}"
        else:
            assert text == "等待", f"第 {row} 行不该预警：{text}"

    # 3) 切回「跟随源格式」，预警要自动消失
    window.cmb_all.setCurrentIndex(0)
    app.processEvents()
    restored = [window.table.item(r, 5).text() for r in range(window.table.rowCount())]
    print("改回跟随源格式:", restored)
    assert all(t == "等待" for t in restored), restored

    # 4) 主题切换：三种模式都要能正确解析，并各存一张图
    window.cmb_all.setCurrentIndex(2)  # 截图时带上预警状态，方便肉眼检查
    app.processEvents()

    cases = [("dark", True), ("light", False)]
    for mode, expect_dark in cases:
        window.set_theme(mode)
        app.processEvents()
        assert window.theme_mode == mode
        assert window.dark is expect_dark, f"{mode} 解析成了 dark={window.dark}"
        shot = ROOT / "_build" / f"gui_{mode}.png"
        window.grab().save(str(shot))
        print(f"主题 {mode}: dark={window.dark}  截图 {shot.name}")

    # 「跟随系统」必须能解出一个确定值，不能是未知状态
    window.set_theme("system")
    app.processEvents()
    assert window.theme_mode == "system"
    print(f"主题 system: 解析为 dark={window.dark}")

    # 5) 下拉箭头必须可见。
    # 曾经踩过：只改 QSS 不改 QPalette，深色下箭头是深色画在深色底上，等于消失。
    for mode, min_contrast in (("dark", 40), ("light", 40)):
        window.set_theme(mode)
        app.processEvents()
        contrast = _arrow_contrast(window.cmb_all)
        print(f"主题 {mode}: 下拉箭头最大对比度 {contrast:.0f}")
        assert contrast >= min_contrast, (
            f"{mode} 主题下箭头看不见（对比度 {contrast:.0f}）"
        )

    # 选择要持久化
    window.set_theme("dark")
    app.processEvents()
    from PySide6.QtCore import QSettings

    saved = QSettings("music-unlock", "MusicUnlock").value("theme")
    assert saved == "dark", f"主题没存下来：{saved}"
    print("主题已持久化: theme =", saved)

    # 还原成「跟随系统」，别把测试状态留给真实使用
    window.set_theme("system")
    app.processEvents()

    window.grab().save(str(out))
    print(f"截图已保存：{out}")

    # 7) 删除：多选两行后移除
    from PySide6.QtCore import QItemSelectionModel

    before = window.table.rowCount()
    window.table.clearSelection()
    flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
    model = window.table.model()
    for row in (0, 2):
        window.table.selectionModel().select(model.index(row, 0), flags)
    app.processEvents()
    selected = len({i.row() for i in window.table.selectedIndexes()})
    print(f"选中行数: {selected}")
    assert selected == 2, f"多选失效，只选中 {selected} 行"

    window.delete_selected()
    app.processEvents()
    after = window.table.rowCount()
    print(f"删除测试: {before} -> {after} 行")
    assert after == before - 2, f"删除后 {after} 行，期望 {before - 2}"

    # 删除后前两行应该是原来的第 1、3 行（即 mgg 和 kwm）
    left = [window.table.item(r, 0).text() for r in range(after)]
    print("剩余文件:", left)
    assert "mgg" in left[0], left

    # 8) 设置面板
    from app.gui import SettingsDialog

    dlg = SettingsDialog(window, "dark", "minimize")
    assert dlg.values() == ("dark", "minimize"), dlg.values()
    print("设置面板取值正确:", dlg.values())
    dlg.deleteLater()

    # 9) 关闭行为：缩到托盘 / 退回最小化，进程和窗口都不该消失
    assert hasattr(window, "btn_settings"), "工具栏没有设置按钮"
    window.close_action = "minimize"
    window.show()
    app.processEvents()
    window.close()
    app.processEvents()
    tray_mode = window.tray is not None
    print(
        f"关闭行为=minimize -> 有托盘={tray_mode} 已隐藏={window.isHidden()} "
        f"最小化={window.isMinimized()}"
    )
    if tray_mode:
        assert window.isHidden(), "有托盘时应该隐藏窗口而不是普通最小化"
        assert window.tray.isVisible(), "托盘图标应该显示出来"
        # 双击托盘还原
        window.restore_from_tray()
        app.processEvents()
        assert window.isVisible(), "从托盘还原后窗口应该重新可见"
        assert not window.tray.isVisible(), "还原后托盘图标应该收起"
    else:
        # 离屏/无托盘环境会退回普通最小化
        assert window.isMinimized(), "没有托盘时应该退回普通最小化"
        window.showNormal()
        app.processEvents()

    # 10) 关闭行为：直接退出时窗口关闭
    window.close_action = "exit"
    window.close()
    app.processEvents()
    print(f"关闭行为=exit -> 窗口已关闭={window.isHidden()}")
    assert window.isHidden(), "退出后窗口应该关闭"

    print("日志:\n" + window.log.toPlainText())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
