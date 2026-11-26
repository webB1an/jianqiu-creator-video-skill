"""Five-domain modular facade over creator-video scripts (100% backward compatible with flat CLI paths)."""
from __future__ import annotations

import importlib
from pathlib import Path
import sys

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

DOMAINS = {
    "voice": {
        "entrypoints": ['voice_mood_pack.py status', 'pronunciation_review.py --help'],
        "purpose": "多情绪配音、韵律标注、多音字与科技词发音审校、逐词时间轴",
        "modules": ["jianqiu_voice", "voice_mood_pack", "narration_pace", "pronunciation_review", "speech_clock", "voice_endings"],
    },
    "audio": {
        "entrypoints": ['sfx_scaffold.py --help', 'music_stems.py init-plan --help', 'music_stems.py mix --help'],
        "purpose": "CC0 多轨编曲与过门/终止式、真实拟音库、去齿音广播人声润色与混音",
        "modules": ["music_stems", "music_direction", "cue_score", "sfx_scaffold", "design_sound", "soundmix"],
    },
    "visual": {
        "entrypoints": ['broll_router.py generate --help', 'parallax_25d.py render --help'],
        "purpose": "影调圣经、AI B-Roll 路由与接力、2.5D 视差、高精度地图、数学曲线、智能对焦与肤色保护调色",
        "modules": ["style_bible", "broll_router", "parallax_25d", "geo_map", "math_motion", "auto_focus", "color_finish", "color_match", "shot_camera", "remotion_library"],
    },
    "render": {
        "entrypoints": ['prepare.py --help', 'export.py --help', 'portrait_cut.py --help'],
        "purpose": "J/L-Cut 时间线编排、全片母带/540p快审代理/定长镜头局部修补、鼓点吸附、动态跟焦竖版与三封面",
        "modules": ["prepare", "export", "beat_snap", "portrait_cut", "covers", "transitions", "targeted_edit"],
    },
    "qa": {
        "entrypoints": ['studio_pipeline.py preflight --help', 'studio_pipeline.py postcheck --help', 'multimodal_critique.py patch --help'],
        "purpose": "统一前/后检管线、视觉多样性导演闸门、素材体检、多模态审片（DeepSeek/本地离线VLM/Gemini）与自动修补",
        "modules": ["studio_pipeline", "review", "narrative_audit", "multimodal_critique", "media", "packaging", "story_craft"],
    },
}


def load(module_name: str):
    """Lazily import any underlying skill script by name."""
    return importlib.import_module(module_name)


def catalog() -> dict:
    """Return the complete 5-domain architecture map with live module availability."""
    out = {}
    for domain, info in DOMAINS.items():
        mods = []
        for name in info["modules"]:
            p = SCRIPTS_DIR / f"{name}.py"
            mods.append({"name": name, "script": f"scripts/{name}.py", "present": p.is_file()})
        out[domain] = {"purpose": info["purpose"], "modules": mods, "entrypoints": info["entrypoints"]}
    return {"schema": 1, "scripts_dir": str(SCRIPTS_DIR), "domains": out}
