"""Visual domain facade: style bible, AI B-roll, 2.5D parallax, geo_map, math_motion, focus & grading."""
from studio import DOMAINS, load

__all__ = ["style_bible", "broll_router", "parallax_25d", "geo_map", "math_motion",
           "auto_focus", "color_finish", "color_match", "shot_camera", "remotion_library"]


def __getattr__(name: str):
    if name in __all__:
        return load(name)
    raise AttributeError(name)
