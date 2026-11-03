# -*- coding: utf-8 -*-
"""生成 app_icon.ico —— 纯标准库手写渲染(无 PIL)。

设计: 圆角方形蓝紫渐变底 + 纯白 WiFi 弧线(小尺寸自动简化为 2 弧, 保证 16px 可读)。
每档尺寸独立 4x 超采样后降采样 -> 自带抗锯齿。
"""
import math
import os
import struct
import zlib

OUT_ICO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_icon.ico")
OUT_PNG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "icon_preview.png")

SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]

# 品牌色(Fluent 蓝)
C1 = (76, 141, 255)    # 顶部
C2 = (22, 78, 208)     # 底部
WHITE = (255, 255, 255)


def _lerp(a, b, t):
    return a + (b - a) * t


def render(size):
    """返回 size*size 的 RGBA 像素列表(行优先, 上到下)"""
    ss = 4
    M = size * ss
    inv = 1.0 / M

    # 几何参数(归一化)
    inset = 0.045
    radius = 0.215
    lo, hi = inset, 1.0 - inset
    cx, cy = 0.5, 0.5
    half = (hi - lo) / 2.0

    apex_y = 0.735
    if size >= 64:
        dot_r = 0.060
        radii = [0.135, 0.243, 0.351]
        widths = [0.062, 0.068, 0.074]
        cone = 55.0
    elif size >= 24:
        # 24~40px: 三根弧会糊在一起, 减到两根
        dot_r = 0.068
        radii = [0.205, 0.392]
        widths = [0.086, 0.096]
        cone = 49.0
    else:
        # 16~20px: 只留一根粗弧 + 大圆点, 保证一眼认出是 WiFi
        dot_r = 0.108
        radii = [0.318]
        widths = [0.130]
        cone = 47.0
    # 每根弧两端加半圆端帽, 避免被角度截断成一刀切的直角
    caps = [(cx + r * math.sin(math.radians(cone)), apex_y - r * math.cos(math.radians(cone)), w)
            for r, w in zip(radii, widths)]
    caps += [(cx - r * math.sin(math.radians(cone)), apex_y - r * math.cos(math.radians(cone)), w)
             for r, w in zip(radii, widths)]

    # 背景浮点缓冲
    buf = [[0.0, 0.0, 0.0, 0.0] for _ in range(M * M)]

    for py in range(M):
        y = (py + 0.5) * inv
        for px in range(M):
            x = (px + 0.5) * inv

            # 圆角矩形 SDF
            qx = abs(x - cx) - (half - radius)
            qy = abs(y - cy) - (half - radius)
            dx = max(qx, 0.0)
            dy = max(qy, 0.0)
            sd = math.hypot(dx, dy) + min(max(qx, qy), 0.0) - radius
            if sd > 0.0:
                continue  # 圆角外: 全透明

            # 渐变: 对角 + 左上柔光
            t = (x * 0.45 + y * 0.85)
            t = min(max(t, 0.0), 1.0)
            r = _lerp(C1[0], C2[0], t)
            g = _lerp(C1[1], C2[1], t)
            b = _lerp(C1[2], C2[2], t)
            glare = max(0.0, 1.0 - math.hypot(x - 0.22, y - 0.16) / 0.85)
            k = 0.16 * glare * glare
            r += (255 - r) * k
            g += (255 - g) * k
            b += (255 - b) * k

            # WiFi 图形(白色)
            wx, wy = x - cx, y - apex_y
            dist = math.hypot(wx, wy)
            white = 0.0
            if dist <= dot_r:
                white = 1.0
            elif wy < 0.0:
                ang = math.degrees(math.atan2(abs(wx), -wy))
                if ang <= cone:
                    for rr, ww in zip(radii, widths):
                        if abs(dist - rr) <= ww / 2.0:
                            white = 1.0
                            break
                if white == 0.0:
                    for ex, ey, ew in caps:
                        if (x - ex) ** 2 + (y - ey) ** 2 <= (ew / 2.0) ** 2:
                            white = 1.0
                            break
            if white > 0.0:
                r = r + (WHITE[0] - r) * white
                g = g + (WHITE[1] - g) * white
                b = b + (WHITE[2] - b) * white

            buf[py * M + px] = [r, g, b, 255.0]

    # 4x4 盒式降采样
    out = []
    for oy in range(size):
        for ox in range(size):
            sr = sg = sb = sa = 0.0
            for yy in range(ss):
                base = (oy * ss + yy) * M + ox * ss
                for xx in range(ss):
                    p = buf[base + xx]
                    sr += p[0]
                    sg += p[1]
                    sb += p[2]
                    sa += p[3]
            n = ss * ss
            a = sa / n
            if a <= 0.5:
                out.append((0, 0, 0, 0))
            else:
                out.append((int(sr / n + 0.5), int(sg / n + 0.5), int(sb / n + 0.5), int(a + 0.5)))
    return out


def bmp_entry(pixels, size):
    """ICO 内的 32bpp BMP(DIB) 数据, 含 AND 掩码"""
    hdr = struct.pack("<IiiHHIIiiII",
                      40, size, size * 2, 1, 32, 0,
                      size * size * 4, 0, 0, 0, 0)
    body = bytearray()
    for y in range(size - 1, -1, -1):          # BMP 自下而上
        for x in range(size):
            r, g, b, a = pixels[y * size + x]
            body += bytes((b, g, r, a))
    mask_row = ((size + 31) // 32) * 4          # 1bpp 每行 4 字节对齐
    mask = bytes(mask_row * size)
    return hdr + bytes(body) + mask


def png_bytes(pixels, size):
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        for x in range(size):
            r, g, b, a = pixels[y * size + x]
            raw += bytes((r, g, b, a))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def zom_png(pixels, size, factor, path):
    """把 size 的像素放大 factor 倍(最近邻), 用来肉眼检查小尺寸是否可读"""
    out = size * factor
    big = [(0, 0, 0, 0)] * (out * out)
    for y in range(out):
        for x in range(out):
            big[y * out + x] = pixels[(y // factor) * size + (x // factor)]
    with open(path, "wb") as f:
        f.write(png_bytes(big, out))


def main():
    images = {}
    for s in SIZES:
        images[s] = render(s)
        print("渲染 %dx%d ok" % (s, s))

    entries = []
    for s in SIZES:
        data = bmp_entry(images[s], s)
        entries.append((s, data))

    head = struct.pack("<HHH", 0, 1, len(entries))
    dir_bytes = b""
    offset = 6 + 16 * len(entries)
    for s, data in entries:
        dir_bytes += struct.pack("<BBBBHHII",
                                 s if s < 256 else 0, s if s < 256 else 0,
                                 0, 0, 1, 32, len(data), offset)
        offset += len(data)

    with open(OUT_ICO, "wb") as f:
        f.write(head + dir_bytes + b"".join(d for _, d in entries))
    print("写出", OUT_ICO, os.path.getsize(OUT_ICO), "bytes")

    with open(OUT_PNG, "wb") as f:
        f.write(png_bytes(images[256], 256))
    print("写出预览", OUT_PNG)

    small = os.path.join(os.path.dirname(OUT_PNG), "icon_preview_16.png")
    zom_png(images[16], 16, 12, small)
    print("写出 16px 放大预览", small)


if __name__ == "__main__":
    main()
