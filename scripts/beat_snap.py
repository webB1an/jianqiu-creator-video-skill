#!/usr/bin/env python3
"""Snap shot cut boundaries onto musical beats by gently nudging preceding shot tails (zero speech impact).

Reads edit.json, timeline.json and qa/beat-grid.json, checks each eligible cut boundary against musical
beats (prioritizing bar downbeats), and shifts shot.tail within +/- max_shift_frames while keeping every
spoken word untouched and enforcing min_tail >= 0.10s. Re-run prepare.py after --write to lock the new clock.

Usage:
  beat_snap.py --project videos/my-film [--beats qa/beat-grid.json] [--max-shift 5] [--write]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def plan_beat_snaps(edit: dict, timeline: dict, beat_grid: dict, *,
                    max_shift_frames: int = 5, min_tail_s: float = 0.10,
                    downbeat_bonus_frames: int = 2) -> dict:
    """Compute safe, speech-preserving tail nudges that snap shot cuts onto musical beats."""
    fps = int(timeline["fps"])
    beats = beat_grid.get("beats") or []
    if not beats:
        return {"snapped_count": 0, "changes": [], "note": "beat-grid.json has no beats"}

    e_shots = {s["id"]: s for s in edit.get("shots", [])}
    t_shots = timeline.get("shots") or []

    beat_frames = [(int(b["frame"]), bool(b.get("is_downbeat"))) for b in beats]
    cumulative_shift = 0
    changes = []
    before_errs = []
    after_errs = []

    for idx in range(len(t_shots) - 1):
        cur_t = t_shots[idx]
        nxt_t = t_shots[idx + 1]
        cur_e = e_shots.get(cur_t["id"], {})
        if not cur_e or "roughcut_source_sha256" in cur_e or cur_e.get("lock_timing"):
            continue
        nominal_cut = nxt_t["start_frame"] + cumulative_shift
        # Find nearest beat (downbeats get slight preference within max_shift_frames).
        best = None
        best_rank = 1e9
        nearest_raw = min(abs(bf - nominal_cut) for bf, _ in beat_frames)
        before_errs.append(nearest_raw)
        for bf, is_down in beat_frames:
            delta = bf - nominal_cut
            if abs(delta) <= max_shift_frames:
                rank = abs(delta) - (downbeat_bonus_frames if is_down else 0)
                if rank < best_rank:
                    best_rank = rank
                    best = (bf, delta, is_down)
        if best is None or best[1] == 0:
            after_errs.append(nearest_raw)
            continue
        target_beat_frame, delta_frames, is_down = best
        old_tail = float(cur_e.get("tail", 0.18))
        new_tail = round(old_tail + delta_frames / fps, 4)
        if new_tail < min_tail_s:
            after_errs.append(nearest_raw)
            continue
        cumulative_shift += delta_frames
        after_errs.append(0)
        changes.append(dict(
            shot=cur_t["id"],
            next_shot=nxt_t["id"],
            cut_before_frame=nominal_cut,
            cut_after_frame=target_beat_frame,
            delta_frames=delta_frames,
            is_downbeat=is_down,
            old_tail=old_tail,
            new_tail=new_tail,
        ))

    return dict(
        fps=fps,
        max_shift_frames=max_shift_frames,
        boundaries_checked=len(before_errs),
        snapped_count=len(changes),
        mean_error_before_frames=round(sum(before_errs) / len(before_errs), 2) if before_errs else 0.0,
        mean_error_after_frames=round(sum(after_errs) / len(after_errs), 2) if after_errs else 0.0,
        total_duration_delta_frames=cumulative_shift,
        changes=changes,
    )


def snap_project(project: Path, *, beats_path: Path | None = None,
                 max_shift_frames: int = 5, min_tail_s: float = 0.10, write: bool = False) -> dict:
    project = project.resolve()
    edit_path = project / "edit.json"
    tl_path = project / "timeline.json"
    b_path = beats_path if (beats_path and beats_path.is_absolute()) else (project / (beats_path or Path("qa/beat-grid.json")))
    edit = json.loads(edit_path.read_text(encoding="utf-8"))
    tl = json.loads(tl_path.read_text(encoding="utf-8"))
    bg = json.loads(b_path.read_text(encoding="utf-8"))
    plan = plan_beat_snaps(edit, tl, bg, max_shift_frames=max_shift_frames, min_tail_s=min_tail_s)
    if write and plan["changes"]:
        by_id = {s["id"]: s for s in edit["shots"]}
        for ch in plan["changes"]:
            by_id[ch["shot"]]["tail"] = ch["new_tail"]
        edit_path.write_text(json.dumps(edit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        plan["written"] = True
        plan["next_step"] = "Run prepare.py to lock the snapped shot tails into timeline.json"
    else:
        plan["written"] = False
    return plan


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", type=Path, default=Path("."))
    ap.add_argument("--beats", type=Path, default=Path("qa/beat-grid.json"))
    ap.add_argument("--max-shift", type=int, default=5, help="Max +/- frames allowed to nudge shot.tail (default 5)")
    ap.add_argument("--min-tail", type=float, default=0.10, help="Minimum protected shot.tail in seconds (default 0.10)")
    ap.add_argument("--write", action="store_true", help="Write snapped shot tails into edit.json")
    args = ap.parse_args()
    print(json.dumps(snap_project(
        args.project, beats_path=args.beats, max_shift_frames=args.max_shift,
        min_tail_s=args.min_tail, write=args.write,
    ), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
