#!/usr/bin/env python3
"""Unified pre-export preflight and post-export director quality pipeline for creator-video.

Consolidates story_craft, pronunciation_review, auto_focus, color_match, packaging, sfx_scaffold,
narrative_audit, review and multimodal_critique into two low-friction commands with single JSON reports.

Usage:
  studio_pipeline.py preflight --project videos/my-film [--write] [--hero s01]
  studio_pipeline.py postcheck --project videos/my-film --media out/review/.../candidate.mp4 [--run-gemini]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

SCRIPTS = Path(__file__).resolve().parent


def _pick_hero_shot(project: Path, edit: dict) -> str | None:
    """Pick the first live-action/non-graphic shot with an existing file as default color-match hero."""
    from color_finish import GENERATED_SHOT_KEYS
    for shot in edit.get("shots", []):
        if any(k in shot for k in GENERATED_SHOT_KEYS):
            continue
        vis = shot.get("visual")
        if vis and (project / vis).is_file():
            return shot["id"]
    return None


def preflight(project: Path, *, write: bool = False, hero: str | None = None,
              apply_sfx: bool = False, apply_cover: bool = False) -> dict:
    """Run all pre-export narrative, pronunciation, visual-focus, color, packaging & pacing checks in one pass."""
    project = project.resolve()
    edit_path = project / "edit.json"
    if not edit_path.is_file():
        raise FileNotFoundError(f"edit.json not found in {project}")
    edit = json.loads(edit_path.read_text(encoding="utf-8"))
    tl_path = project / "timeline.json"
    timeline = json.loads(tl_path.read_text(encoding="utf-8")) if tl_path.is_file() else None
    cov_path = project / "cover.json"
    cov = json.loads(cov_path.read_text(encoding="utf-8")) if cov_path.is_file() else None
    pkg_path = project / "packaging.json"
    pkg = json.loads(pkg_path.read_text(encoding="utf-8")) if pkg_path.is_file() else None
    beats_path = project / "qa/beat-grid.json"
    beat_grid = json.loads(beats_path.read_text(encoding="utf-8")) if beats_path.is_file() else None

    report: dict = {"schema": 1, "project": str(project), "write_mode": write, "applied_changes": []}

    # 1. Story craft: Micro-Story Loops, spoken-word polish, mood annotation
    import story_craft
    if write:
        ann = story_craft.annotate_moods(edit_path, write=True)
        if ann.get("annotated_lines"):
            edit = json.loads(edit_path.read_text(encoding="utf-8"))
            report["applied_changes"].append(f"story_craft: annotated {ann['annotated_lines']} line moods")
    report["story_craft"] = story_craft.analyze_script(edit, timeline, cover_cfg=cov, packaging_cfg=pkg)

    # 2. ZipVoice polyphone pronunciation pre-check
    try:
        import pronunciation_review
        pr_res = pronunciation_review.audit_edit(edit, timeline)
        report["pronunciation"] = pr_res
    except Exception as exc:
        report["pronunciation"] = {"error": str(exc)}

    # 3. Auto-focus face & saliency detection (optionally writes caption_avoid boxes)
    try:
        import auto_focus
        af_res = auto_focus.analyze_project(project, write=write)
        report["auto_focus"] = {
            "analyzed_shots": af_res["analyzed_shots"],
            "written_caption_avoid": af_res["written_caption_avoid"],
            "backend": af_res.get("backend"),
            "shots": af_res["shots"],
        }
        if write and af_res["written_caption_avoid"]:
            report["applied_changes"].append(f"auto_focus: wrote caption_avoid on {af_res['written_caption_avoid']} shots")
            edit = json.loads(edit_path.read_text(encoding="utf-8"))
    except Exception as exc:
        report["auto_focus"] = {"error": str(exc)}

    # 4. Cross-shot color match check (skin-tone & luminance-bucket safe)
    chosen_hero = hero or _pick_hero_shot(project, edit)
    if chosen_hero:
        try:
            import color_match
            cm_res = color_match.match_project(project, chosen_hero, write=write)
            report["color_match"] = {
                "hero": chosen_hero,
                "matched_shots": cm_res["matched_shots"],
                "written": cm_res["written"],
                "shots": cm_res["shots"],
            }
            if write and cm_res["matched_shots"]:
                report["applied_changes"].append(f"color_match: aligned {cm_res['matched_shots']} shots to hero {chosen_hero}")
                edit = json.loads(edit_path.read_text(encoding="utf-8"))
        except Exception as exc:
            report["color_match"] = {"error": str(exc)}
    else:
        report["color_match"] = {"skipped": "no live-action shot available for hero matching"}

    # 5. Packaging 160px mobile legibility & Cover <-> 0-5s payoff audit
    try:
        import packaging
        if apply_cover:
            report["packaging"] = packaging.draft_variants(project, project / "qa/packaging", apply=True)
            report["applied_changes"].append(f"packaging: wrote cover.json ({report['packaging']['selected_variant']})")
        else:
            report["packaging"] = packaging.audit_packaging(project)
    except Exception as exc:
        report["packaging"] = {"error": str(exc)}

    # 6. Timeline-dependent checks (narrative_audit + optional sfx_scaffold --apply-edit)
    matching_shots = timeline is not None and [s['id'] for s in timeline['shots']] == [s['id'] for s in edit['shots']]
    if matching_shots:
        import narrative_audit
        report["narrative_audit"] = narrative_audit.audit(
            timeline, edit, beat_grid=beat_grid, cover_cfg=cov, packaging_cfg=pkg,
        )
        if apply_sfx:
            try:
                import sfx_scaffold
                evs, sils, applied_sils = sfx_scaffold.scaffold(project, write=True, apply_edit=True)
                report["sfx_scaffold"] = {
                    "event_count": len(evs),
                    "recommended_micro_silences": len(sils),
                    "applied_micro_silences": applied_sils,
                }
                report["applied_changes"].append(
                    f"sfx_scaffold: wrote {len(evs)} events to sfx.json and merged {applied_sils} micro_silence cues"
                )
            except Exception as exc:
                report["sfx_scaffold"] = {"error": str(exc)}
    else:
        report["narrative_audit"] = {"skipped": "Run prepare.py to bind the current shots to timeline.json."}

    # Consolidate top-level actionable checklist
    actions = []
    for s in (report.get("narrative_audit") or {}).get("suggestions", []):
        actions.append({"stage": "narrative_audit", "detail": s})
    for c in (report.get("story_craft") or {}).get("written_cliches", [])[:6]:
        actions.append({"stage": "story_craft", "shot": c["shot"], "detail": f"书面腔「{c['phrase']}」：{c['suggestion']}"})
    for tc in (report.get("story_craft") or {}).get("throat_clears", []):
        actions.append({"stage": "story_craft", "shot": tc["shot"], "detail": f"开场寒暄「{tc['phrase']}」，建议删除直入主题"})
    if isinstance(report.get("pronunciation"), dict):
        for row in report["pronunciation"].get("unresolved_lines", []):
            for risk in row.get("risks", []):
                actions.append({"stage": "pronunciation", "shot": row.get("shot"), "detail": f"多音字风险「{risk.get('term')}」：{risk.get('detail')}"})
        for row in report["pronunciation"].get("tech_term_hints", []):
            for term in row["terms"]:
                actions.append({"stage": "pronunciation", "shot": row["shot"], "line_id": row["line_id"],
                                "detail": f"科技词「{term['term']}」建议读法 {term['recommended_read']}。{term['hint']}"})
    leg = (report.get("packaging") or {}).get("legibility_160px")
    if isinstance(leg, dict) and not leg.get("passed", True):
        for note in leg.get("notes", []):
            actions.append({"stage": "packaging", "detail": note})
    from production_run import digest
    report["visual_diversity"] = report["narrative_audit"].get("visual_diversity")
    report["timeline_matches_edit"] = bool(timeline and timeline.get("edit_sha256") == digest(edit_path))
    if timeline and not report["timeline_matches_edit"]:
        actions.append({"stage": "prepare", "detail": "工程改动尚未绑定到时间线，重跑 prepare.py 后复核各镜实际时长。"})
    report["action_items"] = actions
    report["action_count"] = len(actions)

    out_path = project / "qa/preflight-report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report["report_file"] = str(out_path)
    return report


def postcheck(project: Path, media: Path, *, run_gemini: bool = False,
              critique_backend: str = "auto", deepseek_api_key: str | None = None,
              deepseek_base_url: str | None = None, deepseek_model: str | None = None,
              deepseek_vision: bool = False,
              local_vlm_url: str | None = None, local_vlm_model: str | None = None,
              seg_seconds: int = 20, asr: Path | None = None) -> dict:
    """Run review.py + multimodal_critique.py and merge into one unified qa/director-report.json."""
    project = project.resolve()
    media = media.resolve()
    cmd = [sys.executable, str(SCRIPTS / "review.py"), "--media", str(media), "--critique",
           "--critique-seg-seconds", str(seg_seconds), "--critique-backend", critique_backend]
    if run_gemini:
        cmd.append("--run-gemini")
    if deepseek_api_key:
        cmd.extend(["--deepseek-api-key", deepseek_api_key])
    if deepseek_base_url:
        cmd.extend(["--deepseek-base-url", deepseek_base_url])
    if deepseek_model:
        cmd.extend(["--deepseek-model", deepseek_model])
    if deepseek_vision:
        cmd.append("--deepseek-vision")
    if local_vlm_url:
        cmd.extend(["--local-vlm-url", local_vlm_url])
    if local_vlm_model:
        cmd.extend(["--local-vlm-model", local_vlm_model])
    if asr:
        cmd.extend(["--asr", str(asr)])
    qa_dir = media.parent / "qa" if (media.parent / "bundle.json").is_file() else project / "qa"
    cmd.extend(["--report-dir", str(qa_dir)])
    subprocess.run(cmd, cwd=project, check=True)

    check_path = qa_dir / "content-check.json"
    check = json.loads(check_path.read_text(encoding="utf-8"))
    crit_path = qa_dir / "critique-packet/critique-rubric.json"
    crit = json.loads(crit_path.read_text(encoding="utf-8"))
    ed_diag = (check.get("pace") or {}).get("editorial_diagnostics") or {}

    diversity = ed_diag.get("visual_diversity")
    if diversity is not None:
        rhythm = check["pace"]["picture_rhythm"]
        fps = rhythm["fps"]
        holds = [dict(shot=row["shot"], **span, seconds=round((span["end_frame"] - span["start_frame"]) / fps, 3))
                 for row in rhythm["shots"] for span in row["quiet_spans"]
                 if span["end_frame"] - span["start_frame"] > 8 * fps]
        diversity = dict(diversity, measured_quiet_spans_over_8s=holds)
        if holds:
            diversity["status"] = "needs_review"

    director = {
        "schema": 2,
        "project": str(project),
        "media": str(media),
        "content_check_file": str(check_path),
        "critique_rubric_file": str(crit_path) if crit_path.is_file() else None,
        "pace": {
            "han_per_voiced_minute": (check.get("pace") or {}).get("han_per_voiced_minute"),
            "mean_shot_seconds": (check.get("pace") or {}).get("mean_shot_seconds"),
            "picture_rhythm": (check.get("pace") or {}).get("picture_rhythm"),
            "visual_diversity": diversity,
            "editorial_suggestions": ed_diag.get("suggestions", []),
        },
        "caption_layout": check.get("caption_layout"),
        "black_or_silent_intervals": check.get("black_or_silent_intervals", []),
        "multimodal_summary": crit.get("summary"),
        "ai_critique": crit.get("ai_critique"),
        "requested_backend": crit["requested_backend"],
        "effective_backend": crit["effective_backend"],
        "backend_attempts": crit["backend_attempts"],
        "hotspots": crit.get("auto_hotspots", []),
        "pronunciation_review": check.get("pronunciation_review", []),
    }
    out_path = qa_dir / "director-report.json"
    out_path.write_text(json.dumps(director, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    director["report_file"] = str(out_path)
    return director


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("catalog", help="Show the 5-domain modular studio architecture map (voice/audio/visual/render/qa)")

    pf = sub.add_parser("preflight", help="Run unified pre-export quality & craft preflight")
    pf.add_argument("--project", type=Path, default=Path("."))
    pf.add_argument("--hero", help="Optional hero shot id or reference media path for color matching")
    pf.add_argument("--write", action="store_true", help="Apply safe auto-annotations (moods, caption_avoid, color_match)")
    pf.add_argument("--apply-sfx", action="store_true", help="Also run sfx_scaffold --apply-edit when timeline.json exists")
    pf.add_argument("--apply-cover", action="store_true", help="Also draft A/B/C covers and write winner to cover.json")

    pc = sub.add_parser("postcheck", help="Run unified post-export review + multimodal viewer critique")
    pc.add_argument("--project", type=Path, default=Path("."))
    pc.add_argument("--media", type=Path, required=True)
    pc.add_argument("--seg-seconds", type=int, default=20)
    pc.add_argument("--asr", type=Path)
    pc.add_argument("--backend", choices=["auto", "deepseek", "local-vlm", "gemini", "offline"], default="auto")
    pc.add_argument("--deepseek-api-key")
    pc.add_argument("--deepseek-base-url")
    pc.add_argument("--deepseek-model", help="DeepSeek model name (default deepseek-flash)")
    pc.add_argument("--deepseek-vision", action="store_true",
                    help="Send storyboards to a DeepSeek-compatible vision gateway")
    pc.add_argument("--local-vlm-url")
    pc.add_argument("--local-vlm-model")
    pc.add_argument("--run-gemini", action="store_true")

    for parser in (pc, pf):
        parser.add_argument("--require-visual-clear", action="store_true",
                            help="Write the report and exit 2 when visual diversity needs review or cannot be measured")
    args = ap.parse_args()
    if args.cmd == "catalog":
        import studio
        res = studio.catalog()
    elif args.cmd == "preflight":
        res = preflight(
            args.project, write=args.write, hero=args.hero,
            apply_sfx=args.apply_sfx, apply_cover=args.apply_cover,
        )
    else:
        res = postcheck(
            args.project, args.media, run_gemini=args.run_gemini,
            critique_backend=args.backend, deepseek_api_key=args.deepseek_api_key,
            deepseek_base_url=args.deepseek_base_url, deepseek_model=args.deepseek_model,
            deepseek_vision=args.deepseek_vision,
            local_vlm_url=args.local_vlm_url, local_vlm_model=args.local_vlm_model,
            seg_seconds=args.seg_seconds, asr=args.asr,
        )
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if getattr(args, "require_visual_clear", False):
        diversity = res.get("visual_diversity") if args.cmd == "preflight" else res["pace"]["visual_diversity"]
        stale = args.cmd == "preflight" and not res.get("timeline_matches_edit")
        if stale or not diversity or diversity.get("status") != "clear":
            raise SystemExit(2)


if __name__ == "__main__":
    main()
