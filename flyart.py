"""桌面苍蝇的程序化美术 (纯 Python, 不 import tkinter / ctypes / PIL)。

设计要点
--------
1. 不用第三方库, 也不用外部图片: 这里自己写一个"解析式覆盖"的小光栅器——
   每个形状给出有符号距离场 (SDF), 逐像素算覆盖率, 边缘天然抗锯齿。比"超采样再降采样"
   的像素级做法快得多, 质量也更好 (只有真正覆盖到的包围盒才计算)。
2. 内部缓冲是 **预乘 alpha 的 BGRA** (Windows UpdateLayeredWindow 要求的格式),
   合成用预乘空间的 source-over: out = src + dst * (1 - a), 全程整数运算。
3. 帧是"按角度直接画出来"的, 不是把一张图旋转采样出来的 —— 没有重采样毛边。
4. 所有帧都能缓存 (内存 + 磁盘), 运行期每帧只做一次 memmove, Python 不在帧循环里算像素。

对外只有三个函数:
    render_fly(angle_idx, pose, wing_phase, size, scale) -> bytes
    render_egg(size, scale)                             -> bytes
    render_swatter(size, scale)                         -> bytes
"""

from __future__ import annotations

import math
from typing import Callable, Optional, Sequence, Tuple

# ---------------------------------------------------------------- 常量
ART_VERSION = 2                  # 改了美术就 +1, 用来让磁盘帧缓存自动失效
ANGLES = 12                      # 朝向量化档数 (每档 30 度)
WING_PHASES = 4                  # 飞行时振翅相位
POSES = ("crawl_a", "crawl_b", "fly")

# 苍蝇本体在"局部坐标"里的尺度 (单位 px, scale=1.0 时)
FLY_FORWARD = -1.0               # 局部坐标里 -Y 是正前方

# 配色 (偏真实的"金属暗灰苍蝇"; 颜色都带一点冷色, 避免死黑)
C_SHADOW = (0, 0, 0)
C_ABDOMEN_DARK = (22, 24, 29)
C_ABDOMEN_LIGHT = (62, 68, 80)
C_THORAX_DARK = (34, 38, 47)
C_THORAX_LIGHT = (104, 118, 140)
C_IRIDESCENT = (58, 104, 112)          # 一点点蓝绿金属反光
C_HEAD = (28, 30, 37)
C_EYE = (104, 30, 38)
C_HILIGHT = (188, 208, 228)
C_LEG = (24, 25, 30)
C_LEG_HI = (112, 118, 130)
C_WING = (148, 168, 188)
C_WING_VEIN = (104, 120, 136)
C_EGG = (246, 245, 240)
C_EGG_SHADE = (206, 204, 196)

# 预乘 BGRA 缓冲
class Canvas:
    """预乘 alpha 的 BGRA 画布。"""

    __slots__ = ("w", "h", "buf")

    def __init__(self, w: int, h: int):
        self.w = int(w)
        self.h = int(h)
        self.buf = bytearray(self.w * self.h * 4)

    # ---- 合成基元 ----
    def _put(self, i: int, r: float, g: float, b: float, a: float) -> None:
        """预乘 source-over。a/r/g/b 都是 0..255 的浮点。"""
        buf = self.buf
        inv = 1.0 - a / 255.0
        buf[i] = int(buf[i] * inv + b * (a / 255.0) + 0.5)
        buf[i + 1] = int(buf[i + 1] * inv + g * (a / 255.0) + 0.5)
        buf[i + 2] = int(buf[i + 2] * inv + r * (a / 255.0) + 0.5)
        buf[i + 3] = int(buf[i + 3] * inv + a + 0.5)

    def _erase(self, i: int, a: float) -> None:
        """按覆盖率擦掉 alpha (打孔用)。"""
        keep = 1.0 - a / 255.0
        buf = self.buf
        buf[i] = int(buf[i] * keep + 0.5)
        buf[i + 1] = int(buf[i + 1] * keep + 0.5)
        buf[i + 2] = int(buf[i + 2] * keep + 0.5)
        buf[i + 3] = int(buf[i + 3] * keep + 0.5)

    def clear(self) -> None:
        self.buf = bytearray(self.w * self.h * 4)

    def tobytes(self) -> bytes:
        return bytes(self.buf)

    def unpremultiplied(self) -> bytearray:
        """还原成直通 alpha 的 BGRA (只给预览 PNG 用)。"""
        out = bytearray(self.buf)
        for i in range(0, len(out), 4):
            a = out[i + 3]
            if a == 0:
                out[i] = out[i + 1] = out[i + 2] = 0
            elif a != 255:
                f = 255.0 / a
                out[i] = min(255, int(out[i] * f + 0.5))
                out[i + 1] = min(255, int(out[i + 1] * f + 0.5))
                out[i + 2] = min(255, int(out[i + 2] * f + 0.5))
        return out


