"""生成几版候选图标，输出到 assets/candidates/ 并拼一张对比图。

用法::

    python tools/make_icons.py

选好之后把对应方案写进 assets/icon.ico（脚本最后一行会说明怎么改）。
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "candidates"

S = 512                       # 先画大的，最后缩放，边缘才干净
ACCENT = (47, 111, 237, 255)  # 主色
WHITE = (255, 255, 255, 255)
DARK = (24, 40, 84, 255)

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
]


def _font(size: int) -> ImageFont.FreeTypeFont | None:
    for path in FONT_CANDIDATES:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return None


def canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.23), fill=ACCENT)
    # 顶部淡高光
    hi = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(hi).rounded_rectangle(
        [0, 0, S - 1, int(S * 0.45)], radius=int(S * 0.23), fill=(255, 255, 255, 22)
    )
    return Image.alpha_composite(img, hi), ImageDraw.Draw(Image.alpha_composite(img, hi))


def fresh() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.23), fill=ACCENT)
    hi = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(hi).rounded_rectangle(
        [0, 0, S - 1, int(S * 0.45)], radius=int(S * 0.23), fill=(255, 255, 255, 22)
    )
    img = Image.alpha_composite(img, hi)
    return img, ImageDraw.Draw(img)


# --------------------------------------------------------------------------- #
# 基本图形
# --------------------------------------------------------------------------- #


def draw_note_pair(d, cx: float, cy: float, s: float, color=WHITE) -> None:
    """双音符（♫）：两个符头 + 两根符干 + 一根符杠。s 是整体宽度。"""
    rx, ry = s * 0.145, s * 0.105
    stem = max(2, int(s * 0.062))

    # 符头
    h1 = (cx - s * 0.20, cy + s * 0.26)
    h2 = (cx + s * 0.22, cy + s * 0.17)
    for hx, hy in (h1, h2):
        d.ellipse([hx - rx, hy - ry, hx + rx, hy + ry], fill=color)

    # 符干（从符头右侧往上）
    top1 = (h1[0] + rx * 0.86, h1[1] - s * 0.55)
    top2 = (h2[0] + rx * 0.86, h2[1] - s * 0.64)
    d.line([(h1[0] + rx * 0.86, h1[1]), top1], fill=color, width=stem)
    d.line([(h2[0] + rx * 0.86, h2[1]), top2], fill=color, width=stem)

    # 符杠
    t = stem * 1.5
    d.polygon([
        (top1[0] - t, top1[1]), (top2[0] + t, top2[1]),
        (top2[0] + t, top2[1] + t * 1.6), (top1[0] - t, top1[1] + t * 1.35),
    ], fill=color)


def draw_note_single(d, cx: float, cy: float, s: float, color=WHITE) -> None:
    """单音符（四分音符）。"""
    rx, ry = s * 0.20, s * 0.155
    stem = max(2, int(s * 0.085))
    hx, hy = cx, cy + s * 0.22
    d.ellipse([hx - rx, hy - ry, hx + rx, hy + ry], fill=color)
    d.line([(hx + rx * 0.82, hy), (hx + rx * 0.82, cy - s * 0.42)], fill=color, width=stem)


def draw_shackle(d, cx: float, cy: float, w: float, color=WHITE, width: int = 26,
                 raised: bool = False) -> None:
    """锁梁。raised=True 时右半边抬起来，表示"已打开"。"""
    r = w / 2
    box = [cx - r, cy - r, cx + r, cy + r]
    if not raised:
        d.arc(box, 180, 360, fill=color, width=width)
        return
    # 左半边固定，右边抬起
    d.arc(box, 180, 300, fill=color, width=width)
    angle = math.radians(-60)
    x0 = cx + r * math.cos(angle)
    y0 = cy + r * math.sin(angle)
    d.line([(x0, y0), (x0 + r * 1.05, y0 - r * 0.55)], fill=color, width=width)


def draw_wave(d, cx: float, cy: float, w: float, color=WHITE, bars: int = 7) -> None:
    """一排高低起伏的音频条。"""
    heights = [0.28, 0.55, 0.85, 1.0, 0.78, 0.48, 0.26]
    gap = w / (bars * 1.9)
    bar_w = gap * 0.95
    total = bars * gap + (bars - 1) * gap * 0.95
    x = cx - total / 2
    for i in range(bars):
        h = w * 0.5 * heights[i % len(heights)]
        d.rounded_rectangle(
            [x, cy - h, x + bar_w, cy + h], radius=bar_w / 2, fill=color
        )
        x += bar_w + gap * 0.95


def draw_arrow(d, x0, y0, x1, y1, color=WHITE, width=22, head=34) -> None:
    d.line([(x0, y0), (x1, y1)], fill=color, width=width)
    ang = math.atan2(y1 - y0, x1 - x0)
    for delta in (math.radians(150), math.radians(-150)):
        d.line(
            [(x1, y1), (x1 + head * math.cos(ang + delta), y1 + head * math.sin(ang + delta))],
            fill=color, width=width,
        )


def draw_dial(d, cx, cy, r, color=WHITE, width=18, ticks=12) -> None:
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=width)
    for i in range(ticks):
        a = math.radians(360 / ticks * i - 90)
        x0, y0 = cx + (r - width) * math.cos(a), cy + (r - width) * math.sin(a)
        x1, y1 = cx + (r - width * 2.6) * math.cos(a), cy + (r - width * 2.6) * math.sin(a)
        d.line([(x0, y0), (x1, y1)], fill=color, width=int(width * 0.55))


def draw_chain_break(d, cx, cy, r, color=WHITE, width=20) -> None:
    """左右两个断开的链环。"""
    gap = math.radians(58)
    for side in (-1, 1):
        cxx = cx + side * r * 0.95
        d.arc([cxx - r * 0.72, cy - r * 0.72, cxx + r * 0.72, cy + r * 0.72],
              math.degrees(gap) + (0 if side > 0 else 180),
              360 - math.degrees(gap) + (0 if side > 0 else 180),
              fill=color, width=width)


# --------------------------------------------------------------------------- #
# 各个方案
# --------------------------------------------------------------------------- #


def icon_lock_note() -> Image.Image:
    """打开的挂锁 + 锁身里一个音符。"""
    img, d = fresh()
    body = [S * 0.22, S * 0.47, S * 0.78, S * 0.84]
    # 锁梁：圆心偏上偏右，右下留缺口 = 已打开
    r = S * 0.185
    cx, cy = S * 0.50, S * 0.36
    d.arc([cx - r, cy - r, cx + r, cy + r], 168, 352, fill=WHITE, width=int(S * 0.062))
    d.rounded_rectangle(body, radius=int(S * 0.10), fill=WHITE)
    draw_note_single(d, S * 0.50, S * 0.665, S * 0.34, color=ACCENT)
    return img


def icon_note_arrows() -> Image.Image:
    """音符 + 两段带箭头的环弧（转换）。"""
    img, d = fresh()
    r = S * 0.355
    cx = cy = S * 0.5
    for start, end in ((135, 355), (315, 175)):
        d.arc([cx - r, cy - r, cx + r, cy + r], start, end, fill=WHITE, width=int(S * 0.052))
        a = math.radians(end)
        tx, ty = cx + r * math.cos(a), cy + r * math.sin(a)
        head = S * 0.115
        n = (math.cos(a - math.radians(90)), math.sin(a - math.radians(90)))
        u = (math.cos(a), math.sin(a))
        tip = (tx + n[0] * head * 1.35, ty + n[1] * head * 1.35)
        base = (tx + n[0] * head * 0.15, ty + n[1] * head * 0.15)
        d.polygon([
            tip,
            (base[0] + u[0] * head * 0.85, base[1] + u[1] * head * 0.85),
            (base[0] - u[0] * head * 0.85, base[1] - u[1] * head * 0.85),
        ], fill=WHITE)
    draw_note_pair(d, S * 0.50, S * 0.50, S * 0.34)
    return img


def icon_note_badge() -> Image.Image:
    """大音符 + 右下角小锁徽标。"""
    img, d = fresh()
    draw_note_pair(d, S * 0.47, S * 0.46, S * 0.50)
    # 徽标
    bx, by, br = S * 0.74, S * 0.755, S * 0.185
    d.ellipse([bx - br, by - br, bx + br, by + br], fill=WHITE)
    # 小锁
    lw, lh = br * 0.95, br * 0.78
    d.rounded_rectangle([bx - lw / 2, by - lh * 0.12, bx + lw / 2, by + lh * 0.62],
                        radius=br * 0.14, fill=ACCENT)
    rr = lw * 0.30
    d.arc([bx - rr, by - lh * 0.72, bx + rr, by + rr * 0.9],
          180, 360, fill=ACCENT, width=max(4, int(br * 0.17)))
    return img


def icon_note_down() -> Image.Image:
    """音符 + 向下箭头（导出成通用格式）。"""
    img, d = fresh()
    draw_note_pair(d, S * 0.50, S * 0.36, S * 0.42)
    ax = S * 0.50
    d.line([(ax, S * 0.58), (ax, S * 0.76)], fill=WHITE, width=int(S * 0.075))
    d.polygon([(ax - S * 0.105, S * 0.70), (ax + S * 0.105, S * 0.70),
               (ax, S * 0.84)], fill=WHITE)
    return img


def icon_note_keyhole() -> Image.Image:
    """音符的符头是个钥匙孔。"""
    img, d = fresh()
    d.ellipse([S * .17, S * .50, S * .57, S * .74], fill=WHITE)
    d.line([(S * .50, S * .60), (S * .50, S * .16)], fill=WHITE, width=int(S * 0.062))
    d.polygon([(S * .50, S * .16), (S * .78, S * .27), (S * .50, S * .36)], fill=WHITE)
    d.ellipse([S * .325, S * .565, S * .415, S * .655], fill=ACCENT)
    d.polygon([(S * .352, S * .635), (S * .388, S * .635),
               (S * .400, S * .695), (S * .340, S * .695)], fill=ACCENT)
    return img


CONCEPTS = [
    ("A", "打开的锁 + 音符", icon_lock_note),
    ("B", "音符 + 转换箭头", icon_note_arrows),
    ("C", "音符 + 小锁徽标", icon_note_badge),
    ("D", "音符 + 导出箭头", icon_note_down),
    ("E", "音符钥匙孔", icon_note_keyhole),
]


def contact_sheet(images: list[tuple[str, str, Image.Image]]) -> Image.Image:
    cell_w, cell_h = 300, 340
    cols = 3
    rows = (len(images) + cols - 1) // cols
    sheet = Image.new("RGB", (cell_w * cols, cell_h * rows), (246, 247, 249))
    d = ImageDraw.Draw(sheet)
    font = _font(22)
    small = _font(18)
    for i, (key, label, img) in enumerate(images):
        cx = (i % cols) * cell_w
        cy = (i // cols) * cell_h
        sheet.paste(img.resize((200, 200), Image.LANCZOS), (cx + 50, cy + 30), img.resize((200, 200), Image.LANCZOS))
        if font:
            d.text((cx + 50, cy + 245), f"{key}. {label}", font=font, fill=(30, 34, 40))
        if small:
            d.text((cx + 50, cy + 278), "32px 效果 →", font=small, fill=(130, 136, 145))
        thumb = img.resize((32, 32), Image.LANCZOS)
        sheet.paste(thumb, (cx + 170, cy + 272), thumb)
    return sheet


def main(argv: list[str] | None = None) -> int:
    import sys

    argv = list(sys.argv[1:] if argv is None else argv)
    apply_key = None
    if "--apply" in argv:
        apply_key = argv[argv.index("--apply") + 1].upper()

    OUT.mkdir(parents=True, exist_ok=True)
    built = []
    for key, label, fn in CONCEPTS:
        img = fn()
        path = OUT / f"{key}_{label.replace(' + ', '_').replace(' ', '')}.png"
        img.resize((256, 256), Image.LANCZOS).save(path)
        built.append((key, label, img))
        print(f"  {key}  {label}  ->  {path.name}")

    sheet = contact_sheet(built)
    sheet_path = ROOT / "assets" / "icon_candidates.png"
    sheet.save(sheet_path)
    print(f"\n对比图: {sheet_path}")

    if apply_key:
        chosen = next((item for item in built if item[0] == apply_key), None)
        if chosen is None:
            print(f"没有方案 {apply_key}")
            return 2
        img = chosen[2]
        icon = ROOT / "assets" / "icon.ico"
        img.resize((256, 256), Image.LANCZOS).save(
            icon, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                         (64, 64), (128, 128), (256, 256)])
        img.resize((256, 256), Image.LANCZOS).save(ROOT / "assets" / "icon.png")
        print(f"\n已应用方案 {apply_key}（{chosen[1]}） -> {icon}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
