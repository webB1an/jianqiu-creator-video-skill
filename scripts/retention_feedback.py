#!/usr/bin/env python3
"""Ingest YouTube Studio / Bilibili audience retention curves and feed drop-off lessons back into the library.

Maps steep viewer drop-off cliffs (dR/dt) back to exact shots, visual types, transitions, spoken lines
and media-library assets in a finished project, then writes reusable retention lessons so future
productions avoid repeating patterns that lost viewers.

Usage:
  retention_feedback.py ingest retention.csv --project videos/my-film --platform bilibili [--save-library]
  retention_feedback.py lessons [--root /path/to/media-library]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import library
from narrative_audit import _shot_visual_kind, _transition_name


def _parse_curve(path: Path, total_s: float) -> list[tuple[float, float]]:
    """Parse CSV or JSON retention data into sorted (seconds, retention_pct) points."""
    raw = path.read_text(encoding="utf-8-sig").strip()
    points = []
    if path.suffix.lower() == ".json":
        data = json.loads(raw)
        rows = data if isinstance(data, list) else data.get("points", [])
        for row in rows:
            if "seconds" in row:
                t = float(row["seconds"])
            elif "pct_video" in row:
                t = float(row["pct_video"]) / 100.0 * total_s
            else:
                continue
            r = float(row.get("retention_pct", row.get("retention", 0)))
            points.append((t, r))
    else:
        reader = csv.DictReader(raw.splitlines())
        for row in reader:
            norm = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
            if "seconds" in norm or "time_sec" in norm or "time" in norm:
                t = float(norm.get("seconds") or norm.get("time_sec") or norm.get("time"))
            elif "pct_video" in norm or "video_pct" in norm or "progress" in norm:
                t = float(norm.get("pct_video") or norm.get("video_pct") or norm.get("progress")) / 100.0 * total_s
            else:
                continue
            r_str = (norm.get("retention_pct") or norm.get("retention") or norm.get("audience_retention") or "0").rstrip("%")
            points.append((t, float(r_str)))
    points.sort(key=lambda x: x[0])
    if len(points) < 3:
        raise ValueError(f"Retention file {path} needs at least 3 timestamped data points")
    return points


def ingest_retention(curve_path: Path, project: Path, *, platform: str = "bilibili",
                     save_library: bool = False, lib_root: Path | None = None, top_cliffs: int = 5) -> dict:
    project = project.resolve()
    edit = json.loads((project / "edit.json").read_text(encoding="utf-8"))
    tl = json.loads((project / "timeline.json").read_text(encoding="utf-8"))
    lib_uses_p = project / "library-uses.json"
    lib_uses = json.loads(lib_uses_p.read_text(encoding="utf-8")) if lib_uses_p.is_file() else []
    fps = tl["fps"]
    total_s = tl["frames"] / fps
    points = _parse_curve(curve_path, total_s)

    e_shots = {s["id"]: s for s in edit.get("shots", [])}
    t_shots = tl.get("shots", [])

    def shot_for_time(t_s: float) -> dict | None:
        f = round(t_s * fps)
        for ts in t_shots:
            if ts["start_frame"] <= f < ts["start_frame"] + ts["frames"]:
                return ts
        return t_shots[-1] if t_shots else None

    # Compute local drop rate (pct points lost per 5s equivalent)
    cliffs = []
    for (t0, r0), (t1, r1) in zip(points, points[1:]):
        dt = max(0.5, t1 - t0)
        drop = r0 - r1
        if drop <= 1.2:
            continue
        slope_per_10s = (drop / dt) * 10.0
        mid_t = (t0 + t1) * 0.5
        ts = shot_for_time(mid_t)
        sid = ts["id"] if ts else "unknown"
        shot = e_shots.get(sid, {})
        vkind = _shot_visual_kind(shot) if shot else "unknown"
        trans = _transition_name(shot) if shot else "cut"
        rem_eff = (shot.get("remotion_effect") or {}).get("effect") if isinstance(shot.get("remotion_effect"), dict) else None
        shot_dur = round(ts["frames"] / fps, 2) if ts else 0.0
        spoken = " ".join(l.get("text", "") for l in shot.get("lines", []))[:110]
        # Diagnose likely editorial root cause
        causes = []
        if t0 < 30.0:
            causes.append("前30秒黄金钩子流失")
        if vkind == "graphic_slide" and shot_dur >= 7.0:
            causes.append(f"长静图卡({shot_dur}s)缺乏实拍/动态切入")
        if trans == "cut" and shot_dur >= 10.0:
            causes.append("超长镜头缺视觉节拍打断")
        if not causes:
            causes.append("叙事转折/信息密度波动需复看该镜")

        cliffs.append(dict(
            start_s=round(t0, 1), end_s=round(t1, 1),
            retention_from=round(r0, 1), retention_to=round(r1, 1),
            drop_pct=round(drop, 2), drop_per_10s=round(slope_per_10s, 2),
            shot=sid, shot_seconds=shot_dur, visual_kind=vkind,
            remotion_effect=rem_eff, transition=trans, spoken_excerpt=spoken,
            likely_causes=causes,
        ))

    cliffs.sort(key=lambda c: (-c["drop_pct"], -c["drop_per_10s"]))
    top = cliffs[:top_cliffs]

    # Headline KPIs
    r_30s = min(points, key=lambda p: abs(p[0] - 30.0))[1]
    r_end = points[-1][1]
    avg_ret = round(float(sum(r for _, r in points) / len(points)), 1)

    # Synthesize actionable cross-project rule takeaways
    pattern_lessons = []
    if any(c["start_s"] < 30.0 and c["drop_pct"] >= 5.0 for c in top):
        pattern_lessons.append("前 30 秒出现 ≥5% 陡峭流失：下期须压缩开场首切至 3 秒内，并核对封面与首句兑现。")
    slide_drops = [c for c in top if c["visual_kind"] == "graphic_slide"]
    if slide_drops:
        pattern_lessons.append(
            f"前 {len(top)} 大掉点中有 {len(slide_drops)} 处落在无视频背景图卡（平均 {round(sum(c['shot_seconds'] for c in slide_drops)/len(slide_drops), 1)}s）：下期优先改用 broll-evidence-pop 或实拍铺底。"
        )
    if not pattern_lessons:
        pattern_lessons.append("整体留存曲线平稳，重点复看陡峭拐点处的口播信息密度与转场节奏。")

    report = dict(
        schema=1,
        project=project.name,
        platform=platform,
        duration_seconds=round(total_s, 1),
        metrics=dict(retention_at_30s=round(r_30s, 1), retention_at_end=round(r_end, 1), average_retention=avg_ret),
        top_drop_cliffs=top,
        pattern_lessons=pattern_lessons,
        library_assets_count=len(lib_uses) if isinstance(lib_uses, list) else 0,
    )

    qa_out = project / "qa" / "retention-report.json"
    qa_out.parent.mkdir(parents=True, exist_ok=True)
    qa_out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    saved_path = None
    if save_library:
        root = library.find_root(lib_root, project)
        dest_dir = root / "retention-lessons"
        dest_dir.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(f"{project.name}:{platform}:{curve_path.name}".encode()).hexdigest()[:12]
        saved_path = dest_dir / f"{project.name}-{platform}-{key}.json"
        saved_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report["saved_to_library"] = str(saved_path)

    return report


def list_lessons(lib_root: Path | None = None) -> dict:
    root = library.find_root(lib_root)
    folder = root / "retention-lessons"
    items = []
    if folder.is_dir():
        for p in sorted(folder.glob("*.json")):
            data = json.loads(p.read_text(encoding="utf-8"))
            items.append(dict(
                file=p.name, project=data.get("project"), platform=data.get("platform"),
                metrics=data.get("metrics"), pattern_lessons=data.get("pattern_lessons", []),
            ))
    return dict(library_root=str(root), count=len(items), lessons=items)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    ig = sub.add_parser("ingest", help="Ingest platform retention CSV/JSON and map drop-offs to shots")
    ig.add_argument("curve", type=Path)
    ig.add_argument("--project", type=Path, default=Path("."))
    ig.add_argument("--platform", default="bilibili", choices=["bilibili", "youtube", "douyin", "weixin", "other"])
    ig.add_argument("--save-library", action="store_true", help="Save retention lessons into personal media-library/retention-lessons/")
    ig.add_argument("--root", type=Path, help="Optional personal media library root")
    ls = sub.add_parser("lessons", help="List accumulated cross-project retention lessons from personal library")
    ls.add_argument("--root", type=Path)
    args = ap.parse_args()
    if args.cmd == "ingest":
        print(json.dumps(ingest_retention(
            args.curve, args.project, platform=args.platform,
            save_library=args.save_library, lib_root=args.root,
        ), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(list_lessons(args.root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