# ---------------------------------------------------------------- 距离场
def _ellipse_sdf(cx: float, cy: float, rx: float, ry: float, rot: float = 0.0):
    """旋转椭圆的近似有符号距离 (px, 内部为负)。"""
    ca, sa = math.cos(rot), math.sin(rot)
    base = min(rx, ry)

    def sdf(x: float, y: float) -> float:
        dx = x - cx
        dy = y - cy
        u = dx * ca + dy * sa
        v = -dx * sa + dy * ca
        k = math.hypot(u / rx, v / ry)
        return (k - 1.0) * base
    sdf.bbox = (cx - (abs(rx * ca) + abs(ry * sa)), cy - (abs(rx * sa) + abs(ry * ca)),
                cx + (abs(rx * ca) + abs(ry * sa)), cy + (abs(rx * sa) + abs(ry * ca)))
    return sdf


def _capsule_sdf(p0, p1, r0: float, r1: float):
    """带锥度的胶囊 (线段 + 两端半径), 用于腿和翅脉。"""
    vx = p1[0] - p0[0]
    vy = p1[1] - p0[1]
    l2 = vx * vx + vy * vy

    def sdf(x: float, y: float) -> float:
        if l2 <= 1e-9:
            t = 0.0
        else:
            t = ((x - p0[0]) * vx + (y - p0[1]) * vy) / l2
            if t < 0.0:
                t = 0.0
            elif t > 1.0:
                t = 1.0
        dx = x - (p0[0] + vx * t)
        dy = y - (p0[1] + vy * t)
        return math.sqrt(dx * dx + dy * dy) - (r0 + (r1 - r0) * t)
    r = max(r0, r1)
    sdf.bbox = (min(p0[0], p1[0]) - r, min(p0[1], p1[1]) - r,
                max(p0[0], p1[0]) + r, max(p0[1], p1[1]) + r)
    return sdf


def _union(*sdfs):
    """多个距离场取并集 (min); 对外仍是"距离"。"""
    def sdf(x: float, y: float) -> float:
        d = sdfs[0](x, y)
        for f in sdfs[1:]:
            d2 = f(x, y)
            if d2 < d:
                d = d2
        return d
    x0 = min(f.bbox[0] for f in sdfs)
    y0 = min(f.bbox[1] for f in sdfs)
    x1 = max(f.bbox[2] for f in sdfs)
    y1 = max(f.bbox[3] for f in sdfs)
    sdf.bbox = (x0, y0, x1, y1)
    return sdf


def _rotate(pts: Sequence[Tuple[float, float]], deg: float):
    """绕原点旋转一组点 (角度制)。"""
    ca, sa = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [(x * ca - y * sa, x * sa + y * ca) for (x, y) in pts]


# ---------------------------------------------------------------- 绘制
def _coverage(d: float, soft: float = 1.0) -> float:
    """距离 -> 覆盖率(0..1)。soft 是过渡带宽度(px), 越大越柔(阴影用)。"""
    t = 0.5 - d / soft
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return t


