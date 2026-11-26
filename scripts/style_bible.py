#!/usr/bin/env python3
"""Create and manage a project Visual Style Bible (palette, lighting, finish & visual swatch reference).

Locks color palette, lighting direction, optical finish and AI B-roll prompt style across an entire
production so live footage, Remotion graphics and AI-generated B-roll share one cohesive cinema universe.

Usage:
  style_bible.py list
  style_bible.py init --project videos/my-film --preset jianqiu-dark-editorial
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PRESETS = {
    "jianqiu-dark-editorial": {
        "name": "见秋深墨科技纪录风",
        "palette": {
            "background": "#131A17",
            "surface": "#1D2722",
            "accent": "#EDA961",
            "secondary": "#8AD7CD",
            "ink": "#F4EDDD",
        },
        "lighting": "directional warm-amber rim light against cool teal studio shadows, shallow depth of field",
        "lens": "50mm anamorphic documentary prime, subtle organic film grain, clean highlight roll-off",
        "grade": {"look": "night-slate"},
        "finish": {"vignette": 0.22, "grain": 0.12, "sharpen": 0.18},
        "prompt_suffix": (
            "editorial documentary cinematography, deep forest-ink #131A17 shadows, warm amber #EDA961 "
            "key practical lights, muted cyan-teal #8AD7CD accents, 50mm shallow depth of field, "
            "subtle smooth camera push, no text, no watermark, 24fps film cadence"
        ),
        "negative_prompt": "neon cyberpunk rainbow, oversaturated stock look, text overlay, watermark, jittery handheld",
    },
    "vox-paper-studio": {
        "name": "Vox 暖纸杂志拼贴风",
        "palette": {
            "background": "#F4EDDD",
            "surface": "#ECE3CE",
            "accent": "#82501D",
            "secondary": "#176663",
            "ink": "#131A17",
        },
        "lighting": "soft overhead editorial studio daylight, tactile warm paper grain, clean tabletop shadows",
        "lens": "35mm editorial macro tabletop, crisp ink edges, natural warm contrast",
        "grade": {"look": "editorial-warm"},
        "finish": {"vignette": 0.14, "grain": 0.10, "sharpen": 0.15},
        "prompt_suffix": (
            "Vox editorial studio aesthetic, warm cream archival paper lighting, deep teal #176663 and "
            "ochre #82501D accents, tactile physical craft feel, clean controlled camera movement, no text"
        ),
        "negative_prompt": "dark neon, heavy vignette, plastic 3D CGI gloss, text overlay, watermark",
    },
    "cinema-tech-cool": {
        "name": "影视飓风精密科技冷调风",
        "palette": {
            "background": "#0E1418",
            "surface": "#172128",
            "accent": "#68BBE3",
            "secondary": "#EDA961",
            "ink": "#F2F5F7",
        },
        "lighting": "precision cool blue-steel overhead lab lighting with crisp specular reflections and amber status indicators",
        "lens": "macro cinema slider, crisp focus pulls, high dynamic range contrast",
        "grade": {"look": "tech-cool"},
        "finish": {"vignette": 0.18, "grain": 0.08, "sharpen": 0.24, "bloom": 0.12},
        "prompt_suffix": (
            "high-budget tech documentary cinematography, precision steel-blue lab lighting, clean macro "
            "industrial details, controlled motorized slider motion, subtle amber indicator accents, no text"
        ),
        "negative_prompt": "blurry amateur footage, warm sepia cast, cartoonish CGI, text overlay, watermark",
    },
    "warm-documentary": {
        "name": "人物档案暖光纪录片风",
        "palette": {
            "background": "#1A1612",
            "surface": "#28221B",
            "accent": "#D49A79",
            "secondary": "#99B88C",
            "ink": "#F4EDDD",
        },
        "lighting": "golden-hour window key light, intimate warm shadows, gentle archival film texture",
        "lens": "35mm documentary handheld micro-drift, natural skin tones, soft highlight halation",
        "grade": {"look": "cinema-neutral"},
        "finish": {"vignette": 0.24, "grain": 0.16, "bloom": 0.14},
        "prompt_suffix": (
            "intimate documentary cinematography, warm natural window light, gentle archival film texture, "
            "human-scale framing, subtle breathing camera movement, natural skin tones, no text"
        ),
        "negative_prompt": "sterile cold fluorescent, synthetic CGI look, text overlay, watermark",
    },
}

FONT_PATHS = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
]


def _font(size: int):
    for p in FONT_PATHS:
        if Path(p).is_file():
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _hex_rgb(code: str) -> tuple[int, int, int]:
    s = code.strip().lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def render_reference_board(bible: dict, out_png: Path) -> Path:
    """Render a 1920x1080 Visual Style Bible reference card usable as an AI style-ref image."""
    w, h = 1920, 1080
    pal = bible["palette"]
    bg = _hex_rgb(pal["background"])
    surf = _hex_rgb(pal["surface"])
    acc = _hex_rgb(pal["accent"])
    sec = _hex_rgb(pal["secondary"])
    ink = _hex_rgb(pal["ink"])

    im = Image.new("RGB", (w, h), bg)
    draw = ImageDraw.Draw(im)

    # Top tonal light-direction gradient strip (visual cue for AI style-ref models)
    for x in range(w):
        u = x / max(1, w - 1)
        c = tuple(int(bg[i] * (1 - u * 0.55) + acc[i] * (u * 0.35) + sec[i] * ((1 - u) * 0.20)) for i in range(3))
        draw.line([(x, 0), (x, 220)], fill=c)

    # Header
    draw.text((88, 66), f"STYLE BIBLE · {bible['name']}", font=_font(46), fill=ink)
    draw.text((88, 134), bible["lighting"], font=_font(24), fill=ink)

    # Swatches
    items = list(pal.items())
    sw_w = (w - 176 - 28 * (len(items) - 1)) // len(items)
    for idx, (role, code) in enumerate(items):
        x0 = 88 + idx * (sw_w + 28)
        y0 = 280
        draw.rounded_rectangle([x0, y0, x0 + sw_w, y0 + 320], radius=18, fill=_hex_rgb(code), outline=ink, width=2)
        draw.rounded_rectangle([x0, y0 + 235, x0 + sw_w, y0 + 320], radius=14, fill=surf)
        draw.text((x0 + 20, y0 + 250), role.upper(), font=_font(22), fill=acc)
        draw.text((x0 + 20, y0 + 282), code, font=_font(24), fill=ink)

    # Lens & Finish panel
    draw.rounded_rectangle([88, 650, w - 88, 990], radius=18, fill=surf, outline=acc, width=2)
    draw.text((126, 686), "LENS & TEXTURE LOCK", font=_font(26), fill=acc)
    draw.text((126, 732), bible["lens"], font=_font(24), fill=ink)
    draw.text((126, 796), "AI B-ROLL STYLE PROMPT LOCK", font=_font(26), fill=sec)
    prompt = bible["prompt_suffix"]
    # Wrap prompt across two lines
    mid = len(prompt) // 2
    split_at = prompt.find(" ", mid) if " " in prompt[mid:] else mid
    draw.text((126, 842), prompt[:split_at], font=_font(22), fill=ink)
    draw.text((126, 878), prompt[split_at:].strip(), font=_font(22), fill=ink)
    draw.text((126, 932), f"GRADE: {json.dumps(bible['grade'])}    FINISH: {json.dumps(bible['finish'])}", font=_font(21), fill=acc)

    out_png.parent.mkdir(parents=True, exist_ok=True)
    im.save(out_png)
    return out_png


def render_mood_plate(bible: dict, out_png: Path) -> Path:
    """Render a clean 1920x1080 textless lighting/color atmosphere plate (safe for mood refs, never swatch text)."""
    import numpy as np
    w, h = 1920, 1080
    pal = bible["palette"]
    bg = np.array(_hex_rgb(pal["background"]), dtype=np.float32)
    surf = np.array(_hex_rgb(pal["surface"]), dtype=np.float32)
    acc = np.array(_hex_rgb(pal["accent"]), dtype=np.float32)
    sec = np.array(_hex_rgb(pal["secondary"]), dtype=np.float32)

    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    xn, yn = xx / w, yy / h
    img = bg[None, None, :] * (1.0 - 0.35 * yn[:, :, None]) + surf[None, None, :] * (0.35 * yn[:, :, None])

    # Directional rim glow + complementary fill glow (matches lighting direction without any UI/text artifacts)
    rim = np.exp(-(((xn - 0.78) / 0.32) ** 2 + ((yn - 0.28) / 0.38) ** 2))
    fill = np.exp(-(((xn - 0.22) / 0.40) ** 2 + ((yn - 0.72) / 0.42) ** 2))
    img += rim[:, :, None] * (acc[None, None, :] - bg[None, None, :]) * 0.42
    img += fill[:, :, None] * (sec[None, None, :] - bg[None, None, :]) * 0.24

    # Subtle optical vignette + deterministic organic film grain
    rad2 = ((xn - 0.5) * 1.45) ** 2 + ((yn - 0.5) * 1.45) ** 2
    vig = np.clip(1.0 - 0.32 * rad2, 0.55, 1.0)
    rng = np.random.default_rng(17)
    grain = rng.normal(0.0, 2.2, (h, w, 1)).astype(np.float32)
    img = np.clip(img * vig[:, :, None] + grain, 0, 255).astype(np.uint8)

    out_png.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(img, mode="RGB").save(out_png)
    return out_png


def init_bible(project: Path, preset: str, *, apply_to_edit: bool = False) -> dict:
    if preset not in PRESETS:
        raise ValueError(f"Unknown style bible preset: {preset}")
    project = project.resolve()
    bible = dict(schema=1, preset=preset, **PRESETS[preset])
    ref_rel = "assets/style-bible.png"
    mood_rel = "assets/style-mood-plate.png"
    bible["reference_image"] = ref_rel
    bible["mood_plate_image"] = mood_rel
    render_reference_board(bible, project / ref_rel)
    render_mood_plate(bible, project / mood_rel)
    bible_path = project / "style-bible.json"
    bible_path.write_text(json.dumps(bible, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    edit_path = project / "edit.json"
    if apply_to_edit and edit_path.is_file():
        edit = json.loads(edit_path.read_text(encoding="utf-8"))
        edit.setdefault("grade", bible["grade"])
        edit.setdefault("finish", bible["finish"])
        edit["style_bible"] = "style-bible.json"
        edit_path.write_text(json.dumps(edit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return dict(
        style_bible=str(bible_path), reference_image=str(project / ref_rel),
        mood_plate_image=str(project / mood_rel), preset=preset, applied_to_edit=apply_to_edit,
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="List built-in visual style bible presets")
    ini = sub.add_parser("init", help="Create style-bible.json and assets/style-bible.png in a project")
    ini.add_argument("--project", type=Path, default=Path("."))
    ini.add_argument("--preset", choices=list(PRESETS), default="jianqiu-dark-editorial")
    ini.add_argument("--apply-edit", action="store_true", help="Also set default grade/finish in edit.json")
    args = ap.parse_args()
    if args.cmd == "list":
        print(json.dumps({k: {"name": v["name"], "palette": v["palette"]} for k, v in PRESETS.items()}, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(init_bible(args.project, args.preset, apply_to_edit=args.apply_edit), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
