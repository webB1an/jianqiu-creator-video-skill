#!/usr/bin/env python3
"""3Blue1Brown-style mathematical & structural animation engine (Manim bridge + zero-dep builtin renderer).

Produces crisp BT.709 H.264 1080p MP4 shots with continuous mathematical motion so math,
algorithmic, geometry and dependency explanations never devolve into static slides.

Usage:
  # List built-in presets and check Manim availability
  math_motion.py list

  # Render a preset directly
  math_motion.py render --preset complex-plane --seconds 6 --title "临界线上的非平凡零点" \\
    --subtitle "Re(s) = 1/2 · 黎曼 ζ 函数螺旋轨迹" --out media/zeta.mp4

  # Render from a JSON spec file or project shot (shot["math_motion"])
  math_motion.py render spec.json --out media/math.mp4
  math_motion.py render --project videos/my-film --shot s04

  # Render a real Manim Python scene when manim is installed (--engine manim)
  math_motion.py render --engine manim --scene-file scenes/riemann.py --scene-class ZetaSpiral \\
    --seconds 6 --out media/manim-zeta.mp4
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

PRESETS = {
    "complex-plane": {
        "name": "复平面临界线与 ζ 螺旋轨迹",
        "description": "3Blue1Brown 风格复平面坐标网格、临界线 Re(s)=1/2 与连续演化的 ζ 螺旋零点轨迹，实时读数随时间更新。",
    },
    "function-morph": {
        "name": "阶梯分布与连续函数形变对照",
        "description": "展示离散阶梯曲线（如素数计数 π(x)）与光滑逼近曲线（如 Li(x)）之间的形变、误差带扫描与动态读数。",
    },
    "vector-field": {
        "name": "线性空间变换与向量流场",
        "description": "二维坐标网格在 2×2 矩阵作用下的平滑线性拉伸/旋转变换，叠加流动粒子轨迹与特征向量箭头。",
    },
    "network-graph": {
        "name": "分层证明树与信号传播图",
        "description": "多层级依赖图 / 形式化证明 DAG / 神经网络脉冲沿贝塞尔连线逐层点亮传播（支持自定义 layers 节点）。",
    },
    "formula-plot": {
        "name": "通用参数曲线与缩放定律演进",
        "description": "自由绘制 1–3 条自定义数学表达式 curves（支持 sin/cos/exp/log/sqrt/pow/tanh），随进度动态扫描、追踪当前值并实时更新读数。",
    },
}

FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
]
SERIF_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
]


def _load_font(size: int, *, serif: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = SERIF_CANDIDATES if serif else FONT_CANDIDATES
    for p in candidates:
        if Path(p).is_file():
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _hex_to_bgr(code: str, default=(97, 169, 237)) -> tuple[int, int, int]:
    s = (code or "").strip().lstrip("#")
    if len(s) == 6:
        r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
        return (b, g, r)
    return default


def _hex_to_rgb(code: str, default=(237, 169, 97)) -> tuple[int, int, int]:
    b, g, r = _hex_to_bgr(code, (default[2], default[1], default[0]))
    return (r, g, b)


def _smoothstep(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x * x * (3.0 - 2.0 * x)


def _draw_header_and_hud(
    bgr: np.ndarray,
    *,
    title: str,
    subtitle: str,
    hud_lines: list[tuple[str, tuple[int, int, int]]],
    accent_rgb: tuple[int, int, int],
    progress: float,
    title_font,
    sub_font,
    hud_font,
) -> np.ndarray:
    """Composite crisp anti-aliased CJK title, subtitle and live math readout HUD via Pillow."""
    h, w = bgr.shape[:2]
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    img = Image.fromarray(rgb)
    draw = ImageDraw.Draw(img, "RGBA")

    # Subtle top-left editorial plate for legibility without obscuring the math stage.
    if title or subtitle:
        bar_w = int(48 + 16 * math.sin(progress * math.pi * 2))
        draw.rounded_rectangle([68, 52, 68 + bar_w, 57], radius=2, fill=(*accent_rgb, 245))
        if title:
            draw.text((68, 66), title, font=title_font, fill=(244, 237, 221, 250))
        if subtitle:
            draw.text((68, 126), subtitle, font=sub_font, fill=(169, 179, 170, 235))

    # Top-right live telemetry readout pill (placed cleanly below top-right brand badge safe zone)
    if hud_lines:
        row_h = max(28, int(34 * min(1.0, h / 960.0)))
        box_w = min(440, int(w * 0.27))
        box_h = 22 + len(hud_lines) * row_h
        x0, y0 = w - box_w - 64, max(106, int(h * 0.145))
        draw.rounded_rectangle(
            [x0, y0, x0 + box_w, y0 + box_h],
            radius=12,
            fill=(16, 23, 20, 210),
            outline=(*accent_rgb, 140),
            width=2,
        )
        for i, (text, color) in enumerate(hud_lines):
            draw.text((x0 + 18, y0 + 12 + i * row_h), text, font=hud_font, fill=(*color, 245))

    return cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)


def _render_complex_plane(frame_idx: int, total: int, w: int, h: int, spec: dict, fonts: dict) -> np.ndarray:
    """3Blue1Brown-inspired critical strip & evolving Riemann zeta-like spiral zero hits."""
    p = frame_idx / max(1, total - 1)
    canvas = np.full((h, w, 3), (23, 26, 19), dtype=np.uint8)  # #131A17 BGR
    accent_bgr = _hex_to_bgr(spec.get("accent", "#EDA961"), (97, 169, 237))
    teal_bgr = (205, 215, 138)  # #8AD7CD
    coral_bgr = (121, 154, 212)  # #D49A79

    # Subtle radial studio glow drifting gently across the plane
    gx = int(w * (0.48 + 0.08 * math.sin(p * math.pi)))
    gy = int(h * (0.54 + 0.04 * math.cos(p * math.pi * 2)))
    yy, xx = np.ogrid[:h, :w]
    dist2 = ((xx - gx) / (w * 0.52)) ** 2 + ((yy - gy) / (h * 0.56)) ** 2
    glow = np.clip(1.0 - dist2, 0.0, 1.0) ** 2
    canvas[:, :, 0] = np.clip(canvas[:, :, 0] + glow * 26, 0, 255).astype(np.uint8)
    canvas[:, :, 1] = np.clip(canvas[:, :, 1] + glow * 32, 0, 255).astype(np.uint8)
    canvas[:, :, 2] = np.clip(canvas[:, :, 2] + glow * 18, 0, 255).astype(np.uint8)

    # Coordinate frame with gentle cinematic drift
    cx = int(w * (0.50 - 0.02 * p))
    cy = int(h * 0.56)
    unit = int(min(w, h) * (0.145 + 0.015 * _smoothstep(p)))

    # Grid lines
    for k in range(-7, 8):
        x = cx + k * unit
        if 0 <= x < w:
            col = (58, 72, 65) if k == 0 else (34, 42, 38)
            cv2.line(canvas, (x, 175), (x, h - 72), col, 2 if k == 0 else 1, cv2.LINE_AA)
    for k in range(-4, 5):
        y = cy + k * unit
        if 175 <= y < h - 72:
            col = (58, 72, 65) if k == 0 else (34, 42, 38)
            cv2.line(canvas, (68, y), (w - 68, y), col, 2 if k == 0 else 1, cv2.LINE_AA)

    # Critical strip 0 <= Re(s) <= 1 and glowing critical line Re(s) = 1/2
    strip_l = cx
    strip_r = cx + unit
    crit_x = cx + unit // 2
    overlay = canvas.copy()
    cv2.rectangle(overlay, (strip_l, 175), (strip_r, h - 72), (62, 86, 78), -1)
    cv2.addWeighted(overlay, 0.22, canvas, 0.78, 0, canvas)
    cv2.line(canvas, (crit_x, 175), (crit_x, h - 72), accent_bgr, 3, cv2.LINE_AA)

    # Compute Dirichlet eta approximation to ζ(1/2 + i*t) for t in [0.5, t_max]
    # Famous first three nontrivial zeros are at t ≈ 14.1347, 21.0220, 25.0109.
    t_max = float(spec.get("t_max", 26.5))
    t_now = 0.8 + (t_max - 0.8) * _smoothstep(p)
    n_terms = 28
    ns = np.arange(1, n_terms + 1, dtype=np.float64)
    signs = np.where((ns.astype(int) % 2) == 1, 1.0, -1.0)
    amps = signs / np.sqrt(ns)
    log_ns = np.log(ns)

    def zeta_approx(t_val: float) -> complex:
        phase = -t_val * log_ns
        eta = complex(float(np.sum(amps * np.cos(phase))), float(np.sum(amps * np.sin(phase))))
        # ζ(s) = η(s) / (1 - 2^(1-s))
        denom = 1.0 - (2.0 ** 0.5) * complex(math.cos(-t_val * math.log(2.0)), math.sin(-t_val * math.log(2.0)))
        if abs(denom) < 1e-6:
            return complex(0.0, 0.0)
        return eta / denom

    samples = max(40, int(t_now * 16))
    ts = np.linspace(0.8, t_now, samples)
    pts = []
    for tv in ts:
        z = zeta_approx(float(tv))
        px = int(cx + z.real * unit * 0.95)
        py = int(cy - z.imag * unit * 0.95)
        pts.append((px, py))

    # Draw luminous spiral trail with progressive brightness
    if len(pts) >= 2:
        glow_layer = np.zeros_like(canvas)
        for i in range(1, len(pts)):
            frac = i / len(pts)
            col = (
                int(teal_bgr[0] * (0.45 + 0.55 * frac)),
                int(teal_bgr[1] * (0.45 + 0.55 * frac)),
                int(teal_bgr[2] * (0.45 + 0.55 * frac)),
            )
            cv2.line(glow_layer, pts[i - 1], pts[i], col, 6 if frac > 0.85 else 3, cv2.LINE_AA)
            cv2.line(canvas, pts[i - 1], pts[i], col, 3 if frac > 0.85 else 2, cv2.LINE_AA)
        glow_blur = cv2.GaussianBlur(glow_layer, (0, 0), 7.0)
        canvas = cv2.addWeighted(canvas, 1.0, glow_blur, 0.55, 0)

    # Highlight zero crossings on the critical line as t passes known nontrivial zeros
    zeros = [14.1347, 21.0220, 25.0109]
    hits = 0
    for zt in zeros:
        if t_now >= zt:
            hits += 1
            age = min(1.0, (t_now - zt) / 2.2)
            ring_r = int(12 + 34 * age)
            ring_col = tuple(int(c * (1.0 - 0.6 * age)) for c in accent_bgr)
            cv2.circle(canvas, (cx, cy), ring_r, ring_col, 2, cv2.LINE_AA)
            # Mark zero dot on critical line
            zy = int(h - 110 - (zt / t_max) * (h - 330))
            cv2.circle(canvas, (crit_x, zy), 8, accent_bgr, -1, cv2.LINE_AA)
            cv2.circle(canvas, (crit_x, zy), 14, accent_bgr, 2, cv2.LINE_AA)

    # Current tip dot
    cur_z = zeta_approx(t_now)
    tip = pts[-1]
    cv2.circle(canvas, (cx, cy), 6, coral_bgr, -1, cv2.LINE_AA)
    cv2.circle(canvas, tip, 11, accent_bgr, -1, cv2.LINE_AA)
    cv2.circle(canvas, tip, 18, accent_bgr, 2, cv2.LINE_AA)

    hud = [
        (f"t = {t_now:6.2f}   Re(s) = 0.50", (237, 169, 97)),
        (f"ζ(s) = {cur_z.real:+5.2f} {cur_z.imag:+5.2f}i", (138, 215, 205)),
        (f"临界线零点命中：{hits} / {len(zeros)}", (244, 237, 221)),
    ]
    return _draw_header_and_hud(
        canvas,
        title=spec.get("title", "复平面上的临界线与 ζ 零点螺旋"),
        subtitle=spec.get("subtitle", "Re(s) = 1/2 · 随虚部 t 连续演化的曲线反复穿过原点"),
        hud_lines=hud,
        accent_rgb=_hex_to_rgb(spec.get("accent", "#EDA961")),
        progress=p,
        **fonts,
    )


def _render_function_morph(frame_idx: int, total: int, w: int, h: int, spec: dict, fonts: dict) -> np.ndarray:
    """Discrete prime staircase π(x) vs smooth Li(x) approximation + error wave scan."""
    p = frame_idx / max(1, total - 1)
    canvas = np.full((h, w, 3), (23, 26, 19), dtype=np.uint8)
    accent_bgr = _hex_to_bgr(spec.get("accent", "#EDA961"), (97, 169, 237))
    teal_bgr = (205, 215, 138)
    coral_bgr = (121, 154, 212)

    x0, x1 = int(w * 0.10), int(w * 0.90)
    y0, y1 = int(h * 0.24), int(h * 0.84)
    # Subtle grid
    for i in range(7):
        gx = int(x0 + (x1 - x0) * i / 6)
        cv2.line(canvas, (gx, y0), (gx, y1), (34, 42, 38), 1, cv2.LINE_AA)
    for j in range(5):
        gy = int(y0 + (y1 - y0) * j / 4)
        cv2.line(canvas, (x0, gy), (x1, gy), (34, 42, 38), 1, cv2.LINE_AA)
    cv2.line(canvas, (x0, y1), (x1, y1), (95, 108, 100), 2, cv2.LINE_AA)
    cv2.line(canvas, (x0, y0), (x0, y1), (95, 108, 100), 2, cv2.LINE_AA)

    # Primes up to 120 for exact π(x)
    primes = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61, 67, 71, 73, 79, 83, 89, 97, 101, 103, 107, 109, 113]
    x_limit = 118.0
    y_limit = 34.0

    draw_p = _smoothstep(min(1.0, p / 0.72))
    morph_p = _smoothstep(max(0.0, (p - 0.45) / 0.45))
    scan_x_val = 2.0 + (x_limit - 2.0) * draw_p

    # Build exact offset logarithmic integral Li(x) = ∫_2^x dt/ln(t) & staircase π(x)
    xs_grid = np.linspace(2.0, max(2.2, scan_x_val), 240)
    li_pts, pi_pts = [], []
    def li_exact(xv: float) -> float:
        if xv <= 2.001:
            return 0.0
        ts = np.linspace(2.0, xv, 96)
        return float(np.trapezoid(1.0 / np.log(ts), ts))

    for xv in xs_grid:
        px = int(x0 + (xv / x_limit) * (x1 - x0))
        yv_li = li_exact(float(xv))
        yv_pi = sum(1 for pr in primes if pr <= xv)
        # Morph smoothly emphasizes the tight band between π(x) and Li(x)
        py_li = int(y1 - (yv_li / y_limit) * (y1 - y0))
        py_pi = int(y1 - (yv_pi / y_limit) * (y1 - y0))
        li_pts.append((px, max(y0, min(y1, py_li))))
        pi_pts.append((px, max(y0, min(y1, py_pi))))

    # Shaded error envelope between discrete staircase and smooth integral
    if len(li_pts) >= 2:
        poly = np.array(li_pts + list(reversed(pi_pts)), dtype=np.int32)
        shade = canvas.copy()
        cv2.fillPoly(shade, [poly], (110, 128, 170))
        cv2.addWeighted(shade, 0.24 + 0.16 * morph_p, canvas, 0.76 - 0.16 * morph_p, 0, canvas)

        # Draw smooth curve Li(x)
        cv2.polylines(canvas, [np.array(li_pts, dtype=np.int32)], False, teal_bgr, 4, cv2.LINE_AA)
        # Draw discrete staircase π(x)
        cv2.polylines(canvas, [np.array(pi_pts, dtype=np.int32)], False, accent_bgr, 4, cv2.LINE_AA)

        # Scanning vertical cursor
        tip_x = li_pts[-1][0]
        cv2.line(canvas, (tip_x, y0), (tip_x, y1), (110, 125, 118), 1, cv2.LINE_AA)
        cv2.circle(canvas, li_pts[-1], 8, teal_bgr, -1, cv2.LINE_AA)
        cv2.circle(canvas, pi_pts[-1], 8, accent_bgr, -1, cv2.LINE_AA)
        # Error bar highlight
        cv2.line(canvas, pi_pts[-1], li_pts[-1], coral_bgr, 4, cv2.LINE_AA)

    cur_pi = sum(1 for pr in primes if pr <= scan_x_val)
    cur_li = li_exact(scan_x_val)
    err = abs(cur_li - cur_pi)
    hud = [
        (f"x = {scan_x_val:6.1f}   π(x) = {cur_pi:2d}", (237, 169, 97)),
        (f"Li(x) ≈ {cur_li:5.2f}", (138, 215, 205)),
        (f"实时误差 |Δ| = {err:4.2f}  (O(√x log x))", (212, 154, 121)),
    ]
    return _draw_header_and_hud(
        canvas,
        title=spec.get("title", "离散素数阶梯 π(x) 与光滑逼近 Li(x)"),
        subtitle=spec.get("subtitle", "误差带宽度直接受临界线零点位置约束"),
        hud_lines=hud,
        accent_rgb=_hex_to_rgb(spec.get("accent", "#EDA961")),
        progress=p,
        **fonts,
    )


def _render_vector_field(frame_idx: int, total: int, w: int, h: int, spec: dict, fonts: dict) -> np.ndarray:
    """Linear grid transformation + flowing streamlines."""
    p = frame_idx / max(1, total - 1)
    t = _smoothstep(p)
    canvas = np.full((h, w, 3), (23, 26, 19), dtype=np.uint8)
    accent_bgr = _hex_to_bgr(spec.get("accent", "#EDA961"), (97, 169, 237))
    teal_bgr = (205, 215, 138)
    coral_bgr = (121, 154, 212)

    target = spec.get("matrix", [[1.42, 0.48], [-0.32, 1.18]])
    a = 1.0 + (float(target[0][0]) - 1.0) * t
    b = 0.0 + float(target[0][1]) * t
    c = 0.0 + float(target[1][0]) * t
    d = 1.0 + (float(target[1][1]) - 1.0) * t
    det = a * d - b * c

    cx, cy = w // 2, int(h * 0.56)
    unit = min(w, h) * 0.11

    def proj(x: float, y: float) -> tuple[int, int]:
        tx = a * x + b * y
        ty = c * x + d * y
        return (int(cx + tx * unit), int(cy - ty * unit))

    # Original faint reference grid
    for k in range(-6, 7):
        cv2.line(canvas, (int(cx + k * unit), 170), (int(cx + k * unit), h - 60), (30, 36, 33), 1, cv2.LINE_AA)
    for k in range(-4, 5):
        cv2.line(canvas, (60, int(cy - k * unit)), (w - 60, int(cy - k * unit)), (30, 36, 33), 1, cv2.LINE_AA)

    # Transformed grid
    for k in range(-5, 6):
        col = teal_bgr if k == 0 else (84, 118, 108)
        thick = 3 if k == 0 else 1
        cv2.line(canvas, proj(k, -4.5), proj(k, 4.5), col, thick, cv2.LINE_AA)
        cv2.line(canvas, proj(-5.5, k), proj(5.5, k), col, thick, cv2.LINE_AA)

    # Unit square deformation fill
    sq = np.array([proj(0, 0), proj(1, 0), proj(1, 1), proj(0, 1)], dtype=np.int32)
    shade = canvas.copy()
    cv2.fillPoly(shade, [sq], accent_bgr)
    cv2.addWeighted(shade, 0.25, canvas, 0.75, 0, canvas)
    cv2.polylines(canvas, [sq], True, accent_bgr, 2, cv2.LINE_AA)

    # Basis vectors i-hat and j-hat
    cv2.arrowedLine(canvas, proj(0, 0), proj(1, 0), accent_bgr, 5, cv2.LINE_AA, tipLength=0.14)
    cv2.arrowedLine(canvas, proj(0, 0), proj(0, 1), coral_bgr, 5, cv2.LINE_AA, tipLength=0.14)

    # Flowing vector particles
    n_particles = int(spec.get("particles", 36))
    for idx in range(n_particles):
        ang = (idx / n_particles) * math.pi * 2
        rad = 1.4 + 2.4 * ((idx * 0.6180339 + p * 0.85) % 1.0)
        x0_p, y0_p = rad * math.cos(ang), rad * math.sin(ang)
        x1_p, y1_p = (rad + 0.35) * math.cos(ang + 0.14), (rad + 0.35) * math.sin(ang + 0.14)
        cv2.line(canvas, proj(x0_p, y0_p), proj(x1_p, y1_p), teal_bgr, 2, cv2.LINE_AA)
        cv2.circle(canvas, proj(x1_p, y1_p), 4, accent_bgr, -1, cv2.LINE_AA)

    hud = [
        (f"M = [[{a:5.2f}, {b:5.2f}], [{c:5.2f}, {d:5.2f}]]", (237, 169, 97)),
        (f"行列式 det(M) = {det:5.2f}", (138, 215, 205)),
        (f"变换进度：{int(round(t * 100)):3d}%", (244, 237, 221)),
    ]
    return _draw_header_and_hud(
        canvas,
        title=spec.get("title", "线性变换与向量空间流动"),
        subtitle=spec.get("subtitle", "基向量拉伸、旋转与面积缩放系数 det(M)"),
        hud_lines=hud,
        accent_rgb=_hex_to_rgb(spec.get("accent", "#EDA961")),
        progress=p,
        **fonts,
    )


def _render_network_graph(frame_idx: int, total: int, w: int, h: int, spec: dict, fonts: dict) -> np.ndarray:
    """Layered formal proof / algorithmic dependency DAG with travelling signal pulses."""
    p = frame_idx / max(1, total - 1)
    canvas = np.full((h, w, 3), (23, 26, 19), dtype=np.uint8)
    accent_bgr = _hex_to_bgr(spec.get("accent", "#EDA961"), (97, 169, 237))
    teal_bgr = (205, 215, 138)

    layers = spec.get("layers") or [
        ["公理底座", "解析延拓", "函数方程"],
        ["引理 A", "局部界估计", "围道积分", "误差控制"],
        ["形式化规约", "Lean 校验器"],
        ["最终定理"],
    ]
    n_layers = len(layers)
    xs = [int(w * (0.14 + 0.72 * i / max(1, n_layers - 1))) for i in range(n_layers)]
    coords: list[list[tuple[int, int, str]]] = []
    for li, names in enumerate(layers):
        row = []
        top_y, bot_y = int(h * 0.27), int(h * 0.84)
        for ni, label in enumerate(names):
            ny = int(top_y + (bot_y - top_y) * ((ni + 0.5) / len(names)))
            row.append((xs[li], ny, str(label)))
        coords.append(row)

    # Wave front moves from layer 0 to final layer
    wave = p * (n_layers + 0.35)
    # Draw edges + travelling pulses
    for li in range(n_layers - 1):
        seg_p = max(0.0, min(1.0, wave - li))
        for x1, y1, _ in coords[li]:
            for x2, y2, _ in coords[li + 1]:
                # Smooth cubic Bezier curve
                mx = (x1 + x2) // 2
                pts = []
                for step in range(25):
                    u = step / 24.0
                    bx = int(((1 - u) ** 3) * x1 + 3 * ((1 - u) ** 2) * u * mx + 3 * (1 - u) * (u ** 2) * mx + (u ** 3) * x2)
                    by = int(((1 - u) ** 3) * y1 + 3 * ((1 - u) ** 2) * u * y1 + 3 * (1 - u) * (u ** 2) * y2 + (u ** 3) * y2)
                    pts.append((bx, by))
                cv2.polylines(canvas, [np.array(pts, dtype=np.int32)], False, (52, 64, 58), 2, cv2.LINE_AA)
                if seg_p > 0.01:
                    k = max(2, int(len(pts) * seg_p))
                    cv2.polylines(canvas, [np.array(pts[:k], dtype=np.int32)], False, teal_bgr, 3, cv2.LINE_AA)
                    if seg_p < 0.99:
                        cv2.circle(canvas, pts[min(k - 1, len(pts) - 1)], 6, accent_bgr, -1, cv2.LINE_AA)

    # Nodes
    active_nodes = 0
    total_nodes = sum(len(r) for r in coords)
    node_labels = []
    for li, row in enumerate(coords):
        lit = wave >= li + 0.15
        pulse = 0.5 + 0.5 * math.sin(frame_idx * 0.18 + li)
        for nx, ny, label in row:
            if lit:
                active_nodes += 1
                cv2.circle(canvas, (nx, ny), int(26 + 6 * pulse), accent_bgr, 2, cv2.LINE_AA)
                cv2.circle(canvas, (nx, ny), 20, accent_bgr if li == n_layers - 1 else teal_bgr, -1, cv2.LINE_AA)
            else:
                cv2.circle(canvas, (nx, ny), 18, (42, 52, 47), -1, cv2.LINE_AA)
                cv2.circle(canvas, (nx, ny), 18, (88, 102, 95), 2, cv2.LINE_AA)
            node_labels.append((nx, ny + 34, label, lit))

    # Draw node labels with Pillow
    rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
    img = Image.fromarray(rgb)
    draw = ImageDraw.Draw(img, "RGBA")
    lbl_font = _load_font(22)
    for nx, ny, label, lit in node_labels:
        bbox = draw.textbbox((0, 0), label, font=lbl_font)
        tw = bbox[2] - bbox[0]
        draw.text((nx - tw // 2, ny), label, font=lbl_font, fill=(244, 237, 221, 248) if lit else (150, 160, 152, 200))
    canvas = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)

    hud = [
        (f"已校验节点：{active_nodes:2d} / {total_nodes}", (237, 169, 97)),
        (f"推进层级：{min(n_layers, int(wave) + 1)} / {n_layers}", (138, 215, 205)),
        (f"核验完成度：{int(round(p * 100)):3d}%", (244, 237, 221)),
    ]
    return _draw_header_and_hud(
        canvas,
        title=spec.get("title", "形式化证明依赖网络"),
        subtitle=spec.get("subtitle", "从公理底座到终极定理的逐层机器核验链路"),
        hud_lines=hud,
        accent_rgb=_hex_to_rgb(spec.get("accent", "#EDA961")),
        progress=p,
        **fonts,
    )


def _eval_curve_expr(expr: str, x_arr: np.ndarray, t: float) -> np.ndarray:
    """Safely evaluate a bounded mathematical curve expression over numpy array x and progress t."""
    safe_env = {
        "__builtins__": {},
        "x": x_arr, "t": float(t), "pi": math.pi, "e": math.e,
        "sin": np.sin, "cos": np.cos, "tan": np.tan, "tanh": np.tanh,
        "exp": np.exp, "log": lambda v: np.log(np.maximum(1e-6, v)),
        "log2": lambda v: np.log2(np.maximum(1e-6, v)),
        "log10": lambda v: np.log10(np.maximum(1e-6, v)),
        "sqrt": lambda v: np.sqrt(np.maximum(0.0, v)),
        "abs": np.abs, "pow": np.power, "clip": np.clip,
    }
    res = eval(str(expr), safe_env, {})  # noqa: S307 (restricted math-only namespace)
    return np.broadcast_to(np.asarray(res, dtype=np.float64), x_arr.shape).copy()


def _render_formula_plot(frame_idx: int, total: int, w: int, h: int, spec: dict, fonts: dict) -> np.ndarray:
    """General-purpose parameterized multi-curve mathematical & scaling-law evolution plot."""
    p = frame_idx / max(1, total - 1)
    canvas = np.full((h, w, 3), (23, 26, 19), dtype=np.uint8)
    accent_bgr = _hex_to_bgr(spec.get("accent", "#EDA961"), (97, 169, 237))
    x_min, x_max = [float(v) for v in spec.get("x_range", [0.0, 10.0])]
    y_min, y_max = [float(v) for v in spec.get("y_range", [0.0, 10.0])]
    curves = spec.get("curves") or [
        {"label": "缩放律曲线", "expr": "1.2 * pow(x + 0.5, 0.85)", "color": "#EDA961"},
        {"label": "线性基准", "expr": "0.65 * x + 0.8", "color": "#8AD7CD"},
    ]

    x0, x1 = int(w * 0.11), int(w * 0.88)
    y0, y1 = int(h * 0.24), int(h * 0.84)
    # Subtle coordinate grid
    for k in range(6):
        gx = int(x0 + (x1 - x0) * k / 5)
        gy = int(y0 + (y1 - y0) * k / 5)
        cv2.line(canvas, (gx, y0), (gx, y1), (36, 45, 40), 1, cv2.LINE_AA)
        cv2.line(canvas, (x0, gy), (x1, gy), (36, 45, 40), 1, cv2.LINE_AA)
    cv2.line(canvas, (x0, y1), (x1, y1), (95, 112, 104), 2, cv2.LINE_AA)
    cv2.line(canvas, (x0, y0), (x0, y1), (95, 112, 104), 2, cv2.LINE_AA)

    xs = np.linspace(x_min, x_max, 240)
    reveal_p = _smoothstep(min(1.0, p * 1.15))
    k_draw = max(2, int(len(xs) * reveal_p))
    cur_x = float(xs[k_draw - 1])
    hud = [(f"{spec.get('x_label', 'x')} = {cur_x:5.2f}", (244, 237, 221))]

    glow = np.zeros_like(canvas)
    for c_spec in curves[:3]:
        ys = _eval_curve_expr(c_spec["expr"], xs, p)
        col_bgr = _hex_to_bgr(c_spec.get("color", "#EDA961"), accent_bgr)
        col_rgb = _hex_to_rgb(c_spec.get("color", "#EDA961"))
        pts = []
        for xv, yv in zip(xs[:k_draw], ys[:k_draw]):
            if not math.isfinite(yv):
                continue
            px = int(round(x0 + (xv - x_min) / max(1e-6, x_max - x_min) * (x1 - x0)))
            py = int(round(y1 - (yv - y_min) / max(1e-6, y_max - y_min) * (y1 - y0)))
            py = max(y0, min(y1, py))
            pts.append((px, py))
        if len(pts) >= 2:
            arr = np.array(pts, dtype=np.int32)
            cv2.polylines(glow, [arr], False, col_bgr, 8, cv2.LINE_AA)
            cv2.polylines(canvas, [arr], False, col_bgr, 4, cv2.LINE_AA)
            tip = pts[-1]
            cv2.circle(canvas, tip, 9, col_bgr, -1, cv2.LINE_AA)
            cv2.circle(canvas, tip, 4, (255, 255, 255), -1, cv2.LINE_AA)
            hud.append((f"{c_spec.get('label', 'y')}: {float(ys[k_draw - 1]):6.2f}", col_rgb))
    canvas = cv2.addWeighted(canvas, 1.0, cv2.GaussianBlur(glow, (0, 0), 7.0), 0.45, 0)

    return _draw_header_and_hud(
        canvas,
        title=spec.get("title", "函数演化与指标对比"),
        subtitle=spec.get("subtitle", "参数化曲线连续演进"),
        hud_lines=hud,
        accent_rgb=_hex_to_rgb(spec.get("accent", "#EDA961")),
        progress=p,
        **fonts,
    )


RENDERERS = {
    "complex-plane": _render_complex_plane,
    "function-morph": _render_function_morph,
    "vector-field": _render_vector_field,
    "network-graph": _render_network_graph,
    "formula-plot": _render_formula_plot,
}


def render_builtin(spec: dict, out_path: Path, *, width: int, height: int, fps: int, frames: int) -> dict:
    preset = spec.get("preset", "complex-plane")
    if preset not in RENDERERS:
        raise ValueError(f"Unknown math_motion preset: {preset}. Choose from {list(RENDERERS)}")
    fn = RENDERERS[preset]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fonts = {
        "title_font": _load_font(44, serif=True),
        "sub_font": _load_font(22, serif=False),
        "hud_font": _load_font(22, serif=False),
    }

    with tempfile.TemporaryDirectory(prefix="math-motion-") as td:
        tmp_mp4 = Path(td) / "out.mp4"
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-f", "rawvideo", "-vcodec", "rawvideo",
            "-s", f"{width}x{height}", "-pix_fmt", "bgr24", "-r", str(fps),
            "-i", "-",
            "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "16",
            "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709",
            "-color_trc", "bt709", "-color_range", "tv", "-movflags", "+faststart",
            str(tmp_mp4),
        ]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        assert proc.stdin is not None
        try:
            for idx in range(frames):
                frame = fn(idx, frames, width, height, spec, fonts)
                proc.stdin.write(frame.tobytes())
        finally:
            proc.stdin.close()
        rc = proc.wait()
        if rc != 0:
            raise RuntimeError(f"FFmpeg encode failed with code {rc}")
        shutil.copy2(tmp_mp4, out_path)

    return dict(
        engine="builtin",
        preset=preset,
        output=str(out_path),
        width=width,
        height=height,
        fps=fps,
        frames=frames,
        seconds=round(frames / fps, 3),
    )


def render_manim(scene_file: Path, scene_class: str, out_path: Path, *, width: int, height: int, fps: int, frames: int) -> dict:
    manim_bin = shutil.which("manim")
    if not manim_bin:
        raise RuntimeError(
            "Manim is not installed in PATH. Install optional Manim (`pip install manim`) "
            "or use `--engine builtin` / `--engine auto` for zero-dependency 3Blue1Brown-style math renders."
        )
    if not scene_file.is_file():
        raise FileNotFoundError(f"Manim scene file not found: {scene_file}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="manim-render-") as td:
        media_dir = Path(td) / "media"
        cmd = [
            manim_bin, "render",
            "--media_dir", str(media_dir),
            "--fps", str(fps),
            "--resolution", f"{width},{height}",
            "-o", "scene.mp4",
            str(scene_file), scene_class,
        ]
        subprocess.run(cmd, check=True)
        rendered = next(media_dir.rglob("scene.mp4"), None)
        if not rendered:
            raise RuntimeError("Manim completed but scene.mp4 was not produced")
        # Normalize to exact frame count & BT.709 delivery format.
        tmp_out = Path(td) / "final.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-v", "error", "-i", str(rendered),
            "-vf", f"fps={fps},scale={width}:{height}:force_original_aspect_ratio=decrease,"
                   f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=#131A17,"
                   f"tpad=stop_mode=clone:stop_duration=30,trim=end_frame={frames},setpts=N/({fps}*TB)",
            "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "16",
            "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709",
            "-color_trc", "bt709", "-color_range", "tv", "-movflags", "+faststart",
            str(tmp_out),
        ], check=True)
        shutil.copy2(tmp_out, out_path)
    return dict(
        engine="manim",
        scene=f"{scene_file}:{scene_class}",
        output=str(out_path),
        width=width,
        height=height,
        fps=fps,
        frames=frames,
        seconds=round(frames / fps, 3),
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="List built-in mathematical animation presets and Manim status")
    rd = sub.add_parser("render", help="Render a math animation shot")
    rd.add_argument("spec", type=Path, nargs="?", help="Optional JSON spec file")
    rd.add_argument("--project", type=Path, help="Project directory containing edit.json & timeline.json")
    rd.add_argument("--shot", help="Shot id in edit.json with math_motion config")
    rd.add_argument("--engine", choices=["auto", "builtin", "manim"], default="auto")
    rd.add_argument("--preset", choices=list(PRESETS), default="complex-plane")
    rd.add_argument("--scene-file", type=Path, help="Python file containing a Manim Scene subclass")
    rd.add_argument("--scene-class", help="Manim Scene subclass name")
    rd.add_argument("--title", default="")
    rd.add_argument("--subtitle", default="")
    rd.add_argument("--accent", default="#EDA961")
    rd.add_argument("--seconds", type=float, default=5.0)
    rd.add_argument("--frames", type=int)
    rd.add_argument("--fps", type=int, default=30)
    rd.add_argument("--width", type=int, default=1920)
    rd.add_argument("--height", type=int, default=1080)
    rd.add_argument("--out", type=Path, help="Output MP4 path (optional with --project/--shot)")
    args = ap.parse_args()

    if args.cmd == "list":
        print(json.dumps({
            "manim_available": bool(shutil.which("manim")),
            "manim_install_hint": "Optional: pip install manim (zero-dependency --engine builtin works out of the box)",
            "presets": PRESETS,
        }, ensure_ascii=False, indent=2))
        return

    spec: dict = {}
    width, height, fps = args.width, args.height, args.fps
    frames = args.frames
    out_path = args.out

    if args.project and args.shot:
        base = args.project.resolve()
        edit = json.loads((base / "edit.json").read_text(encoding="utf-8"))
        tl = json.loads((base / "timeline.json").read_text(encoding="utf-8"))
        shot = next((s for s in edit["shots"] if s["id"] == args.shot), None)
        if not shot or "math_motion" not in shot:
            raise SystemExit(f"Shot {args.shot} has no math_motion configuration")
        timing = next(t for t in tl["shots"] if t["id"] == args.shot)
        spec = dict(shot["math_motion"])
        width, height = edit.get("size", [1920, 1080])
        fps = int(tl["fps"])
        frames = int(timing["frames"])
        out_path = out_path or (base / shot["visual"])
    elif args.spec:
        spec = json.loads(args.spec.read_text(encoding="utf-8"))
        width = int(spec.get("width", width))
        height = int(spec.get("height", height))
        fps = int(spec.get("fps", fps))
        frames = int(spec.get("frames", frames or round(float(spec.get("seconds", args.seconds)) * fps)))
    else:
        spec = {"preset": args.preset, "accent": args.accent}
        if args.title:
            spec["title"] = args.title
        if args.subtitle:
            spec["subtitle"] = args.subtitle
        frames = frames or max(2, round(args.seconds * fps))

    if not out_path:
        raise SystemExit("Specify --out path.mp4 (or use --project and --shot)")
    frames = int(frames or max(2, round(args.seconds * fps)))

    engine = spec.get("engine", args.engine)
    scene_file = Path(spec["scene_file"]) if spec.get("scene_file") else args.scene_file
    scene_class = spec.get("scene_class") or args.scene_class
    use_manim = (engine == "manim") or (engine == "auto" and scene_file and scene_class and shutil.which("manim"))

    if use_manim:
        if not scene_file or not scene_class:
            raise SystemExit("Manim engine requires --scene-file and --scene-class")
        res = render_manim(scene_file, scene_class, out_path, width=width, height=height, fps=fps, frames=frames)
    else:
        res = render_builtin(spec, out_path, width=width, height=height, fps=fps, frames=frames)
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