def draw(canvas: Canvas, sdf, color, alpha: float = 255.0,
         soft: float = 1.0, shade: Optional[Callable] = None,
         mode: str = "over", ctx: Optional[Tuple[float, float, float]] = None) -> None:
    """把形状画到画布上。

    sdf    距离场 (局部坐标)
    color  (r,g,b) 或 callable(x, y, d) -> (r,g,b)
    alpha  形状的最大不透明度
    soft   边缘过渡宽度 px (阴影/柔光用大值)
    shade  额外的透明度调制 callable(x, y, d) -> 0..1
    mode   "over" 正常合成 / "erase" 打孔
    ctx    (ox, oy, scale) 把局部坐标映射到画布
    """
    ox, oy, sc = ctx or (0.0, 0.0, 1.0)
    bx0, by0, bx1, by1 = sdf.bbox
    # 局部包围盒 -> 画布像素范围
    x0 = int(math.floor(bx0 * sc + ox)) - 1
    y0 = int(math.floor(by0 * sc + oy)) - 1
    x1 = int(math.ceil(bx1 * sc + ox)) + 1
    y1 = int(math.ceil(by1 * sc + oy)) + 1
    if x0 < 0:
        x0 = 0
    if y0 < 0:
        y0 = 0
    if x1 > canvas.w - 1:
        x1 = canvas.w - 1
    if y1 > canvas.h - 1:
        y1 = canvas.h - 1
    if x1 < x0 or y1 < y0:
        return
    buf = canvas.buf
    is_call = callable(color)
    w = canvas.w
    inv_sc = 1.0 / sc
    for y in range(y0, y1 + 1):
        ly = (y + 0.5 - oy) * inv_sc
        row = y * w
        for x in range(x0, x1 + 1):
            lx = (x + 0.5 - ox) * inv_sc
            d = sdf(lx, ly)
            if d >= soft * 0.5:
                continue
            cov = _coverage(d, soft)
            if cov <= 0.0:
                continue
            a = alpha * cov
            if shade is not None:
                a *= shade(lx, ly, d)
                if a <= 0.0:
                    continue
            i = (row + x) * 4
            if mode == "erase":
                canvas._erase(i, a)
                continue
            if is_call:
                r, g, b = color(lx, ly, d)
            else:
                r, g, b = color
            canvas._put(i, r, g, b, a)


# ---------------------------------------------------------------- 苍蝇形状
def _xform_sdf(local, ox: float, oy: float, ang_deg: float,
               mirror: bool = False):
    """把一个"局部"距离场摆到苍蝇局部坐标系里: 先绕原点转 ang, 再镜像, 再平移。"""
    ca = math.cos(math.radians(ang_deg))
    sa = math.sin(math.radians(ang_deg))

    def wrapped(x: float, y: float) -> float:
        dx = x - ox
        dy = y - oy
        u = dx * ca + dy * sa
        v = -dx * sa + dy * ca
        if mirror:
            u = -u
        return local(u, v)

    b = local.bbox
    corners = [(b[0], b[1]), (b[2], b[1]), (b[0], b[3]), (b[2], b[3])]
    pts = []
    for (u, v) in corners:
        if mirror:
            u = -u
        pts.append((ox + u * ca - v * sa, oy + u * sa + v * ca))
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    wrapped.bbox = (min(xs), min(ys), max(xs), max(ys))
    return wrapped


def _wing_local(length: float, width: float):
    """翅膀本体 (局部: 沿 +X 展开, 翅根在原点) —— 根部细、外缘圆的泪滴。"""
    blade = _ellipse_sdf(length * 0.56, 0.0, length * 0.46, width, 0.0)
    root = _capsule_sdf((0.0, 0.0), (length * 0.28, 0.0), width * 0.40, width * 0.8)
    return _union(blade, root)


def _wing_vein_shapes(length: float, width: float):
    """翅脉 (局部坐标, 由翅根向外发散)。"""
    tips = [(length * 0.98, -width * 0.18),
            (length * 0.96, width * 0.30),
            (length * 0.66, -width * 1.02),
            (length * 0.60, width * 0.92)]
    widths = [0.30, 0.28, 0.22, 0.22]
    return [_capsule_sdf((length * 0.10, 0.0), t, w, 0.14)
            for t, w in zip(tips, widths)]


def _leg(parts: Sequence[Tuple[float, float]], widths: Sequence[float]):
    segs = []
    for k in range(len(parts) - 1):
        segs.append(_capsule_sdf(parts[k], parts[k + 1], widths[k], widths[k + 1]))
    return _union(*segs)


# 腿的三种姿态: 每条腿是"根 -> 膝 -> 尖"三段折线 (x 带符号为左右)
_LEG_SHAPE = {
    "front": [(3.4, -8.0), (8.6, -11.0), (13.4, -14.6)],
    "mid": [(5.6, -4.6), (11.0, -3.2), (15.4, -2.4)],
    "hind": [(5.2, 1.8), (10.6, 6.0), (14.2, 11.8)],
}
# 走位: 三足步态, 同组的三条腿向前挪一点, 另一组向后
_GAIT_FWD = {"crawl_a": {"front": +1.0, "mid": +1.0, "hind": +1.0},
             "crawl_b": {"front": -1.0, "mid": -1.0, "hind": -1.0}}
