"""生成应用图标 assets/icon_1024.png（品牌：渐变圆角方块 + 白色四角星，与 favicon 一致）。

用法：python tools/make_icon.py
"""
import os
from PIL import Image, ImageDraw

SIZE = 1024
C1 = (0x5B, 0x5B, 0xD6)  # #5b5bd6（渐变起，左上）
C2 = (0x7C, 0x6C, 0xF0)  # #7c6cf0（渐变止，右下）
RADIUS = 224              # 14/64 × 1024（与 favicon 的圆角比例一致）


def diagonal_gradient(size: int, c1, c2) -> Image.Image:
    """45° 对角线性渐变：横向渐变条 → 放大铺满正方形 → 旋转 45° → 中心裁切。"""
    length = size * 2
    ramp = Image.new("RGB", (length, 1))
    for i in range(length):
        t = i / (length - 1)
        ramp.putpixel((i, 0), tuple(int(c1[k] + (c2[k] - c1[k]) * t) for k in range(3)))
    sq = ramp.resize((length, length), Image.BILINEAR)  # 关键：先铺满，再旋转（否则只有一条斜线）
    big = sq.rotate(45, expand=True, resample=Image.BICUBIC)
    w, h = big.size
    left, top = (w - size) // 2, (h - size) // 2
    return big.crop((left, top, left + size, top + size))


def main() -> None:
    img = diagonal_gradient(SIZE, C1, C2).convert("RGBA")

    # 圆角蒙版：圆角外区域透明
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=RADIUS, fill=255)

    # 四角星（favicon SVG 路径 64 网格 ×16 放大，中心对齐）
    s = SIZE / 64
    star = [(32 * s, 11 * s), (37.2 * s, 26.8 * s), (53 * s, 32 * s), (37.2 * s, 37.2 * s),
            (32 * s, 53 * s), (26.8 * s, 37.2 * s), (11 * s, 32 * s), (26.8 * s, 26.8 * s)]
    ImageDraw.Draw(img).polygon(star, fill=(255, 255, 255, 255))

    out = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    os.makedirs("assets", exist_ok=True)
    out.save("assets/icon_1024.png")
    print("已生成 assets/icon_1024.png")


if __name__ == "__main__":
    main()
