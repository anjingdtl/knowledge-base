"""深海风格纹理生成脚本

使用 Pillow 程序化生成 ShineHeKnowledge 所需的装饰纹理图片。
运行: python src/gui/resources/generate_textures.py
"""
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw

OUT_DIR = Path(__file__).resolve().parent / "textures"
OUT_DIR.mkdir(exist_ok=True)

# ---- 调色板 — 深海五色体系 ----
ABYSS = (2, 31, 46)
DEEP  = (4, 75, 102)
OCEAN = (9, 121, 158)
SKY   = (71, 169, 207)
ICE   = (166, 224, 244)
SURFACE_LIGHT = (240, 248, 252)   # ICE 衍生浅色
SURFACE_DARK  = (4, 75, 102)      # 同 DEEP


# ---- Perlin 噪声（纯 Python 实现） ----

def _fade(t: float) -> float:
    return t * t * t * (t * (t * 6 - 15) + 10)


def _lerp(a: float, b: float, t: float) -> float:
    return a + t * (b - a)


def _grad(hash_val: int, x: float, y: float) -> float:
    h = hash_val & 3
    u = x if h < 2 else y
    v = y if h < 2 else x
    return (u if h & 1 == 0 else -u) + (v if h & 2 == 0 else -v)


class PerlinNoise:
    """2D Perlin 噪声生成器"""

    def __init__(self, seed: int = 42):
        rng = random.Random(seed)
        self.p = list(range(256))
        rng.shuffle(self.p)
        self.p = self.p + self.p  # 双倍避免溢出

    def noise(self, x: float, y: float) -> float:
        xi = int(math.floor(x)) & 255
        yi = int(math.floor(y)) & 255
        xf = x - math.floor(x)
        yf = y - math.floor(y)
        u = _fade(xf)
        v = _fade(yf)
        aa = self.p[self.p[xi] + yi]
        ab = self.p[self.p[xi] + yi + 1]
        ba = self.p[self.p[xi + 1] + yi]
        bb = self.p[self.p[xi + 1] + yi + 1]
        return _lerp(
            _lerp(_grad(aa, xf, yf), _grad(ba, xf - 1, yf), u),
            _lerp(_grad(ab, xf, yf - 1), _grad(bb, xf - 1, yf - 1), u),
            v,
        )


def _clamp(v: int) -> int:
    return max(0, min(255, v))


# ---- 1. 侧边栏深海纹理 ----

def gen_sidebar_stone(base_rgb: tuple, filename: str, w: int = 220, h: int = 1200):
    """生成侧边栏深海底纹 — Perlin 噪声叠加水平层理"""
    img = Image.new("RGBA", (w, h), (*base_rgb, 255))
    pixels = img.load()
    if pixels is None:
        raise RuntimeError("Unable to access generated texture pixels")
    perlin = PerlinNoise(seed=42)

    for y in range(h):
        for x in range(w):
            n1 = perlin.noise(x * 0.03, y * 0.02) * 4
            n2 = perlin.noise(x * 0.08, y * 0.05) * 2
            layer = math.sin(y * 0.15) * 1.5
            noise_val = n1 + n2 + layer
            r = _clamp(base_rgb[0] + int(noise_val))
            g = _clamp(base_rgb[1] + int(noise_val))
            b = _clamp(base_rgb[2] + int(noise_val))
            pixels[x, y] = (r, g, b, 255)

    # 右边缘渐暗
    for y in range(h):
        for x in range(w - 20, w):
            alpha_factor = (x - (w - 20)) / 20
            darken = int(12 * alpha_factor)
            pixel = pixels[x, y]
            if not isinstance(pixel, tuple):
                continue
            r, g, b, a = pixel
            pixels[x, y] = (_clamp(r - darken), _clamp(g - darken), _clamp(b - darken), a)

    img.save(OUT_DIR / filename, "PNG")
    print(f"  >> {filename}")


# ---- 2. 品牌区深海装饰条 ----