_LEG_SHIFT = 1.6                 # 挪动量
_TUCK = 0.42                     # 飞行时腿收起来的比例


def _leg_points(kind: str, side: float, pose: str, pair_side: int):
    base = [(x * side, y) for (x, y) in _LEG_SHAPE[kind]]
    if pose == "fly":
        # 收腿: 整体往身体后方/内侧收
        out = []
        for idx, (x, y) in enumerate(base):
            k = _TUCK + 0.28 * idx
            out.append((x * (1.0 - k * 0.55), y * (1.0 - k * 0.28) + 1.6 * k))
        return out
    dir_ = _GAIT_FWD[pose][kind] * _LEG_SHIFT * pair_side
    out = [base[0]]
    for idx in (1, 2):
        k = 0.55 + 0.45 * idx
        out.append((base[idx][0], base[idx][1] - dir_ * k))
    return out


def _draw_fly(canvas: Canvas, angle_deg: float, pose: str, wing_phase: int,
              size: int, scale: float) -> None:
    """按"朝向角度 + 姿态 + 振翅相位"直接画出来 (角度制, 0 = 头朝上)。"""
    c = size / 2.0
    ctx = (c, c, scale)

    def rot(pts):
        return _rotate(pts, angle_deg)

    # ---- 影子 (先画, 在被身体盖住的位置) ----
    draw(canvas, _ellipse_sdf(1.2, 3.0, 7.6, 11.6, 0.0), C_SHADOW,
         alpha=58, soft=8.0, ctx=ctx)

    flying = (pose == "fly")
    # 振翅: 相位 -> 展开系数与角度偏移
    if flying:
        flap = (1.0, 0.74, 0.40, 0.74)[wing_phase % WING_PHASES]
    else:
        flap = 1.0

    # ---- 翅膀 ----
    # 局部坐标里 +X 是"展翅方向", 所以 tilt 越大越往后; 左翅用 mirror 镜像。
    for side in (1.0, -1.0):
        if flying:
            length = 13.6 * (0.74 + 0.26 * flap)
            width = 5.4 * (0.44 + 0.56 * flap)
            tilt = 19.0 + 9.0 * (1.0 - flap)
            root_y = -4.4
            alpha = int(92 + 52 * (1.0 - flap))
        else:
            # 停 / 爬: 真苍蝇会把翅膀折起来平贴在背上 (这里就是"向后收拢")
            length, width, tilt, root_y, alpha = 12.6, 3.1, 73.0, -1.6, 126
        body = _xform_sdf(_wing_local(length, width), side * 1.7, root_y,
                          tilt, mirror=(side < 0))
        draw(canvas, _rotate_sdf(body, angle_deg), C_WING,
             alpha=alpha, soft=1.1, ctx=ctx)
        for vein in _wing_vein_shapes(length, width):
            v = _xform_sdf(vein, side * 1.7, root_y, tilt, mirror=(side < 0))
            draw(canvas, _rotate_sdf(v, angle_deg), C_WING_VEIN,
                 alpha=46, soft=0.9, ctx=ctx)

    # ---- 腿 (画在身体下面一点, 先画) ----
    for kind in ("hind", "mid", "front"):
        for side in (-1.0, 1.0):
            for pair_side in (1, -1):
                # pair_side 决定这条腿在步态里是"前一步"还是"后一步"
                pts = rot(_leg_points(kind, side, pose, pair_side))
                widths = (1.85, 1.35, 0.75)
                seg = _leg(pts, widths)
                draw(canvas, seg, C_LEG, alpha=255, soft=1.0, ctx=ctx)
                # 腿的受光面: 整体往左上错开一点画一条更细的亮线 (统一光源方向)
                hi = _leg([(p[0] - 0.42, p[1] - 0.42) for p in pts],
                          (0.50, 0.38, 0.22))
                draw(canvas, hi, C_LEG_HI, alpha=150, soft=0.7, ctx=ctx)

    # ---- 腹部 ----
    abd = rot([(0.0, 5.6)])[0]
    draw(canvas, _ellipse_sdf(abd[0], abd[1], 6.5, 9.2, math.radians(angle_deg)),
         lambda x, y, d: _mix(C_ABDOMEN_DARK, C_ABDOMEN_LIGHT,
                              _clamp01(0.5 - (x - abd[0]) / 13.0)),
         alpha=255, soft=1.2, ctx=ctx)
    # 腹部节纹
    for k in (1, 2, 3):
        yy = abd[1] - 2.6 + k * 2.6
        p0 = rot([(-6.0, yy)])[0]
        p1 = rot([(6.0, yy)])[0]
        draw(canvas, _capsule_sdf(p0, p1, 0.55, 0.55), C_ABDOMEN_DARK,
             alpha=96, soft=0.8, ctx=ctx)
    # 腹部高光
    hl = rot([(-1.8, 2.6)])[0]
    draw(canvas, _ellipse_sdf(hl[0], hl[1], 1.9, 3.6, math.radians(angle_deg)),
         C_HILIGHT, alpha=118, soft=2.2, ctx=ctx)

    # ---- 胸部 ----
    th = rot([(0.0, -5.0)])[0]

    def _thorax_color(x, y, d):
        base = _mix(C_THORAX_DARK, C_THORAX_LIGHT,
                    _clamp01(0.45 - (y - th[1]) / 11.0))
        # 左上角一点点蓝绿金属反光, 苍蝇的"金属感"主要来自这里
        return _mix(base, C_IRIDESCENT,
                    _clamp01(0.55 - (x - th[0] + 1.2) / 9.0) * 0.5)

    draw(canvas, _ellipse_sdf(th[0], th[1], 6.0, 6.4, math.radians(angle_deg)),
         _thorax_color, alpha=255, soft=1.2, ctx=ctx)
    # 胸部刚毛 (几根短线, 缩小后就是"毛茸茸"的质感)
    for (bx, by, ex, ey) in ((2.4, -7.6, 4.6, -9.6), (-2.4, -7.6, -4.6, -9.6),
                             (4.2, -5.4, 6.6, -6.0), (-4.2, -5.4, -6.6, -6.0),
                             (0.0, -9.2, 0.0, -11.4)):
        p0 = rot([(bx, by)])[0]
        p1 = rot([(ex, ey)])[0]
        draw(canvas, _capsule_sdf(p0, p1, 0.34, 0.18), C_THORAX_LIGHT,
             alpha=170, soft=0.7, ctx=ctx)
    # 胸部高光 (金属反光)
    hl2 = rot([(-1.6, -6.6)])[0]
    draw(canvas, _ellipse_sdf(hl2[0], hl2[1], 1.6, 2.4, math.radians(angle_deg)),
         C_HILIGHT, alpha=140, soft=1.8, ctx=ctx)

    # ---- 头 + 复眼 + 触角 ----
    hd = rot([(0.0, -11.6)])[0]
    draw(canvas, _ellipse_sdf(hd[0], hd[1], 4.5, 3.9, math.radians(angle_deg)),
         C_HEAD, alpha=255, soft=1.0, ctx=ctx)
    for side in (-1.0, 1.0):
        e = rot([(side * 2.9, -12.2)])[0]
        draw(canvas, _ellipse_sdf(e[0], e[1], 2.3, 1.9, math.radians(angle_deg)),
             C_EYE, alpha=232, soft=0.8, ctx=ctx)
        e2 = rot([(side * 3.4, -12.8)])[0]
        draw(canvas, _ellipse_sdf(e2[0], e2[1], 0.7, 0.6, 0.0),
             C_HILIGHT, alpha=150, soft=0.6, ctx=ctx)
        a0 = rot([(side * 1.5, -14.6)])[0]
        a1 = rot([(side * 3.6, -17.6)])[0]
        draw(canvas, _capsule_sdf(a0, a1, 0.42, 0.20), C_LEG,
             alpha=225, soft=0.8, ctx=ctx)


