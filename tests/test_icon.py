#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""内置图标(托盘/GUI/exe)生成回归测试

这个坑踩过两次: ICO 里 DIB 头多写 16 字节 -> 像素错位只剩几个杂点;
像素按 RGB 而不是 BGRA 写 -> 蓝色渲染成橙色。测试直接把字节拆开验像素。
"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import campus_net as cn

PASS = FAIL = 0
FAILED = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS - %s %s" % (name, detail))
    else:
        FAIL += 1
        FAILED.append(name)
        print("FAIL - %s %s" % (name, detail))


def entries(data):
    n = struct.unpack("<H", data[4:6])[0]
    out = []
    for i in range(n):
        off = 6 + i * 16
        w, h, _, _, _, bpp = struct.unpack("<BBBBHH", data[off:off + 8])
        size, offset = struct.unpack("<II", data[off + 8:off + 16])
        out.append((w or 256, h or 256, bpp, data[offset:offset + size]))
    return out


def pixels(dib, w, h):
    """解析 32bpp DIB -> [(r,g,b,a)] 行优先"""
    px = []
    stride = w * 4
    body = dib[40:40 + stride * h]
    for y in range(h):
        base = (h - 1 - y) * stride
        for x in range(w):
            b, g, r, a = body[base + x * 4: base + x * 4 + 4]
            px.append((r, g, b, a))
    return px


def main():
    data = cn._make_icon_bytes()
    check("ICO 魔数为 1(图标)", data[:4] == b"\x00\x00\x01\x00", repr(data[:4]))
    es = entries(data)
    check("包含 16/32 两档", all(any(e[0] == s for e in es) for s in (16, 32)),
          str([e[0] for e in es]))
    check("全部为 32bpp", all(e[2] == 32 for e in es), str([e[2] for e in es]))

    for w, h, bpp, dib in es:
        check("%dx%d DIB 头正好 40 字节" % (w, h),
              struct.unpack("<I", dib[:4])[0] == 40, str(struct.unpack("<I", dib[:4])[0]))
        px = pixels(dib, w, h)
        opaque = [p for p in px if p[3] > 20]
        ratio = len(opaque) / float(w * h)
        check("%dx%d 不透明像素占比 >60%%" % (w, h), ratio > 0.60, "%.0f%%" % (ratio * 100))
        blue = sum(1 for r, g, b, a in opaque if b > r + 30)
        white = sum(1 for r, g, b, a in opaque if r > 200 and g > 200 and b > 200)
        check("%dx%d 主色为蓝(不是橙色)" % (w, h), blue > len(opaque) * 0.5,
              "蓝 %d / 白 %d / 共 %d" % (blue, white, len(opaque)))
        check("%dx%d 有白色 WiFi 图形" % (w, h), white > len(opaque) * 0.03,
              "%.0f%%" % (100.0 * white / max(1, len(opaque))))

    # 圆角外必须透明(否则托盘里是个方块)
    w, h, _, dib = [e for e in es if e[0] == 32][0]
    px = pixels(dib, w, h)
    check("四角透明", px[0][3] == 0 and px[w - 1][3] == 0, str(px[0]))

    # 小尺寸可读性: 16px 不能是一团
    w, h, _, dib = [e for e in es if e[0] == 16][0]
    px = pixels(dib, w, h)
    white = sum(1 for r, g, b, a in px if a > 20 and r > 200 and g > 200 and b > 200)
    check("16px 白色像素数量合理(6~80)", 6 <= white <= 80, str(white))

    # 落盘的文件也要能被再次解析(供 GUI/NSIS 用)
    import tempfile
    p = os.path.join(tempfile.gettempdir(), "campusnet_icon_test.ico")
    cn.write_icon_file(p)
    with open(p, "rb") as f:
        disk = f.read()
    check("write_icon_file 写出合法 ICO", entries(disk)[0][0] == 16 and len(disk) > 5000,
          "%d bytes" % len(disk))

    print("-" * 50)
    print("总计 %d 项, 失败 %d 项" % (PASS + FAIL, FAIL))
    if FAILED:
        print("失败项: %s" % FAILED)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
