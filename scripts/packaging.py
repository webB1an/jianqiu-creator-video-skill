#!/usr/bin/env python3
"""Packaging-First workflow: generate 3 A/B/C high-CTR covers, run 160px mobile legibility checks,
and audit Cover <-> First-5-Seconds payoff alignment.

Usage:
  packaging.py draft --project videos/my-film --out qa/packaging
  packaging.py audit --project videos/my-film
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import subprocess
import tempfile

import numpy as np
from PIL import Image, ImageDraw, ImageOps

import covers
import story_craft


def _load_source_image(project: Path, edit: dict, cfg: dict) -> Image.Image:
    source_name = cfg.get("image") or (edit["shots"][0]["visual"] if edit.get("shots") else None)
    if not source_name:
        raise ValueError("No cover source image or opening shot visual available")
    source = (project / source_name).resolve() if not Path(source_name).is_absolute() else Path(source_name)
    if not source.is_file():
        raise FileNotFoundError(f"Cover source not found: {source}")
    if source.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
        with Image.open(source) as im:
            return ImageOps.exif_transpose(im).convert("RGB")
    with tempfile.TemporaryDirectory() as td:
        still = Path(td) / "frame.png"
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", str(cfg.get("time", 0.5)),
                        "-i", str(source), "-frames:v", "1", str(still)], check=True)
        with Image.open(still) as im:
            return im.convert("RGB")


def legibility_160px(img_path: Path, lines: list[str]) -> dict:
    """Downscale 16:9 cover to 160x90px mobile-feed size and measure real readability & contrast."""
    with Image.open(img_path) as im:
        full = im.convert("RGB")
        tiny = full.resize((160, 90), Image.Resampling.LANCZOS)
    arr = np.asarray(tiny, dtype=np.float32)
    # Relative luminance (Rec.709)
    lum = 0.2126 * arr[:, :, 0] + 0.7152 * arr[:, :, 1] + 0.0722 * arr[:, :, 2]

    # Left-62% headline zone (standard 16:9 cover layout)
    text_zone = lum[18:82, 8:104]
    p95 = float(np.percentile(text_zone, 96))
    p15 = float(np.percentile(text_zone, 18))
    # WCAG-style contrast ratio approximation (normalized 0..1 with 0.05 floor)
    contrast_ratio = round(((p95 / 255.0) + 0.05) / ((p15 / 255.0) + 0.05), 2)

    # Subject-zone clarity (right 42%)
    subj = lum[10:80, 92:154]
    subj_std = float(np.std(subj))

    # Objective pixel & typography legibility gates at 160x90 mobile feed width (no synthetic point score).
    clean_lines = [l.strip() for l in (lines or []) if l.strip()]
    max_line_chars = max((len(l) for l in clean_lines), default=0)
    total_chars = sum(len(l) for l in clean_lines)

    gates = {
        "contrast_wcag_aa": contrast_ratio >= 4.5,
        "line_length_mobile": max_line_chars <= 7,
        "total_chars_mobile": total_chars <= 14,
        "subject_separation": subj_std >= 18.0,
    }
    failed_gates = [k for k, ok in gates.items() if not ok]
    notes = []
    if not gates["contrast_wcag_aa"]:
        notes.append(f"160px 缩略图文字对比度偏低（{contrast_ratio}:1 < 4.5:1），建议加深压暗或改用 high-contrast 样式")
    elif contrast_ratio < 6.5:
        notes.append(f"160px 对比度达标但偏临界（{contrast_ratio}:1），可进一步拉开高光字与暗底反差")
    if not gates["line_length_mobile"]:
        notes.append(f"单行标题达 {max_line_chars} 字，在手机信息流 160px 宽度下字号偏小；建议压缩到每行 ≤6–7 字")
    if not gates["total_chars_mobile"]:
        notes.append(f"封面总字数 {total_chars} 字偏密，建议砍到 6–12 个视觉重字，其余留给视频标题")
    if not gates["subject_separation"]:
        notes.append("右侧主视觉对比层次偏平（std < 18），建议拉近特写主体或加 callout_at 圈选焦点")

    passed = gates["contrast_wcag_aa"] and gates["line_length_mobile"] and gates["total_chars_mobile"]
    return dict(
        thumbnail_size=[160, 90],
        contrast_ratio=contrast_ratio,
        subject_contrast_std=round(subj_std, 1),
        max_line_chars=max_line_chars,
        total_chars=total_chars,
        gates=gates,
        failed_gates=failed_gates,
        passed=passed,
        notes=notes,
        tiny_image=tiny,
    )


def _punch_lines(title: str) -> list[str]:
    clean = (title or "深度拆解").strip()
    if len(clean) <= 6:
        return [clean]
    if len(clean) <= 12:
        mid = (len(clean) + 1) // 2
        return [clean[:mid], clean[mid:]]
    # Compress long title into two crisp punch lines (max 6 chars each)
    parts = [p for p in clean.replace("：", " ").replace("，", " ").replace("？", " ").split() if p]
    if len(parts) >= 2:
        return [parts[0][:6], parts[-1][:6]]
    return [clean[:6], clean[6:12]]


def draft_variants(project: Path, out_dir: Path, *, apply: bool = False, variant_name: str | None = None) -> dict:
    """Generate 3 A/B/C cover variants, verify each at 160px mobile scale, save sheet, and optionally write cover.json."""
    project = project.resolve()
    edit = json.loads((project / "edit.json").read_text(encoding="utf-8"))
    cov_path = project / "cover.json"
    pkg_path = project / "packaging.json"
    base_cfg = json.loads(cov_path.read_text(encoding="utf-8")) if cov_path.is_file() else {}
    pkg_cfg = json.loads(pkg_path.read_text(encoding="utf-8")) if pkg_path.is_file() else {}

    title = pkg_cfg.get("headline") or edit.get("title") or "深度拆解"
    base_lines = base_cfg.get("lines") or _punch_lines(title)
    short_lines = pkg_cfg.get("punch_lines") or _punch_lines(title)
    kicker = base_cfg.get("kicker") or pkg_cfg.get("kicker") or "深度解析"
    badge = base_cfg.get("badge") or pkg_cfg.get("badge") or "实证拆解"
    focus = base_cfg.get("focus")
    if not focus:
        # Auto-detect focal point on cover image when not explicitly provided.
        try:
            import auto_focus
            src_name = base_cfg.get("image") or (edit["shots"][0]["visual"] if edit.get("shots") else None)
            if src_name:
                src_p = (project / src_name).resolve() if not Path(src_name).is_absolute() else Path(src_name)
                if src_p.is_file():
                    focus = auto_focus.analyze_media(src_p)["focal_point"]
        except Exception:
            focus = None
    focus = focus or [0.55, 0.45]

    src = _load_source_image(project, edit, base_cfg)
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    variants_spec = [
        ("A_editorial", dict(base_cfg, style="editorial", lines=base_lines, kicker=kicker, badge=badge, focus=focus)),
        ("B_high_contrast", dict(
            base_cfg, style="high-contrast", lines=short_lines, kicker=kicker, badge=badge,
            focus=focus, highlight_line=len(short_lines) - 1,
        )),
        ("C_callout_focus", dict(
            base_cfg, style="high-contrast", lines=short_lines, kicker=kicker,
            badge=pkg_cfg.get("curiosity_badge") or "关键实证",
            focus=focus, highlight_line=0, callout_at=base_cfg.get("callout_at", [0.74, 0.46]),
        )),
    ]

    results = []
    cfgs_by_name = {}
    sheet = Image.new("RGB", (160 * 3 + 40, 126), (19, 26, 23))
    sdraw = ImageDraw.Draw(sheet)

    for idx, (name, cfg) in enumerate(variants_spec):
        img = covers.cover(src, cfg, "16x9", edit.get("comparison_roles"))
        jpg_path = out_dir / f"{name}.jpg"
        img.save(jpg_path, quality=94, subsampling=0)
        leg = legibility_160px(jpg_path, cfg["lines"])
        tiny = leg.pop("tiny_image")
        x = 10 + idx * 170
        sheet.paste(tiny, (x, 8))
        tag = "PASS" if leg["passed"] else "WARN"
        sdraw.text((x + 4, 102), f"{name[:8]} {leg['contrast_ratio']:.1f}:1 {tag}",
                   fill=(237, 169, 97) if leg["passed"] else (220, 110, 95))
        cfgs_by_name[name] = cfg
        results.append(dict(variant=name, file=str(jpg_path), style=cfg["style"], lines=cfg["lines"], legibility=leg))

    sheet_path = out_dir / "mobile-160px-sheet.png"
    sheet.save(sheet_path)
    results.sort(key=lambda r: (
        not r["legibility"]["passed"],
        len(r["legibility"]["failed_gates"]),
        -r["legibility"]["contrast_ratio"],
    ))

    # Also compute opening 0-5s payoff audit
    tl_path = project / "timeline.json"
    tl = json.loads(tl_path.read_text(encoding="utf-8")) if tl_path.is_file() else None
    sc = story_craft.analyze_script(edit, tl, cover_cfg=base_cfg or {"lines": short_lines}, packaging_cfg=pkg_cfg)

    chosen = variant_name if (variant_name and variant_name in cfgs_by_name) else results[0]["variant"]
    applied_cover = None
    if apply:
        chosen_cfg = dict(cfgs_by_name[chosen])
        if "image" not in chosen_cfg and edit.get("shots"):
            chosen_cfg["image"] = edit["shots"][0]["visual"]
        cov_path.write_text(json.dumps(chosen_cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        applied_cover = str(cov_path)

    report = dict(
        project=str(project),
        recommended_variant=results[0]["variant"],
        selected_variant=chosen,
        applied_cover_json=applied_cover,
        mobile_preview_sheet=str(sheet_path),
        variants=results,
        opening_5s_payoff=sc.get("cover_opening_payoff"),
    )
    (out_dir / "packaging-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def audit_packaging(project: Path) -> dict:
    project = project.resolve()
    edit = json.loads((project / "edit.json").read_text(encoding="utf-8"))
    tl = json.loads((project / "timeline.json").read_text(encoding="utf-8")) if (project / "timeline.json").is_file() else None
    cov = json.loads((project / "cover.json").read_text(encoding="utf-8")) if (project / "cover.json").is_file() else {}
    pkg = json.loads((project / "packaging.json").read_text(encoding="utf-8")) if (project / "packaging.json").is_file() else {}
    sc = story_craft.analyze_script(edit, tl, cover_cfg=cov or None, packaging_cfg=pkg or None)

    cover_jpg = next((p for p in (project / "out/covers/16x9.jpg", project / "qa/packaging/B_high_contrast.jpg") if p.is_file()), None)
    leg = None
    if cover_jpg:
        leg = legibility_160px(cover_jpg, cov.get("lines") or _punch_lines(edit.get("title", "")))
        leg.pop("tiny_image", None)
    return dict(
        project=str(project),
        cover_file=str(cover_jpg) if cover_jpg else None,
        legibility_160px=leg,
        opening_5s_payoff=sc.get("cover_opening_payoff"),
        throat_clears=sc.get("throat_clears", []),
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    dr = sub.add_parser("draft", help="Generate A/B/C cover concepts and 160px mobile legibility sheet")
    dr.add_argument("--project", type=Path, default=Path("."))
    dr.add_argument("--out", type=Path, default=Path("qa/packaging"))
    dr.add_argument("--apply", action="store_true", help="Write the recommended (or --variant) cover config into cover.json")
    dr.add_argument("--variant", choices=["A_editorial", "B_high_contrast", "C_callout_focus"], help="Explicit variant when using --apply")
    au = sub.add_parser("audit", help="Audit cover readability at 160px and first-5s payoff match")
    au.add_argument("--project", type=Path, default=Path("."))
    args = ap.parse_args()
    if args.cmd == "draft":
        out = args.out if args.out.is_absolute() else (args.project.resolve() / args.out)
        print(json.dumps(draft_variants(args.project, out, apply=args.apply, variant_name=args.variant), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(audit_packaging(args.project), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