def _rotate_sdf(sdf, deg: float):
    """把一个形状顺时针旋转 deg 度 (y 轴向下, 所以顺时针 = 数学正角度)。"""
    ca, sa = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    b = sdf.bbox
    corners = [(b[0], b[1]), (b[2], b[1]), (b[0], b[3]), (b[2], b[3])]
    rc = [(x * ca - y * sa, x * sa + y * ca) for (x, y) in corners]
    xs = [p[0] for p in rc]
    ys = [p[1] for p in rc]

    def wrapped(x: float, y: float) -> float:
        return sdf(x * ca + y * sa, -x * sa + y * ca)
    wrapped.bbox = (min(xs), min(ys), max(xs), max(ys))
    return wrapped


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def _mix(c1, c2, t: float):
    return (c1[0] + (c2[0] - c1[0]) * t,
            c1[1] + (c2[1] - c1[1]) * t,
            c1[2] + (c2[2] - c1[2]) * t)


def _hit_disc(canvas: Canvas, radius: float) -> None:
    """隐形的命中圆盘 (alpha=1): 逐像素 alpha 命中测试里"看不见但点得到"。"""
    if radius <= 1.0:
        return
    c = canvas.w / 2.0
    draw(canvas, _ellipse_sdf(c, c, radius, radius, 0.0),
         (0, 0, 0), alpha=1.0, soft=1.0, ctx=(0.0, 0.0, 1.0))