def gen_brand_ornament(w: int = 220, h: int = 6):
    """品牌区分隔线装饰 — DEEP→SKY 渐变 + 菱形重复纹样"""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # DEEP → SKY 渐变底色
    for x in range(w):
        t = x / w
        r = int(DEEP[0] + (SKY[0] - DEEP[0]) * t)
        g = int(DEEP[1] + (SKY[1] - DEEP[1]) * t)
        b = int(DEEP[2] + (SKY[2] - DEEP[2]) * t)
        for y in range(h):
            center_fade = 1.0 - abs(y - h / 2) / (h / 2) * 0.5
            alpha = int(180 * center_fade)
            draw.point((x, y), fill=(r, g, b, alpha))

    # 重复菱形纹样
    diamond_spacing = 16
    for cx in range(8, w, diamond_spacing):
        cy = h // 2
        size = 2
        points = [(cx, cy - size), (cx + size, cy), (cx, cy + size), (cx - size, cy)]
        draw.polygon(points, fill=(*OCEAN, 220))

    # 上下极细描边线
    for x in range(w):
        draw.point((x, 0), fill=(*DEEP, 120))
        draw.point((x, h - 1), fill=(*DEEP, 120))

    img.save(OUT_DIR / "brand_ornament.png", "PNG")
    print("  >> brand_ornament.png")


# ---- 3. 几何纹样 ----

