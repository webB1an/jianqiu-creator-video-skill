#!/usr/bin/env python3
"""Stage a native Remotion composition or HyperFrames composition in an editable local directory."""
from argparse import ArgumentParser
from html import escape
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from fractions import Fraction
from PIL import Image, ImageOps
from evidence_cut import STILLS, label_image, validate

ASSETS = Path(__file__).resolve().parents[1] / "assets" / "motion"


def stage_media(source, target):
    if target.exists() and source.samefile(target):
        return
    with tempfile.TemporaryDirectory(dir=target.parent) as temp:
        staged = Path(temp) / target.name
        try:
            os.link(source, staged)
        except OSError:
            shutil.copy2(source, staged)
        os.replace(staged, target)


def transition_spec(spec, location):
    for key in ("width", "height", "fps"):
        if type(spec.get(key)) is not int or spec[key] <= 0:
            raise ValueError(f"{key} must be a positive integer")
    if spec["width"] % 2 or spec["height"] % 2:
        raise ValueError("Use even dimensions for H.264")
    if len(spec.get("sources", [])) != 2:
        raise ValueError("Provide the outgoing and incoming sources in that order")
    transition = spec["transition"]
    kind = transition.get("type")
    if kind not in ("slide", "wipe", "fade", "iris"):
        raise ValueError("Choose slide, wipe, fade or iris")
    if "direction" in transition:
        directions = {"from-left", "from-right", "from-top", "from-bottom"}
        if kind == "wipe":
            directions.update(("from-top-left", "from-top-right", "from-bottom-left", "from-bottom-right"))
        if kind not in ("slide", "wipe") or transition["direction"] not in directions:
            raise ValueError(f"Unsupported direction for {kind}")
    overlap = transition.get("frames")
    if type(overlap) is not int or overlap < 1:
        raise ValueError("transition.frames must be a positive integer")
    for source in spec["sources"]:
        if "legend" in source:
            raise ValueError("Source legend belongs to the EvidenceCut composition")
        frames = source.get("frames")
        if type(frames) is not int or frames < overlap:
            raise ValueError("Each source must supply at least the transition duration in frames")
        path = (location / source["file"]).resolve(strict=True)
        if not path.is_file():
            raise ValueError(f"Source must be a local file: {path}")
        source["file"] = path
        anchor = source.setdefault("anchor", [0.5, 0.5])
        if len(anchor) != 2 or any(type(v) not in (int, float) or not 0 <= v <= 1 for v in anchor):
            raise ValueError("anchor requires horizontal and vertical fractions in [0,1]")
        seconds = source.get("in", 0)
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
            raise ValueError("Source in-point must be nonnegative seconds")
        fps = spec["fps"]
        source["trimFrames"] = math.floor(Fraction(str(seconds)) * fps + Fraction(1, 2))
        if path.suffix.lower() not in STILLS:
            probe = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "V:0",
                "-show_entries", "stream=duration_ts,time_base:format=duration", "-of", "json", str(path)])
            info = json.loads(probe)
            if not info["streams"]:
                raise ValueError(f"No video track in {path}")
            video = info["streams"][0]
            duration = (int(video["duration_ts"]) * Fraction(video["time_base"]) if "duration_ts" in video
                        else Fraction(info["format"]["duration"]))
            if Fraction(source["trimFrames"] + frames - 1, fps) >= duration:
                raise ValueError(f"Video ends before the last requested frame: {path}")
    total = sum(source["frames"] for source in spec["sources"]) - overlap
    if "frames" in spec and (type(spec["frames"]) is not int or spec["frames"] != total):
        raise ValueError(f"Expected {total} programme frames after the {overlap}-frame overlap")
    spec["frames"] = total


def remotion(spec, location, project):
    if "transition" in spec:
        transition_spec(spec, location)
        template = "shot-transition.jsx"
    else:
        validate(spec, location)
        template = "evidence-cut.jsx"
    public = project / "public"
    public.mkdir(exist_ok=True)
    for i, source in enumerate(spec["sources"]):
        path = source.pop("file")
        name = f"source-{i}{path.suffix.lower()}"
        if "transition" in spec and path.suffix.lower() in (".tif", ".tiff"):
            name = f"source-{i}.png"
            with tempfile.TemporaryDirectory(dir=public) as temp:
                staged = Path(temp) / name
                with Image.open(path) as image:
                    ImageOps.exif_transpose(image).convert("RGBA").save(staged)
                os.replace(staged, public / name)
        else:
            stage_media(path, public / name)
        source.update(asset=name, still=path.suffix.lower() in STILLS)
        if "legend" in source:
            legend = source["legend"]
            sticker = label_image(legend["text"],legend["color"],min(spec["width"],spec["height"]))
            label_name = f"source-{i}-label.png"
            sticker.save(public / label_name)
            source.update(labelAsset=label_name, labelWidth=sticker.width, labelHeight=sticker.height)
    (project / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2))
    view = template.replace(".jsx", "-view.jsx")
    shutil.copy2(ASSETS / view, project / view)
    shutil.copy2(ASSETS / template, project / "index.jsx")


def hyperframes(spec, project, gsap):
    if gsap is None or not gsap.is_file():
        raise ValueError("Pass --gsap /path/to/node_modules/gsap/dist/gsap.min.js")
    rows = spec["rows"]
    duration = spec["duration"]
    reveal = spec["reveal"]
    if len(rows) != 2 or not 0 < reveal <= duration - 0.4:
        raise ValueError("Provide two rows and allow the second reveal at least 0.4s")
    content = (ASSETS / "claim-boundary.html").read_text()
    values = {"DURATION": str(duration), "REVEAL": str(reveal), "TAIL": str(duration - 0.01)}
    for index, row in enumerate(rows, 1):
        values[f"LABEL{index}"] = escape(row["label"])
        values[f"COPY{index}"] = escape(row["copy"])
    content = re.sub(r"__(\w+)__", lambda match: values[match[1]], content)
    (project / "index.html").write_text(content)
    stage_media(gsap, project / "gsap.min.js")


def main():
    p = ArgumentParser(description=__doc__)
    p.add_argument("engine", choices=("remotion", "hyperframes"))
    p.add_argument("spec", type=Path)
    p.add_argument("--project", type=Path, required=True)
    p.add_argument("--gsap", type=Path)
    args = p.parse_args()
    args.project.mkdir(parents=True, exist_ok=True)
    spec = json.loads(args.spec.read_text())
    if args.engine == "remotion":
        remotion(spec, args.spec.resolve().parent, args.project)
    else:
        hyperframes(spec, args.project, args.gsap)


if __name__ == "__main__":
    main()