# ---------------------------------------------------------------- 对外接口
def spawn_size(scale: float, hit_radius: float) -> int:
    """一只苍蝇的窗口边长: 要装得下本体(含旋转外接框)、命中圆盘和一点余量。"""
    body = 46.0 * scale + 14.0
    disc = 2.0 * hit_radius + 10.0
    size = int(math.ceil(max(52.0, body, disc)))
    return size + (size % 2)          # 取偶数, 让中心落在整数像素上


def render_fly(angle_idx: int, pose: str, wing_phase: int, size: int,
               scale: float = 1.0, hit_radius: float = 0.0) -> bytes:
    """渲染一帧苍蝇 (预乘 BGRA, size*size*4 字节)。"""
    canvas = Canvas(size, size)
    if hit_radius > 1.0:
        _hit_disc(canvas, hit_radius)
    angle = 360.0 * (angle_idx % ANGLES) / ANGLES
    _draw_fly(canvas, angle, pose, wing_phase, size, scale)
    return canvas.tobytes()


def render_egg(size: int, scale: float = 1.0) -> bytes:
    """卵: 一个椭圆小白点 (带一点阴影和高光)。"""
    canvas = Canvas(size, size)
    c = size / 2.0
    rx, ry = 3.6 * scale, 4.6 * scale
    draw(canvas, _ellipse_sdf(c + 0.8 * scale, c + 1.6 * scale, rx, ry, 0.0),
         C_SHADOW, alpha=64, soft=5.0, ctx=(0.0, 0.0, 1.0))
    draw(canvas, _ellipse_sdf(c, c, rx, ry, 0.0),
         lambda x, y, d: _mix(C_EGG, C_EGG_SHADE,
                              _clamp01(0.35 + (x - c) / (5.0 * scale))),
         alpha=255, soft=0.9, ctx=(0.0, 0.0, 1.0))
    draw(canvas, _ellipse_sdf(c - 1.1 * scale, c - 1.6 * scale, 1.1 * scale,
                              1.5 * scale, 0.0),
         (255, 255, 255), alpha=210, soft=1.4, ctx=(0.0, 0.0, 1.0))
    return canvas.tobytes()


def swatter_size(scale: float) -> int:
    return int(math.ceil(88.0 * scale)) + 1


