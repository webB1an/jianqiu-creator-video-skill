#!/usr/bin/env python3
"""Offline Johnny Harris / Vox-style animated tactical map & 2.5D geo-flight renderer (zero API key).

Renders smooth camera flights, 2.5D map tilt, animated glowing supply-chain/history arcs,
pulsing hub pins, highlighted regions and live lat/lon HUD in BT.709 H.264 1080p.

Usage:
  geo_map.py list
  geo_map.py render --preset semiconductor-chain --seconds 5 --out visuals/map.mp4
  geo_map.py render spec.json --out visuals/map.mp4
  geo_map.py render --project videos/my-film --shot s05
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

SKILL = Path(__file__).resolve().parents[1]
GEO_DATA = SKILL / "assets/geo/world-simple.json"

PRESETS = {
    "semiconductor-chain": {
        "title": "全球高端算力与光刻供应链",
        "subtitle": "从阿姆斯特丹/埃因霍温到新竹与硅谷的跨洋协同",
        "theme": "dark",
        "tilt": 16,
        "waypoints": [
            {"at": [10.0, 48.0], "zoom": 2.4},
            {"at": [118.0, 26.0], "zoom": 2.8},
            {"at": [-120.0, 38.0], "zoom": 2.6},
        ],
        "pins": ["Eindhoven", "Hsinchu", "Silicon Valley", "Tokyo"],
        "highlight_countries": ["NLD", "TWN", "JPN", "USA"],
        "routes": [
            {"from": "Eindhoven", "to": "Hsinchu", "label": "EUV 光刻机", "color": "#EDA961"},
            {"from": "Tokyo", "to": "Hsinchu", "label": "光刻胶材料", "color": "#8AD7CD"},
            {"from": "Hsinchu", "to": "Silicon Valley", "label": "先进封装芯片", "color": "#EDA961"},
        ],
    },
    "riemann-history": {
        "title": "从哥廷根到旧金山：167 年的数学接力",
        "subtitle": "1859 黎曼手稿 → 普林斯顿高等研究院 → 2026 AI 形式化协作",
        "theme": "dark",
        "tilt": 14,
        "waypoints": [
            {"at": [10.0, 51.5], "zoom": 3.0},
            {"at": [-45.0, 46.0], "zoom": 1.8},
            {"at": [-115.0, 38.0], "zoom": 2.7},
        ],
        "pins": ["Gottingen", "Princeton", "San Francisco"],
        "highlight_countries": ["DEU", "USA"],
        "routes": [
            {"from": "Gottingen", "to": "Princeton", "label": "1859 → 20 世纪解析数论", "color": "#8AD7CD"},
            {"from": "Princeton", "to": "San Francisco", "label": "2026 AI × Lean4", "color": "#EDA961"},
        ],
    },
    "global-ai-hubs": {
        "title": "全球前沿大模型研发枢纽",
        "subtitle": "北美、欧洲与东亚核心算力与实验室连线",
        "theme": "dark",
        "tilt": 12,
        "waypoints": [
            {"at": [-95.0, 38.0], "zoom": 1.7},
            {"at": [20.0, 46.0], "zoom": 1.5},
            {"at": [116.0, 32.0], "zoom": 2.2},
        ],
        "pins": ["San Francisco", "London", "Paris", "Beijing", "Shanghai", "Tokyo"],
        "highlight_countries": ["USA", "GBR", "FRA", "CHN", "JPN"],
        "routes": [
            {"from": "San Francisco", "to": "London", "label": "跨大西洋协作", "color": "#8AD7CD"},
            {"from": "San Francisco", "to": "Beijing", "label": "开源模型竞速", "color": "#EDA961"},
            {"from": "Beijing", "to": "Shanghai", "label": "国内算力集群", "color": "#EDA961"},
        ],
    },
}

FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
]
SERIF_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]


def _load_font(size: int, *, serif: bool = False):
    for p in (SERIF_CANDIDATES if serif else FONT_CANDIDATES):
        if Path(p).is_file():
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _hex_bgr(code: str, default=(97, 169, 237)):
    s = (code or "").strip().lstrip("#")
    if len(s) == 6:
        return (int(s[4:6], 16), int(s[2:4], 16), int(s[0:2], 16))
    return default


def _hex_rgb(code: str, default=(237, 169, 97)):
    b, g, r = _hex_bgr(code, (default[2], default[1], default[0]))
    return (r, g, b)


def _smootherstep(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


def _mercator_y(lat: float) -> float:
    lat = max(-72.0, min(78.0, float(lat)))
    rad = math.radians(lat)
    return math.log(math.tan(math.pi / 4.0 + rad / 2.0))


def _resolve_coord(val, cities: dict) -> tuple[float, float, str]:
    if isinstance(val, str):
        # Match English key or Chinese name
        if val in cities:
            c = cities[val]
            return float(c["lon"]), float(c["lat"]), c.get("zh", val)
        for k, c in cities.items():
            if k.lower() == val.lower() or c.get("zh") == val:
                return float(c["lon"]), float(c["lat"]), c.get("zh", k)
        raise ValueError(f"Unknown city name in geo_map: {val}")
    if isinstance(val, (list, tuple)) and len(val) == 2:
        return float(val[0]), float(val[1]), f"{val[1]:.1f}°, {val[0]:.1f}°"
    if isinstance(val, dict) and "lon" in val and "lat" in val:
        return float(val["lon"]), float(val["lat"]), str(val.get("label", ""))
    raise ValueError(f"Invalid map coordinate: {val}")


def _interpolate_waypoints(waypoints: list[dict], progress: float, cities: dict) -> tuple[float, float, float]:
    if not waypoints:
        return 10.0, 28.0, 1.3
    if len(waypoints) == 1:
        lon, lat, _ = _resolve_coord(waypoints[0]["at"], cities)
        return lon, lat, float(waypoints[0].get("zoom", 1.8))
    pos = progress * (len(waypoints) - 1)
    idx = min(len(waypoints) - 2, int(pos))
    local = _smootherstep(pos - idx)
    lon0, lat0, _ = _resolve_coord(waypoints[idx]["at"], cities)
    lon1, lat1, _ = _resolve_coord(waypoints[idx + 1]["at"], cities)
    z0 = float(waypoints[idx].get("zoom", 1.8))
    z1 = float(waypoints[idx + 1].get("zoom", 1.8))
    # Smooth shortest-path or direct longitude interpolation
    dlon = lon1 - lon0
    if dlon > 180:
        dlon -= 360
    elif dlon < -180:
        dlon += 360
    lon = lon0 + dlon * local
    if lon > 180:
        lon -= 360
    elif lon < -180:
        lon += 360
    lat = lat0 + (lat1 - lat0) * local
    zoom = z0 + (z1 - z0) * local
    return lon, lat, zoom


def _great_circle_points(lon0: float, lat0: float, lon1: float, lat1: float, steps: int = 56) -> list[tuple[float, float]]:
    """Compute true 3D spherical great-circle geodesic coordinates between two lat/lon hubs."""
    p0_lat, p0_lon = math.radians(lat0), math.radians(lon0)
    p1_lat, p1_lon = math.radians(lat1), math.radians(lon1)
    v0 = np.array([math.cos(p0_lat) * math.cos(p0_lon), math.cos(p0_lat) * math.sin(p0_lon), math.sin(p0_lat)])
    v1 = np.array([math.cos(p1_lat) * math.cos(p1_lon), math.cos(p1_lat) * math.sin(p1_lon), math.sin(p1_lat)])
    dot = float(np.clip(np.dot(v0, v1), -1.0, 1.0))
    omega = math.acos(dot)
    pts = []
    if omega < 1e-4:
        return [(lon0, lat0), (lon1, lat1)]
    sin_o = math.sin(omega)
    for s in range(steps + 1):
        u = s / steps
        v = (math.sin((1.0 - u) * omega) / sin_o) * v0 + (math.sin(u * omega) / sin_o) * v1
        lat = math.degrees(math.asin(float(np.clip(v[2], -1.0, 1.0))))
        lon = math.degrees(math.atan2(float(v[1]), float(v[0])))
        pts.append((lon, lat))
    return pts


def _resolve_highlighted_iso3(spec: dict, countries: dict) -> set[str]:
    """Match highlight_countries / highlight_regions against ISO3, English name, or Chinese name."""
    raw = list(spec.get("highlight_countries") or []) + list(spec.get("highlight_regions") or [])
    matched = set()
    for token in raw:
        if not isinstance(token, str) or not token.strip():
            continue
        t = token.strip()
        tu = t.upper()
        if tu in countries:
            matched.add(tu)
            continue
        tl = t.lower()
        for iso3, info in countries.items():
            if info.get("zh") == t or info.get("name", "").lower() == tl:
                matched.add(iso3)
    return matched


def render_geo_map(spec: dict, out_path: Path, *, width: int = 1920, height: int = 1080, fps: int = 30, frames: int = 150) -> dict:
    if isinstance(spec.get("preset"), str) and spec["preset"] in PRESETS:
        spec = {**PRESETS[spec["preset"]], **spec}
    world = json.loads(GEO_DATA.read_text(encoding="utf-8"))
    cities = world["cities"]
    landmasses = world["landmasses"]
    countries = world.get("countries") or {}

    theme = spec.get("theme", "dark")
    dark = theme != "paper"
    ocean_bgr = (21, 24, 17) if dark else (200, 223, 233)
    land_bgr = (35, 45, 33) if dark else (224, 237, 242)
    land_hi_bgr = (54, 72, 68) if dark else (188, 214, 228)
    coast_bgr = (82, 108, 96) if dark else (120, 142, 152)
    hi_border_bgr = _hex_bgr(spec.get("accent", "#EDA961" if dark else "#82501D"))
    grid_bgr = (31, 38, 34) if dark else (182, 204, 214)
    text_rgb = (244, 237, 221) if dark else (19, 26, 23)
    muted_rgb = (169, 179, 170) if dark else (86, 96, 90)
    accent_hex = spec.get("accent", "#EDA961" if dark else "#82501D")
    accent_bgr = _hex_bgr(accent_hex)
    accent_rgb = _hex_rgb(accent_hex)
    highlight_regions = set(spec.get("highlight_regions") or [])
    highlight_iso3 = _resolve_highlighted_iso3(spec, countries)

    waypoints = spec.get("waypoints") or [{"at": [15.0, 32.0], "zoom": 1.3}, {"at": [116.4, 35.0], "zoom": 2.4}]
    pins_raw = spec.get("pins") or []
    routes_raw = spec.get("routes") or []
    tilt_deg = max(0.0, min(28.0, float(spec.get("tilt", 14.0))))

    # Pre-resolve pins & routes
    pins = [_resolve_coord(p, cities) for p in pins_raw]
    routes = []
    for r in routes_raw:
        f_lon, f_lat, f_lbl = _resolve_coord(r["from"], cities)
        t_lon, t_lat, t_lbl = _resolve_coord(r["to"], cities)
        routes.append({
            "from": (f_lon, f_lat, f_lbl),
            "to": (t_lon, t_lat, t_lbl),
            "label": r.get("label", ""),
            "color_bgr": _hex_bgr(r.get("color", accent_hex)),
            "color_rgb": _hex_rgb(r.get("color", accent_hex)),
        })

    # Precompute perspective warp matrix for 2.5D Johnny Harris map tilt
    M_tilt = None
    if tilt_deg > 0.5:
        squeeze = 0.085 * (tilt_deg / 16.0)
        src_pts = np.float32([[0, 0], [width, 0], [width, height], [0, height]])
        dst_pts = np.float32([
            [width * squeeze, height * (squeeze * 0.45)],
            [width * (1.0 - squeeze), height * (squeeze * 0.45)],
            [width * (1.0 + squeeze * 0.65), height],
            [-width * (squeeze * 0.65), height],
        ])
        M_tilt = cv2.getPerspectiveTransform(src_pts, dst_pts)

    title_font = _load_font(44, serif=True)
    sub_font = _load_font(22)
    hud_font = _load_font(21)
    pin_font = _load_font(23)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="geo-map-") as td:
        tmp_mp4 = Path(td) / "map.mp4"
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
            for f_idx in range(frames):
                p = f_idx / max(1, frames - 1)
                c_lon, c_lat, zoom = _interpolate_waypoints(waypoints, p, cities)
                cy_m = _mercator_y(c_lat)
                scale_x = (width / 360.0) * zoom
                scale_y = (width / (2.0 * math.pi)) * zoom

                def project(lon: float, lat: float, wrap_shift: float = 0.0) -> tuple[int, int]:
                    dl = (lon + wrap_shift) - c_lon
                    if dl > 180.0 and wrap_shift == 0.0:
                        dl -= 360.0
                    elif dl < -180.0 and wrap_shift == 0.0:
                        dl += 360.0
                    x = width * 0.5 + dl * scale_x
                    y = height * 0.54 - (_mercator_y(lat) - cy_m) * scale_y
                    return int(round(x)), int(round(y))

                canvas = np.full((height, width, 3), ocean_bgr, dtype=np.uint8)

                # Tactical lat/lon graticule
                for glon in range(-180, 181, 20):
                    p0 = project(glon, 74)
                    p1 = project(glon, -62)
                    if -width < p0[0] < width * 2:
                        cv2.line(canvas, p0, p1, grid_bgr, 1, cv2.LINE_AA)
                for glat in range(-60, 76, 15):
                    p0 = (0, project(c_lon, glat)[1])
                    p1 = (width, project(c_lon, glat)[1])
                    if 0 <= p0[1] < height:
                        cv2.line(canvas, p0, p1, grid_bgr, 1, cv2.LINE_AA)

                # Draw world continents & national borders (wrap copies at -360, 0, +360 so trans-Pacific flights never tear)
                hi_polys = []
                for wrap in (-360.0, 0.0, 360.0):
                    for lm in landmasses:
                        pts = []
                        for lon, lat in lm["coords"]:
                            x = int(round(width * 0.5 + ((lon + wrap) - c_lon) * scale_x))
                            y = int(round(height * 0.54 - (_mercator_y(lat) - cy_m) * scale_y))
                            pts.append([x, y])
                        arr = np.array(pts, dtype=np.int32)
                        if arr[:, 0].max() < -80 or arr[:, 0].min() > width + 80:
                            continue
                        is_hi = (lm["id"] in highlight_regions) or (lm.get("iso3") in highlight_iso3)
                        if is_hi:
                            hi_polys.append(arr)
                        else:
                            cv2.fillPoly(canvas, [arr], land_bgr, cv2.LINE_AA)
                            cv2.polylines(canvas, [arr], True, coast_bgr, 1, cv2.LINE_AA)
                for arr in hi_polys:
                    cv2.fillPoly(canvas, [arr], land_hi_bgr, cv2.LINE_AA)
                    cv2.polylines(canvas, [arr], True, hi_border_bgr, 2, cv2.LINE_AA)

                # Draw animated glowing great-circle geodesic flight / supply-chain routes
                glow_layer = np.zeros_like(canvas)
                route_labels = []
                n_routes = max(1, len(routes))
                for r_idx, rt in enumerate(routes):
                    r_start = (r_idx / n_routes) * 0.55
                    r_end = min(0.95, r_start + 0.45)
                    rp = _smootherstep((p - r_start) / max(0.05, r_end - r_start))
                    if rp <= 0.01:
                        continue
                    lon0, lat0, _ = rt["from"]
                    lon1, lat1, _ = rt["to"]
                    gc_coords = _great_circle_points(lon0, lat0, lon1, lat1, steps=56)
                    k_max = max(2, int((len(gc_coords) - 1) * rp))
                    arc_pts = []
                    for lon_u, lat_u in gc_coords[:k_max + 1]:
                        x = int(round(width * 0.5 + (((lon_u - c_lon + 180) % 360) - 180) * scale_x))
                        y = int(round(height * 0.54 - (_mercator_y(lat_u) - cy_m) * scale_y))
                        arc_pts.append((x, y))
                    # Break line segments if dateline wrap jumps > width*0.6
                    for a_pt, b_pt in zip(arc_pts, arc_pts[1:]):
                        if abs(a_pt[0] - b_pt[0]) < width * 0.55:
                            cv2.line(glow_layer, a_pt, b_pt, rt["color_bgr"], 7, cv2.LINE_AA)
                            cv2.line(canvas, a_pt, b_pt, rt["color_bgr"], 3, cv2.LINE_AA)
                    tip = arc_pts[-1]
                    cv2.circle(canvas, tip, 7, rt["color_bgr"], -1, cv2.LINE_AA)
                    if rt["label"] and rp > 0.55:
                        mid_pt = arc_pts[len(arc_pts) // 2]
                        route_labels.append((mid_pt[0], mid_pt[1] - 18, rt["label"], rt["color_rgb"]))

                if routes:
                    canvas = cv2.addWeighted(canvas, 1.0, cv2.GaussianBlur(glow_layer, (0, 0), 7.0), 0.55, 0)

                # Pulsing hub pins
                pin_draw_labels = []
                pulse = 0.5 + 0.5 * math.sin(f_idx * 0.20)
                for p_lon, p_lat, p_lbl in pins:
                    px, py = project(p_lon, p_lat)
                    if -60 <= px <= width + 60 and -60 <= py <= height + 60:
                        cv2.circle(canvas, (px, py), int(15 + 9 * pulse), accent_bgr, 2, cv2.LINE_AA)
                        cv2.circle(canvas, (px, py), 8, accent_bgr, -1, cv2.LINE_AA)
                        cv2.circle(canvas, (px, py), 3, (255, 255, 255), -1, cv2.LINE_AA)
                        pin_draw_labels.append((px, py, p_lbl))

                # Apply 2.5D perspective tilt to the map plane (before crisp 2D HUD/title overlay!)
                if M_tilt is not None:
                    canvas = cv2.warpPerspective(canvas, M_tilt, (width, height),
                                                 flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=ocean_bgr)
                    # Transform pin & route label coordinates through M_tilt so text pins stay razor sharp!
                    def warp_pt(x: int, y: int) -> tuple[int, int]:
                        vec = np.array([x, y, 1.0], dtype=np.float64)
                        res = M_tilt @ vec
                        return int(round(res[0] / res[2])), int(round(res[1] / res[2]))
                    pin_draw_labels = [(*warp_pt(px, py), lbl) for px, py, lbl in pin_draw_labels]
                    route_labels = [(*warp_pt(rx, ry), lbl, col) for rx, ry, lbl, col in route_labels]

                # Top editorial vignette & atmospheric horizon fade
                grad = np.linspace(1.0, 0.0, int(height * 0.24), dtype=np.float32)[:, None, None]
                bg_strip = np.array(ocean_bgr, dtype=np.float32)[None, None, :]
                top_h = len(grad)
                canvas[:top_h] = (canvas[:top_h].astype(np.float32) * (1.0 - 0.72 * grad) + bg_strip * (0.72 * grad)).astype(np.uint8)

                # Composite razor-sharp Pillow CJK labels, title & tactical telemetry HUD
                img = Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB))
                draw = ImageDraw.Draw(img, "RGBA")

                for px, py, lbl in pin_draw_labels:
                    if 40 < px < width - 120 and 140 < py < height - 60:
                        bb = draw.textbbox((0, 0), lbl, font=pin_font)
                        tw, th = bb[2] - bb[0], bb[3] - bb[1]
                        bx, by = px + 14, py - th // 2 - 6
                        draw.rounded_rectangle([bx - 8, by - 4, bx + tw + 10, by + th + 8], radius=6,
                                               fill=(15, 22, 19, 215) if dark else (244, 237, 221, 230),
                                               outline=(*accent_rgb, 175), width=1)
                        draw.text((bx, by), lbl, font=pin_font, fill=(*text_rgb, 250))

                for rx, ry, rlbl, rcol in route_labels:
                    if 60 < rx < width - 160 and 150 < ry < height - 60:
                        rb = draw.textbbox((0, 0), rlbl, font=hud_font)
                        rw, rh = rb[2] - rb[0], rb[3] - rb[1]
                        draw.rounded_rectangle([rx - 8, ry - 4, rx + rw + 10, ry + rh + 8], radius=6,
                                               fill=(15, 22, 19, 205) if dark else (244, 237, 221, 220),
                                               outline=(*rcol, 130), width=1)
                        draw.text((rx, ry), rlbl, font=hud_font, fill=(*rcol, 250))

                # Title & Subtitle
                title = spec.get("title", "")
                subtitle = spec.get("subtitle", "")
                if title or subtitle:
                    bar_w = int(46 + 14 * math.sin(p * math.pi * 2))
                    draw.rounded_rectangle([68, 52, 68 + bar_w, 57], radius=2, fill=(*accent_rgb, 250))
                    if title:
                        draw.text((68, 66), title, font=title_font, fill=(*text_rgb, 252))
                    if subtitle:
                        draw.text((68, 126), subtitle, font=sub_font, fill=(*muted_rgb, 238))

                # Tactical coordinate HUD (placed cleanly below top-right brand badge safe zone)
                lat_ns = f"{abs(c_lat):5.2f}°{'N' if c_lat >= 0 else 'S'}"
                lon_ew = f"{abs(c_lon):6.2f}°{'E' if c_lon >= 0 else 'W'}"
                hud_lines = [
                    (f"LAT {lat_ns}   LON {lon_ew}", accent_rgb),
                    (f"ZOOM {zoom:4.2f}x   TILT {tilt_deg:4.1f}°", (138, 215, 205) if dark else (23, 102, 99)),
                ]
                box_w, box_h = 370, 94
                x0, y0 = width - box_w - 64, max(108, int(height * 0.145))
                draw.rounded_rectangle([x0, y0, x0 + box_w, y0 + box_h], radius=12,
                                       fill=(15, 22, 19, 210) if dark else (244, 237, 221, 220),
                                       outline=(*accent_rgb, 145), width=2)
                for hi, (htxt, hcol) in enumerate(hud_lines):
                    draw.text((x0 + 20, y0 + 15 + hi * 34), htxt, font=hud_font, fill=(*hcol, 248))

                out_bgr = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
                proc.stdin.write(out_bgr.tobytes())
        finally:
            proc.stdin.close()
        rc = proc.wait()
        if rc != 0:
            raise RuntimeError(f"FFmpeg geo_map encode failed with code {rc}")
        shutil.copy2(tmp_mp4, out_path)

    return dict(
        output=str(out_path), width=width, height=height, fps=fps,
        frames=frames, seconds=round(frames / fps, 3),
        waypoints=len(waypoints), pins=len(pins), routes=len(routes),
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="List built-in geo_map presets and bundled cities")
    rd = sub.add_parser("render", help="Render an offline animated tactical map shot")
    rd.add_argument("spec", type=Path, nargs="?")
    rd.add_argument("--project", type=Path)
    rd.add_argument("--shot")
    rd.add_argument("--preset", choices=list(PRESETS), default="semiconductor-chain")
    rd.add_argument("--title")
    rd.add_argument("--subtitle")
    rd.add_argument("--theme", choices=["dark", "paper"], default="dark")
    rd.add_argument("--seconds", type=float, default=5.0)
    rd.add_argument("--frames", type=int)
    rd.add_argument("--fps", type=int, default=30)
    rd.add_argument("--width", type=int, default=1920)
    rd.add_argument("--height", type=int, default=1080)
    rd.add_argument("--out", type=Path)
    args = ap.parse_args()

    if args.cmd == "list":
        world = json.loads(GEO_DATA.read_text(encoding="utf-8"))
        print(json.dumps({
            "presets": {k: {"title": v["title"], "subtitle": v["subtitle"]} for k, v in PRESETS.items()},
            "city_count": len(world["cities"]),
            "sample_cities": list(world["cities"])[:16],
        }, ensure_ascii=False, indent=2))
        return

    w, h, fps = args.width, args.height, args.fps
    frames = args.frames
    out_path = args.out
    if args.project and args.shot:
        base = args.project.resolve()
        edit = json.loads((base / "edit.json").read_text(encoding="utf-8"))
        tl = json.loads((base / "timeline.json").read_text(encoding="utf-8"))
        shot = next((s for s in edit["shots"] if s["id"] == args.shot), None)
        if not shot or "geo_map" not in shot:
            raise SystemExit(f"Shot {args.shot} has no geo_map configuration")
        timing = next(t for t in tl["shots"] if t["id"] == args.shot)
        spec = dict(shot["geo_map"])
        w, h = edit.get("size", [1920, 1080])
        fps = int(tl["fps"])
        frames = int(timing["frames"])
        out_path = out_path or (base / shot["visual"])
    elif args.spec:
        spec = json.loads(args.spec.read_text(encoding="utf-8"))
        w = int(spec.get("width", w))
        h = int(spec.get("height", h))
        fps = int(spec.get("fps", fps))
        frames = int(spec.get("frames", frames or round(float(spec.get("seconds", args.seconds)) * fps)))
    else:
        spec = dict(PRESETS[args.preset])
        spec["theme"] = args.theme
        if args.title:
            spec["title"] = args.title
        if args.subtitle:
            spec["subtitle"] = args.subtitle
        frames = frames or max(2, round(args.seconds * fps))

    if not out_path:
        raise SystemExit("Specify --out path.mp4 (or use --project and --shot)")
    res = render_geo_map(spec, out_path, width=w, height=h, fps=fps, frames=int(frames))
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