def gen_rune_pattern(w: int = 200, h: int = 200):
    """可平铺几何纹样 — 极淡深海线条"""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    line_color = (*OCEAN, 8)

    # 中心圆
    cx, cy = w // 2, h // 2
    r = 40
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=line_color, width=1)
    # 内圈
    r2 = 25
    draw.ellipse([cx - r2, cy - r2, cx + r2, cy + r2], outline=line_color, width=1)
    # 十字线
    draw.line([(cx - r - 10, cy), (cx + r + 10, cy)], fill=line_color, width=1)
    draw.line([(cx, cy - r - 10), (cx, cy + r + 10)], fill=line_color, width=1)
    # 对角线
    diag = int(r * 0.707)
    draw.line([(cx - diag, cy - diag), (cx + diag, cy + diag)], fill=line_color, width=1)
    draw.line([(cx + diag, cy - diag), (cx - diag, cy + diag)], fill=line_color, width=1)
    # 三角形
    tri_r = 50
    tri_pts = [
        (cx, cy - tri_r),
        (cx - int(tri_r * 0.866), cy + tri_r // 2),
        (cx + int(tri_r * 0.866), cy + tri_r // 2),
    ]
    draw.polygon(tri_pts, outline=line_color, width=1)

    # 角落小圆弧装饰
    corner_r = 15
    for ox, oy in [(0, 0), (w, 0), (0, h), (w, h)]:
        draw.arc([ox - corner_r, oy - corner_r, ox + corner_r, oy + corner_r], 0, 90, fill=line_color, width=1)

    # 极淡的短线装饰
    for i in range(0, w, 40):
        draw.line([(i, 0), (i + 8, 0)], fill=(*OCEAN, 5), width=1)
        draw.line([(i, h - 1), (i + 8, h - 1)], fill=(*OCEAN, 5), width=1)
    for j in range(0, h, 40):
        draw.line([(0, j), (0, j + 8)], fill=(*OCEAN, 5), width=1)
        draw.line([(w - 1, j), (w - 1, j + 8)], fill=(*OCEAN, 5), width=1)

    img.save(OUT_DIR / "rune_pattern.png", "PNG")
    print("  >> rune_pattern.png")


# ---- 4. 能量光晕 ----

def gen_energy_glow(size: int = 48):
    """碧空能量光晕 — 径向渐变"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    cx, cy = size // 2, size // 2
    max_r = size // 2

    for y in range(size):
        for x in range(size):
            dist = math.sqrt((x - cx) ** 2 + (y - cy) ** 2)
            if dist <= max_r:
                intensity = math.exp(-0.5 * (dist / (max_r * 0.4)) ** 2)
                alpha = int(80 * intensity)
                r = _clamp(int(ICE[0] * intensity + SKY[0] * (1 - intensity)))
                g = _clamp(int(ICE[1] * intensity + SKY[1] * (1 - intensity)))
                b = _clamp(int(ICE[2] * intensity + SKY[2] * (1 - intensity)))
                img.putpixel((x, y), (r, g, b, alpha))

    img.save(OUT_DIR / "energy_glow.png", "PNG")
    print("  >> energy_glow.png")


# ---- 5. 卡片表面纹理 ----

def gen_card_surface(base_rgb: tuple, filename: str, w: int = 100, h: int = 100):
    """卡片表面微纹理"""
    img = Image.new("RGBA", (w, h), (*base_rgb, 255))
    pixels = img.load()
    if pixels is None:
        raise RuntimeError("Unable to access generated texture pixels")
    perlin = PerlinNoise(seed=123)

    for y in range(h):
        for x in range(w):
            n = perlin.noise(x * 0.06, y * 0.06) * 2.5
            r = _clamp(base_rgb[0] + int(n))
            g = _clamp(base_rgb[1] + int(n))
            b = _clamp(base_rgb[2] + int(n))
            pixels[x, y] = (r, g, b, 255)

    img.save(OUT_DIR / filename, "PNG")
    print(f"  >> {filename}")


# ---- 6. 深海角饰 ----

def gen_gold_corners(size: int = 32):
    """品牌区四角 SKY L 形装饰角"""
    positions = {
        "gold_corner_tl": lambda s: [(8, 8), (8 + 16, 8), (8, 8 + 10)],
        "gold_corner_tr": lambda s: [(s - 8, 8), (s - 8 - 16, 8), (s - 8, 8 + 10)],
        "gold_corner_bl": lambda s: [(8, s - 8), (8 + 16, s - 8), (8, s - 8 - 10)],
        "gold_corner_br": lambda s: [(s - 8, s - 8), (s - 8 - 16, s - 8), (s - 8, s - 8 - 10)],
    }

    for name, get_lines in positions.items():
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        lines = get_lines(size)
        p0 = lines[0]
        p1 = lines[1]
        p2 = lines[2]
        draw.line([p0, p1], fill=(*SKY, 50), width=1)
        draw.line([p0, p2], fill=(*SKY, 50), width=1)
        draw.ellipse([p0[0] - 1, p0[1] - 1, p0[0] + 1, p0[1] + 1], fill=(*SKY, 70))
        img.save(OUT_DIR / f"{name}.png", "PNG")
        print(f"  >> {name}.png")


# ---- 7. 侧边栏右侧 SKY 竖线装饰 ----

def gen_sidebar_edge(w: int = 4, h: int = 1200):
    """侧边栏右边缘 SKY 光晕竖线"""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for y in range(h):
        for x in range(w):
            t = x / (w - 1)
            intensity = math.sin(t * math.pi)
            alpha = int(18 * intensity)
            img.putpixel((x, y), (*SKY, alpha))

    img.save(OUT_DIR / "sidebar_edge.png", "PNG")
    print("  >> sidebar_edge.png")


# ---- 主入口 ----

def main():
    print("[Deep Ocean] Generating textures...")
    OUT_DIR.mkdir(exist_ok=True)

    # 1. 侧边栏深海纹理
    print("\n[1/7] Sidebar ocean")
    gen_sidebar_stone(DEEP, "sidebar_stone.png")
    gen_sidebar_stone(ABYSS, "sidebar_stone_dark.png")

    # 2. 品牌区深海装饰条
    print("\n[2/7] Brand ornament")
    gen_brand_ornament()

    # 3. 几何纹样
    print("\n[3/7] Geometric pattern")
    gen_rune_pattern()

    # 4. 能量光晕
    print("\n[4/7] Energy glow")
    gen_energy_glow()

    # 5. 卡片表面纹理
    print("\n[5/7] Card surface")
    gen_card_surface(SURFACE_LIGHT, "card_surface_light.png")
    gen_card_surface(SURFACE_DARK, "card_surface_dark.png")

    # 6. 深海角饰
    print("\n[6/7] Ocean corners")
    gen_gold_corners()

    # 7. 侧边栏边缘装饰
    print("\n[7/7] Sidebar edge")
    gen_sidebar_edge()

    print(f"\nDone! Textures saved to {OUT_DIR}")


if __name__ == "__main__":
    main()