def render_swatter(size: int, scale: float = 1.0, swing: float = 0.0) -> bytes:
    """苍蝇拍 (从斜上方看): 拍面中心 = 光标位置, 柄往右下伸。

    swing: 0 = 抬起, 1 = 拍下去 (下压 + 轻微前倾), 用来做挥拍动画。
    """
    canvas = Canvas(size, size)
    c = size / 2.0
    ctx = (c, c, scale)
    tilt = -14.0 * swing
    press = 5.0 * swing

    # 拍柄: 从拍面右下方伸出
    handle = _capsule_sdf((7.0, 7.0), (33.0, 33.0), 3.0, 2.2)
    draw(canvas, handle, (54, 60, 70), alpha=250, soft=1.0, ctx=ctx)
    draw(canvas, _capsule_sdf((6.6, 6.2), (31.0, 29.8), 1.1, 0.8),
         (150, 158, 172), alpha=170, soft=0.9, ctx=ctx)
    # 手柄握把
    draw(canvas, _ellipse_sdf(31.0, 31.0, 3.4, 3.4, 0.0), (38, 42, 50),
         alpha=250, soft=1.0, ctx=ctx)

    # 拍面: 圆角方形网格 (用椭圆近似), 外框
    head = _ellipse_sdf(0.0, press * 0.35, 20.0, 22.0, math.radians(tilt))
    frame = _union(head,
                   _ellipse_sdf(0.0, press * 0.35, 17.0, 19.0, math.radians(tilt)))
    draw(canvas, head, (226, 84, 74), alpha=170, soft=1.0, ctx=ctx)
    draw(canvas, _ellipse_sdf(0.0, press * 0.35, 20.0, 22.0, math.radians(tilt)),
         (176, 48, 40), alpha=250, soft=1.0, ctx=ctx)
    draw(canvas, _ellipse_sdf(0.0, press * 0.35, 17.4, 19.4, math.radians(tilt)),
         (226, 84, 74), alpha=255, soft=1.0, ctx=ctx)
    # 打出网格孔
    step = 5.2
    for gy in range(-3, 4):
        for gx in range(-3, 4):
            px_ = gx * step
            py_ = gy * step * 1.05 + press * 0.35
            if (px_ / 15.6) ** 2 + ((py_ - press * 0.35) / 17.4) ** 2 > 0.92:
                continue
            pt = _rotate([(px_, py_)], tilt)[0]
            draw(canvas, _ellipse_sdf(pt[0], pt[1], 1.35, 1.35, 0.0),
                 (0, 0, 0), alpha=235, soft=0.8, mode="erase", ctx=ctx)
    # 拍面高光
    draw(canvas, _ellipse_sdf(-7.0, -8.0 + press * 0.35, 5.4, 8.6,
                              math.radians(tilt)),
         (255, 235, 230), alpha=88, soft=3.0, ctx=ctx)
    return canvas.tobytes()


# ---------------------------------------------------------------- 预览 (开发用)
def _preview(path: str = "flyart_preview.png") -> None:  # pragma: no cover
    """把各种角度/姿态拼成一张联系表, 方便肉眼检查美术效果。

    只在开发时手动跑: python flyart.py [输出路径]
    需要 Pillow (仅预览用, 程序运行时不需要)。
    """
    from PIL import Image, ImageDraw

    size = spawn_size(1.0, 0.0)
    cell = size + 6
    cols = ANGLES
    rows = 3 + WING_PHASES
    sheet = Image.new("RGBA", (cols * cell, rows * cell + 110), (58, 60, 66, 255))
    for j in range(cols):
        for i, pose in enumerate(POSES):
            data = render_fly(j, pose, 0, size, 1.0, 0.0)
            img = Image.frombuffer("RGBA", (size, size), bytes(data), "raw", "BGRA", 0, 1)
            sheet.alpha_composite(img, (j * cell + 3, i * cell + 3))
    for k in range(WING_PHASES):
        data = render_fly(0, "fly", k, size, 1.0, 0.0)
        img = Image.frombuffer("RGBA", (size, size), bytes(data), "raw", "BGRA", 0, 1)
        sheet.alpha_composite(img, (k * cell + 3, (3 + k) * cell + 3))
    # 放大镜: 6 个角度的 3 倍大图 + 卵 + 拍子
    big = size * 3
    for j in range(6):
        data = render_fly(j * 2, "fly", j % WING_PHASES, big, 3.0, 0.0)
        img = Image.frombuffer("RGBA", (big, big), bytes(data), "raw", "BGRA", 0, 1)
        sheet.alpha_composite(img, (j * (big + 4) + 3, rows * cell + 6))
    return sheet


if __name__ == "__main__":  # pragma: no cover
    import sys
    import time

    out = sys.argv[1] if len(sys.argv) > 1 else "flyart_preview.png"
    t0 = time.time()
    n = 0
    for a in range(ANGLES):
        for p in POSES:
            for w in range(WING_PHASES if p == "fly" else 1):
                render_fly(a, p, w, spawn_size(1.0, 44.0), 1.0, 44.0)
                n += 1
    dt = time.time() - t0
    print("渲染 %d 帧耗时 %.2fs (%.1f ms/帧)" % (n, dt, dt * 1000.0 / max(1, n)))
    sheet = _preview(out)
    sheet.save(out)
    print("已保存", out)
