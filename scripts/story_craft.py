#!/usr/bin/env python3
"""Narrative craft engine: Micro-Story Loop check, spoken-word cliché detector, mood director & packaging payoff audit.

Usage:
  story_craft.py audit videos/my-film
  story_craft.py annotate-moods videos/my-film/edit.json [--write]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

# Calibrated director moods supported natively by jianqiu_voice.py & prepare.py.
MOOD_PROFILES = {
    "neutral": {
        "speed_scale": 1.00,
        "pause_delta": 0.00,
        "eq": "",
        "description": "自然平稳的见秋叙述基准",
    },
    "hook": {
        "speed_scale": 1.04,
        "pause_delta": -0.02,
        "eq": "equalizer=f=3400:t=q:w=1.2:g=1.8,equalizer=f=180:t=q:w=1.0:g=0.8",
        "description": "开场高能直入，干脆利落，略增临场存在感",
    },
    "curious": {
        "speed_scale": 1.01,
        "pause_delta": 0.08,
        "eq": "equalizer=f=3800:t=q:w=1.3:g=1.3",
        "description": "设问与悬念起势，句尾多留 0.08s 引发思考",
    },
    "suspense": {
        "speed_scale": 0.91,
        "pause_delta": 0.18,
        "eq": "equalizer=f=160:t=q:w=1.0:g=1.6,equalizer=f=4600:t=q:w=1.2:g=-1.5",
        "description": "压低语速、增强近讲暖度与停顿，制造反转前张力",
    },
    "reveal": {
        "speed_scale": 0.94,
        "pause_delta": 0.14,
        "eq": "equalizer=f=3200:t=q:w=1.1:g=2.0,equalizer=f=220:t=q:w=1.1:g=1.0",
        "description": "核心结论/关键数字落地，略放慢并留出消化停顿",
    },
    "warm": {
        "speed_scale": 0.96,
        "pause_delta": 0.10,
        "eq": "equalizer=f=175:t=q:w=1.0:g=1.4,equalizer=f=3000:t=q:w=1.2:g=0.9",
        "description": "收尾回扣与观感沉淀，温和收束",
    },
}

WRITTEN_CLICHES = [
    ("首先", "改用动作或具体现象直入，不要像列提纲一样喊“首先”"),
    ("其次", "直接顺着上一句的因果往下接，删去公文序号“其次”"),
    ("综上所述", "不要念总结套话，直接回到开头悬念给一句话结论"),
    ("总而言之", "去掉书面过渡词，用一句具体画面或数字收尾"),
    ("值得注意的是", "直接抛出反常细节本身，例如“这里有个反直觉的细节”"),
    ("与此同时", "改为更口语的“同一时间”或直接切画面动作"),
    ("由此可见", "改为“所以你看”或直接说出推论"),
    ("不难发现", "改为“点开就能看到”或“真正奇怪的地方在于”"),
    ("众所周知", "不要预设观众知道，直接讲反差或删去"),
    ("毋庸置疑", "用具体证据替代空泛强调词"),
    ("某种意义上来说", "删掉虚词，把判断说实"),
    ("进行了深入的", "用具体动词替代“进行了…的”公文句式"),
]

THROAT_CLEARING = (
    "大家好", "欢迎来到", "今天我们来聊聊", "今天带大家", "本期视频", "哈喽大家好", "各位观众",
)

TENSION_MARKERS = (
    "？", "?", "但是", "然而", "问题在于", "为什么", "难道", "竟然", "居然", "没想到",
    "反直觉", "真正", "其实", "却", "直到", "奇怪", "代价", "陷阱", "漏洞", "卡在",
    "凭什么", "怎么做到", "秘密", "反转", "真相", "冲突", "吵翻",
)

CONCRETE_ANCHORS = re.compile(
    r"[0-9一二两三四五六七八九十百千万亿]+(?:倍|%|％|年|天|小时|分钟|秒|万|亿|行|元|美元|块|页|帧|毫米|公里|GB|TB|MB|Hz)"
    r"|比如|就像|想象一下|点开|翻开|屏幕上|代码里|表格里|账单|截图|原件|实验|实测|你看"
)

SAFE_EXIT_MARKERS = ("总结一下", "以上就是", "到这里就结束了", "综上所述", "总而言之")


def infer_mood(text: str, *, shot: dict | None = None, is_opening: bool = False, is_closing: bool = False) -> str:
    """Infer an expressive voice director mood from line punctuation, rhetoric and shot role."""
    s = (text or "").strip()
    shot = shot or {}
    if shot.get("hook") or is_opening:
        if any(k in s for k in ("？", "?", "为什么", "难道")):
            return "curious"
        return "hook"
    if is_closing:
        return "warm"
    if shot.get("rehook") or any(k in s for k in ("但是", "然而", "问题在于", "直到", "没想到", "却", "漏洞", "陷阱")):
        return "suspense"
    if "？" in s or "?" in s or any(k in s for k in ("为什么", "难道", "怎么做到", "凭什么")):
        return "curious"
    if shot.get("emphasis") or any(k in s for k in ("真相", "答案", "核心在于", "真正原因", "结论", "原来")):
        return "reveal"
    return "neutral"


def _content_tokens(text: str) -> set[str]:
    """Extract meaningful CJK 2-grams and ASCII/numeric tokens for cover-to-opening payoff checks."""
    tokens = set()
    for m in re.findall(r"[A-Za-z0-9]{2,}", text or ""):
        tokens.add(m.lower())
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", text or ""):
        if len(run) <= 4:
            tokens.add(run)
        for i in range(len(run) - 1):
            bg = run[i:i + 2]
            if bg not in ("我们", "这个", "一个", "可以", "就是", "不是", "什么", "如何", "怎么", "为什么"):
                tokens.add(bg)
    return tokens


def analyze_script(edit: dict, timeline: dict | None = None, *, cover_cfg: dict | None = None,
                   packaging_cfg: dict | None = None) -> dict:
    """Run Micro-Story Loop, spoken-word polish, abstract-drought and opening-payoff diagnostics."""
    shots = edit.get("shots", [])
    takes_by_key = {}
    if isinstance(timeline, dict):
        fps = timeline.get("fps", 30)
        total_s = timeline.get("frames", 0) / fps if fps else 0.0
        for vt in timeline.get("voice_takes", []):
            key = (vt.get("shot"), vt.get("spoken"))
            takes_by_key.setdefault(key, []).append(vt)
    else:
        total_s = 0.0

    # Flatten spoken timeline with exact or estimated timestamps
    line_rows = []
    cursor = 0.0
    for s_idx, shot in enumerate(shots):
        sid = shot.get("id", f"s{s_idx}")
        for l_idx, line in enumerate(shot.get("lines", [])):
            text = (line.get("text") or "").strip()
            spoken = (line.get("spoken") or text).strip()
            if not text:
                continue
            vt_list = takes_by_key.get((sid, spoken))
            if vt_list:
                vt = vt_list.pop(0)
                t0 = vt["start_sample"] / vt["sample_rate"]
                t1 = vt["end_sample"] / vt["sample_rate"]
            else:
                dur = max(1.0, len(re.sub(r"\s+", "", spoken)) / 4.2)
                t0, t1 = cursor, cursor + dur
            cursor = max(cursor, t1 + float(line.get("pause", 0.09)))
            line_rows.append(dict(
                shot=sid, shot_index=s_idx, line_index=l_idx, text=text, spoken=spoken,
                start_s=round(t0, 2), end_s=round(t1, 2), shot_obj=shot, line_obj=line,
            ))
    if not total_s:
        total_s = round(cursor, 2)

    cliches = []
    long_sentences = []
    throat_clears = []
    mood_plan = []
    explicit_mood_count = 0

    for idx, row in enumerate(line_rows):
        text = row["text"]
        shot = row["shot_obj"]
        line = row["line_obj"]
        is_opening = row["start_s"] < 6.0
        is_closing = total_s > 20.0 and row["end_s"] > total_s - 10.0

        # Written-prose cliché scan
        for phrase, hint in WRITTEN_CLICHES:
            if phrase in text:
                cliches.append(dict(shot=row["shot"], line_index=row["line_index"],
                                    phrase=phrase, text=text, suggestion=hint))
        if text.count("的") >= 3 and len(text) >= 24:
            cliches.append(dict(shot=row["shot"], line_index=row["line_index"],
                                phrase="多重“的”定语", text=text,
                                suggestion="多层书面定语口播易绕口，建议拆成两句短主谓结构"))
        # Run-on breath check (excluding punctuation)
        clauses = [c for c in re.split(r"[，。！？；,.!?;]", text) if c.strip()]
        if clauses and max(len(c) for c in clauses) > 34:
            long_sentences.append(dict(shot=row["shot"], line_index=row["line_index"],
                                       chars=max(len(c) for c in clauses), text=text))
        # Throat clearing in first 6 seconds
        if is_opening:
            for tc in THROAT_CLEARING:
                if tc in text:
                    throat_clears.append(dict(shot=row["shot"], phrase=tc, text=text))

        explicit_mood = line.get("mood") or shot.get("mood")
        if explicit_mood:
            explicit_mood_count += 1
        rec_mood = explicit_mood or infer_mood(text, shot=shot, is_opening=is_opening, is_closing=is_closing)
        mood_plan.append(dict(
            shot=row["shot"], line_index=row["line_index"], start_s=row["start_s"],
            mood=rec_mood, explicit=bool(explicit_mood), text=text[:48],
        ))

    # Micro-Story Loop & Curiosity-Gap windows (every 40s across body)
    window_size = 40.0
    curiosity_windows = []
    drought_windows = []
    safe_exits = []
    if total_s >= 35.0:
        w_start = 0.0
        while w_start < total_s - 12.0:
            w_end = min(total_s, w_start + window_size)
            in_win = [r for r in line_rows if r["end_s"] > w_start and r["start_s"] < w_end]
            joined = " ".join(r["text"] for r in in_win)
            has_tension = any(m in joined for m in TENSION_MARKERS) or any(
                r["shot_obj"].get("rehook") or r["shot_obj"].get("curiosity_hook") for r in in_win
            )
            has_concrete = bool(CONCRETE_ANCHORS.search(joined))
            is_final_win = w_end >= total_s - 10.0
            win_rec = dict(
                window=f"{int(w_start)}s–{int(w_end)}s",
                has_curiosity_loop=has_tension,
                has_concrete_anchor=has_concrete,
                shots=sorted({r["shot"] for r in in_win}, key=lambda sid: next(i for i, x in enumerate(in_win) if x["shot"] == sid)),
            )
            curiosity_windows.append(win_rec)
            if not is_final_win and not has_tension:
                drought_windows.append(win_rec)
            if not is_final_win and any(m in joined for m in SAFE_EXIT_MARKERS):
                safe_exits.append(dict(window=win_rec["window"], shots=win_rec["shots"]))
            w_start += window_size

    # Abstract-only continuous stretch check (>28s with zero concrete numbers/metaphors/evidence)
    abstract_stretches = []
    abs_start = None
    abs_shots = []
    for r in line_rows:
        has_c = bool(CONCRETE_ANCHORS.search(r["text"])) or any(
            k in r["shot_obj"] for k in ("evidence_window", "value_comparison", "geo_map", "math_motion")
        )
        if not has_c:
            if abs_start is None:
                abs_start = r["start_s"]
                abs_shots = [r["shot"]]
            elif r["shot"] not in abs_shots:
                abs_shots.append(r["shot"])
        else:
            if abs_start is not None and r["start_s"] - abs_start >= 28.0:
                abstract_stretches.append(dict(
                    start_s=round(abs_start, 1), end_s=round(r["start_s"], 1),
                    duration_s=round(r["start_s"] - abs_start, 1), shots=abs_shots,
                ))
            abs_start, abs_shots = None, []
    if abs_start is not None and total_s - abs_start >= 28.0:
        abstract_stretches.append(dict(
            start_s=round(abs_start, 1), end_s=round(total_s, 1),
            duration_s=round(total_s - abs_start, 1), shots=abs_shots,
        ))

    # Packaging / Cover <-> First 5s Payoff Check
    payoff = None
    first_5s_text = " ".join(r["text"] for r in line_rows if r["start_s"] < 5.5)
    cover_texts = []
    if isinstance(cover_cfg, dict):
        cover_texts.extend(cover_cfg.get("lines") or [])
        if cover_cfg.get("kicker"):
            cover_texts.append(cover_cfg["kicker"])
        if cover_cfg.get("badge"):
            cover_texts.append(cover_cfg["badge"])
    if isinstance(packaging_cfg, dict):
        if packaging_cfg.get("headline"):
            cover_texts.append(packaging_cfg["headline"])
        if packaging_cfg.get("hook_promise"):
            cover_texts.append(packaging_cfg["hook_promise"])
    if not cover_texts and edit.get("title"):
        cover_texts.append(edit["title"])
    if cover_texts and first_5s_text:
        c_toks = _content_tokens(" ".join(str(x) for x in cover_texts))
        o_toks = _content_tokens(first_5s_text)
        shared = sorted(c_toks & o_toks)
        overlap_ratio = round(len(shared) / max(1, min(len(c_toks), 6)), 3)
        payoff = dict(
            cover_promise=" / ".join(str(x) for x in cover_texts[:3]),
            first_5s_spoken=first_5s_text[:120],
            shared_concepts=shared[:8],
            payoff_ratio=overlap_ratio,
            matched=overlap_ratio >= 0.25 and not throat_clears,
        )

    # Compute objective narrative-craft gates & actionable Chinese suggestions (no synthetic point score).
    suggestions = []
    if throat_clears:
        suggestions.append(
            f"开场前 6 秒出现寒暄套话（{'、'.join(t['phrase'] for t in throat_clears)}），顶级博主一律砍掉问好，0 秒直入结果或核心冲突。"
        )
    if cliches:
        samples = "、".join(f"{c['shot']}「{c['phrase']}」" for c in cliches[:4])
        suggestions.append(f"口播稿发现 {len(cliches)} 处书面报告腔（{samples}），建议改写为口语短句、反问或第一人称动作句。")
    if long_sentences:
        suggestions.append(f"有 {len(long_sentences)} 处单气口超过 34 字的过长从句，建议拆短让配音呼吸与字幕切分更干脆。")
    if drought_windows:
        wins = "、".join(w["window"] for w in drought_windows[:3])
        suggestions.append(f"时段 {wins} 缺少微型悬念闭环（无反问/转折/冲突/新钩子），观众容易在此处滑走；建议在揭晓前先抛出下一段好奇心缺口。")
    if safe_exits:
        suggestions.append(f"中段出现提前收束套话（{', '.join(s['window'] for s in safe_exits)}），制造了“安全退出点”，建议改成悬念过渡句。")
    if abstract_stretches:
        st = "、".join(f"{a['start_s']}–{a['end_s']}s" for a in abstract_stretches[:2])
        suggestions.append(f"区间 {st} 连续超过 28 秒纯抽象阐述（缺具体数字、生活类比或可见物证），建议补一个直观类比或真证据镜头。")
    if payoff and not payoff["matched"]:
        suggestions.append(
            f"封面/标题承诺「{payoff['cover_promise']}」与前 5 秒台词重合偏低（{payoff['payoff_ratio']:.0%}），建议前 5 秒第一句话直接兑现封面悬念。"
        )
    if line_rows and explicit_mood_count == 0:
        suggestions.append(
            "尚未给口播句配置情绪导演标签（line.mood），可运行 `story_craft.py annotate-moods edit.json --write` 自动注入 hook/curious/suspense/reveal/warm 情绪起伏。"
        )

    checks = {
        "no_opening_throat_clear": len(throat_clears) == 0,
        "no_written_cliches": len(cliches) == 0,
        "no_long_runon_clauses": len(long_sentences) == 0,
        "micro_story_loops_active": len(drought_windows) == 0,
        "no_mid_safe_exits": len(safe_exits) == 0,
        "no_long_abstract_stretch": len(abstract_stretches) == 0,
        "cover_5s_payoff": True if not payoff else bool(payoff["matched"]),
    }
    issue_count = sum(1 for ok in checks.values() if not ok)

    return dict(
        schema=1,
        checks=checks,
        issue_count=issue_count,
        passed=issue_count == 0,
        line_count=len(line_rows),
        explicit_mood_lines=explicit_mood_count,
        throat_clears=throat_clears,
        written_cliches=cliches,
        long_sentences=long_sentences,
        curiosity_windows=curiosity_windows,
        curiosity_droughts=drought_windows,
        safe_exits=safe_exits,
        abstract_stretches=abstract_stretches,
        cover_opening_payoff=payoff,
        mood_plan=mood_plan,
        suggestions=suggestions,
    )


def annotate_moods(edit_path: Path, *, write: bool = False) -> dict:
    """Assign expressive line.mood director tags to shots/lines without explicit moods."""
    edit = json.loads(edit_path.read_text(encoding="utf-8"))
    report = analyze_script(edit)
    by_pos = {(m["shot"], m["line_index"]): m["mood"] for m in report["mood_plan"]}
    applied = []
    for shot in edit.get("shots", []):
        sid = shot["id"]
        for idx, line in enumerate(shot.get("lines", [])):
            if line.get("mood"):
                continue
            mood = by_pos.get((sid, idx), "neutral")
            if mood != "neutral":
                line["mood"] = mood
                applied.append(dict(shot=sid, line_index=idx, mood=mood, text=line.get("text", "")[:42]))
    if write:
        edit_path.write_text(json.dumps(edit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return dict(edit=str(edit_path), written=write, annotated_lines=len(applied), changes=applied)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    au = sub.add_parser("audit", help="Audit script for Micro-Story Loops, spoken clarity and cover payoff")
    au.add_argument("project", type=Path, default=Path("."), nargs="?")
    am = sub.add_parser("annotate-moods", help="Suggest or write expressive line.mood tags into edit.json")
    am.add_argument("edit", type=Path)
    am.add_argument("--write", action="store_true")
    args = ap.parse_args()

    if args.cmd == "audit":
        proj = args.project.resolve()
        edit = json.loads((proj / "edit.json").read_text(encoding="utf-8"))
        tl = json.loads((proj / "timeline.json").read_text(encoding="utf-8")) if (proj / "timeline.json").is_file() else None
        cov = json.loads((proj / "cover.json").read_text(encoding="utf-8")) if (proj / "cover.json").is_file() else None
        pkg = json.loads((proj / "packaging.json").read_text(encoding="utf-8")) if (proj / "packaging.json").is_file() else None
        print(json.dumps(analyze_script(edit, tl, cover_cfg=cov, packaging_cfg=pkg), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(annotate_moods(args.edit, write=args.write), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
