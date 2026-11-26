"""Search the offline Remotion catalog, stage an editable composition, or render a project shot."""

import argparse
from copy import deepcopy
from contextlib import ExitStack, contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from fractions import Fraction

from production_run import atomic_json, digest
from remotion_source_usage import media_usage, MOVING

SKILL = Path(__file__).resolve().parents[1]
LIBRARY = SKILL / "assets/remotion-library"
RUNTIME = Path(os.environ.get("CREATOR_REMOTION", Path.home() / ".cache/creator-video-motion/remotion"))
GROUPS = {
    "data": "数据与流程",
    "text": "文字与包装",
    "media": "素材与运镜",
    "transitions": "镜头转场",
    "atmosphere": "背景与片尾",
}
MEDIA_TYPES = {".svg", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".mp4", ".m4v", ".mov", ".webm", ".mkv"}


def catalog():
    result = {}
    for group, category in GROUPS.items():
        for entry in json.loads((LIBRARY / group / "catalog.json").read_text()):
            key = entry["id"]
            if key in result or not re.fullmatch(r"[a-z][a-z0-9-]*", key):
                raise ValueError(f"Invalid or duplicated effect id: {key}")
            example = dict(entry["example"])
            for param in entry.get("media", []):
                if example.get(param, "").startswith("examples/"):
                    example[param] = "@library/" + group + "/" + example[param]
            spec = dict(effect=key, width=1920, height=1080, fps=30, frames=90, params=example)
            result[key] = dict(entry, group=group, category=category, spec=spec)
    return result


def safe_path(root, name):
    path = (root / name).resolve()
    if root.resolve() not in (path, *path.parents):
        raise ValueError(f"Path outside project: {name}")
    return path


def score(entry, query):
    text = " ".join(
        [entry["id"], entry["name"], entry["category"], *entry["tags"], entry["description"]]
    ).casefold()
    terms = query.strip().casefold().split()
    return sum(3 for w in terms if w in text) + sum(
        len(tag) for tag in entry["tags"] if tag.casefold() in query.casefold()
    )


def resolve_frame(anchor, timeline, timing):
    match = re.fullmatch(r"@([^:#]+)(?::([^#]+?)(?:#([1-9]\d*))?)?", anchor)
    if not match:
        raise ValueError(f"Invalid word cue {anchor}")
    ident, word, repeat = match.groups()
    beat = next((b for b in timeline.get("beats", []) if b["id"] == ident), None)
    if beat is None:
        raise ValueError(
            f"Missing word clock for {ident}; prepare this edit with word_clock or reviewed timings"
        )
    if word is None or word == "END":
        seconds = beat["end" if word else "start"]
    else:
        pos = -1
        for _ in range(int(repeat or "1")):
            pos = beat["text"].find(word, pos + 1)
            if pos < 0:
                raise ValueError(f"Word not found: {anchor}")
        seconds = beat["chars"][pos][1]
    frame = math.ceil(seconds * timeline["fps"] - 1e-8) - timing["start_frame"]
    if not 0 <= frame < timing["frames"]:
        raise ValueError(f"Word cue outside this shot: {anchor}")
    return frame


def timed_params(settings, timeline, timing):
    """Resolve word cues on a copy. An indexed path changes one field of an existing layer."""
    params = deepcopy(settings.get("params", {}))
    if not isinstance(params, dict):
        raise ValueError("Effect params must be an object")
    for path, anchor in settings.get("cues", {}).items():
        if not re.fullmatch(r"[A-Za-z_]\w*(?:\[\d+\]|\.[A-Za-z_]\w*)*", path):
            raise ValueError(f"Invalid effect cue path: {path}")
        target = params
        keys = re.findall(r"[A-Za-z_]\w*|\d+", path)
        for key in keys[:-1]:
            if isinstance(target, list) and key.isdecimal() and int(key) < len(target):
                target = target[int(key)]
            elif isinstance(target, dict) and key in target:
                target = target[key]
            else:
                raise ValueError(f"No such layer in effect cue path: {path}")
        frame = resolve_frame(anchor, timeline, timing)
        last = keys[-1]
        if isinstance(target, dict) and not last.isdecimal():
            target[last] = frame
        elif isinstance(target, list) and last.isdecimal() and int(last) < len(target):
            target[int(last)] = frame
        else:
            raise ValueError(f"Invalid target in effect cue path: {path}")
    return params


def project_params(shot, timeline, timing):
    settings = shot["remotion_effect"]
    params = timed_params(settings, timeline, timing)
    if "placement" in settings:
        from picture_reservations import place_lower_third
        params["startFrame"], params["endFrame"] = place_lower_third(shot, timing["frames"], params)
    return params


def placement_record(settings, params):
    return dict(frames=[params["startFrame"], params["endFrame"]], box=settings["placement"]["box"])


def engine_files(entry, *, placement=False):
    group = entry["group"]
    files = [
        "scripts/remotion_library.py",
        "scripts/remotion_source_usage.py",
        f"assets/remotion-library/{group}/effects.jsx",
        f"assets/remotion-library/{group}/catalog.json",
        "assets/remotion-library/composition.jsx",
    ]
    if group == "transitions":
        files.extend(["assets/motion/evidence-cut-view.jsx", "assets/motion/shot-transition-view.jsx"])
    if placement:
        files.append("scripts/picture_reservations.py")
    return files


def probe_video_duration(path):
    info = json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "V:0",
                "-show_entries",
                "stream=duration_ts,time_base:format=duration",
                "-of",
                "json",
                str(path),
            ]
        )
    )
    if not info["streams"]:
        raise ValueError(f"No video track in {path.name}")
    stream = info["streams"][0]
    if "duration_ts" in stream:
        return int(stream["duration_ts"]) * Fraction(stream["time_base"])
    return Fraction(info["format"]["duration"])


def check_source_duration(path, usage, fps, *, allow_loop=False):
    if not usage or path.suffix.lower() not in MOVING:
        return
    duration = probe_video_duration(path)
    if allow_loop:
        start_s = usage["range"][0]
        if Fraction(start_s) >= duration:
            raise ValueError(f"{path.name} ends before requested backgroundStart {start_s}s")
        usage["range"][1] = min(usage["range"][1], float(duration))
        return
    last_frame = round(usage["range"][1] * fps) - 1
    if Fraction(last_frame, fps) >= duration:
        raise ValueError(f"{path.name} ends before requested source frame {last_frame}")


@contextmanager
def project_lock(project):
    work = project / ".creator-run"
    work.mkdir(exist_ok=True)
    with (work / "project.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        yield


def _measure_media_luma(path, *, start_s=0.0):
    """Sample median Rec.709 normalized luminance (0..1) of background media for adaptive scrim."""
    try:
        if path.suffix.lower() in MOVING:
            raw = subprocess.check_output([
                "ffmpeg", "-nostdin", "-v", "error", "-ss", f"{max(0.0, float(start_s)):.3f}",
                "-i", str(path), "-frames:v", "1", "-vf", "scale=160:90,format=rgb24",
                "-f", "rawvideo", "-",
            ])
            if len(raw) >= 160 * 90 * 3:
                import numpy as np
                arr = np.frombuffer(raw[:160 * 90 * 3], np.uint8).reshape(90, 160, 3).astype(np.float32)
                luma = (0.2126 * arr[:, :, 0] + 0.7152 * arr[:, :, 1] + 0.0722 * arr[:, :, 2]) / 255.0
                return round(float(np.median(luma)), 3)
        else:
            from PIL import Image
            import numpy as np
            with Image.open(path) as im:
                arr = np.asarray(im.convert("RGB").resize((160, 90)), dtype=np.float32)
                luma = (0.2126 * arr[:, :, 0] + 0.7152 * arr[:, :, 1] + 0.0722 * arr[:, :, 2]) / 255.0
                return round(float(np.median(luma)), 3)
    except Exception:
        return None
    return None


def stage(spec, base, target, entry, *, film=False):
    if "placement" in spec and not film:
        raise ValueError("Resolve placement on the prepared project with render --project and --shot")
    for key in ("width", "height", "fps", "frames"):
        if type(spec.get(key)) is not int or spec[key] < 1:
            raise ValueError(f"{key} must be a positive integer")
    if spec["width"] % 2 or spec["height"] % 2:
        raise ValueError("Use even picture dimensions")
    if not isinstance(spec.get("params"), dict):
        raise ValueError("params must be an object")
    group = entry["group"]
    component = entry["component"]
    if not re.fullmatch("[A-Z][A-Za-z0-9_]*", component):
        raise ValueError("Invalid catalog component")
    if target.exists() and any(target.iterdir()):
        raise ValueError("Stage into a new or empty directory")
    public = target / "public"
    public.mkdir(parents=True, exist_ok=True)
    engines = engine_files(entry, placement="placement" in spec)
    engine = {name: digest(SKILL / name) for name in engines}
    for name in engines:
        if name.endswith(".jsx"):
            copy = target / name
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SKILL / name, copy)
    params = dict(spec["params"])
    inputs, used = {}, []
    media_keys = entry.get("media", [])

    def copy_media(value, label):
        if not isinstance(value, str) or not value:
            raise ValueError(f"{label} needs a local media path")
        bundled = value.startswith("@library/")
        path = (
            safe_path(LIBRARY, value[9:])
            if bundled
            else (safe_path(base, value) if film else (base / value).resolve())
        )
        if not path.is_file() or path.suffix.lower() not in MEDIA_TYPES:
            raise ValueError(f"Missing or unsupported media: {value}")
        name = f"{label}{path.suffix.lower()}"
        shutil.copyfile(path, public / name)
        checksum = digest(public / name)
        if bundled:
            engine[str(path.relative_to(SKILL))] = checksum
        else:
            inputs[os.path.relpath(path, base)] = checksum
        return name, path, bundled

    def stage_layer(value, key, *, allow_loop=False):
        name, path, bundled = copy_media(value, key or "backplate")
        relative = os.path.relpath(path, base)
        usage = media_usage(spec, entry, key, relative)
        check_source_duration(public / name, usage, spec["fps"], allow_loop=allow_loop)
        if usage and not bundled:
            used.append(usage)
        return name, public / name

    for key in media_keys:
        if params.get(key):
            can_loop = (entry["id"] == "broll-evidence-pop" and key == "source" and params.get("sourceLoop", True))
            params[key], staged_file = stage_layer(params[key], key, allow_loop=bool(can_loop))
            if can_loop and staged_file.suffix.lower() in MOVING:
                dur_s = probe_video_duration(staged_file)
                src_start = int(params.get("sourceStart", 0) or 0)
                params["sourceDurationFrames"] = max(2, int(math.floor(float(dur_s) * spec["fps"])) - src_start - 1)
    if entry["id"] == "broll-evidence-pop" and isinstance(params.get("popups"), list):
        staged_popups = []
        for idx, pop in enumerate(params["popups"]):
            item = dict(pop)
            if item.get("source"):
                item["source"], _ = stage_layer(item["source"], f"popup_{idx}", allow_loop=True)
            staged_popups.append(item)
        params["popups"] = staged_popups
    if entry["id"] == "infinite-canvas" and isinstance(params.get("nodes"), list):
        staged_nodes = []
        for idx, node in enumerate(params["nodes"]):
            item = dict(node)
            if item.get("image"):
                item["image"], _ = stage_layer(item["image"], f"node_{idx}", allow_loop=True)
            staged_nodes.append(item)
        params["nodes"] = staged_nodes
    background = spec.get("background")
    bg_loop = spec.get("backgroundLoop", True)
    bg_start = spec.get("backgroundStart", 0)
    bg_duration_frames = None
    bg_luma = None
    if background:
        if not isinstance(bg_start, int) or bg_start < 0:
            raise ValueError("backgroundStart must be a nonnegative integer frame offset")
        background, bg_file = stage_layer(background, None, allow_loop=bool(bg_loop))
        if bg_file.suffix.lower() in MOVING:
            dur_s = probe_video_duration(bg_file)
            usable_frames = max(2, int(math.floor(float(dur_s) * spec["fps"])) - bg_start - 1)
            bg_duration_frames = usable_frames
        bg_luma = _measure_media_luma(bg_file, start_s=bg_start / max(1, spec["fps"]))
    clean = dict(
        width=spec["width"],
        height=spec["height"],
        fps=spec["fps"],
        frames=spec["frames"],
        params=params,
        background=background,
        backgroundStart=bg_start,
        backgroundLoop=bool(bg_loop),
        backgroundDurationFrames=bg_duration_frames,
    )
    if bg_luma is not None:
        clean["backgroundLuma"] = bg_luma
    for opt in ("backgroundScrim", "backgroundBlur", "backgroundMotion"):
        if opt in spec:
            clean[opt] = spec[opt]
    (target / "spec.json").write_text(json.dumps(clean, ensure_ascii=False))
    (target / "index.jsx").write_text(
        'import {registerRoot} from "remotion";\nimport {makeComposition} from "./assets/remotion-library/composition";\n'
        f'import {{{component}}} from "./assets/remotion-library/{group}/effects";\n'
        f'import spec from "./spec.json";\nregisterRoot(makeComposition({component}, spec));\n'
    )
    return inputs, engine, used


def stage_editable(spec, base, target, entry):
    if not target.name or target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ValueError("Stage into a new or empty directory")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as temp:
        prepared = Path(temp) / "project"
        stage(spec, base, prepared, entry)
        shutil.copyfile(LIBRARY / "runtime-package.json", prepared / "package.json")
        os.replace(prepared, target)
    print(target.resolve())


def browser():
    env = os.environ.get("CREATOR_CHROME")
    if env:
        return env
    found = next(
        (
            str(p)
            for p in (Path.home() / ".cache/hyperframes/chrome").glob("**/chrome-headless-shell")
            if p.is_file()
        ),
        None,
    )
    if found:
        return found
    mac_chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    return str(mac_chrome) if mac_chrome.is_file() else None


def render(args, entries):
    project_mode = args.project is not None or args.shot is not None
    if project_mode:
        if args.spec or not args.project or not args.shot:
            raise ValueError("Use --project and --shot together, or give a JSON spec")
        base = args.project.resolve()
        bindings = {k: digest(base / k) for k in ("edit.json", "timeline.json")}
        edit = json.loads((base / "edit.json").read_text())
        tl = json.loads((base / "timeline.json").read_text())
        if tl.get("edit_sha256") != bindings["edit.json"]:
            raise ValueError("Prepare the current edit first")
        shot = next((s for s in edit["shots"] if s["id"] == args.shot), None)
        if not shot or "remotion_effect" not in shot:
            raise ValueError("No remotion_effect configured for this shot")
        if shot.get("in") or "explainer_scene" in shot:
            raise ValueError("Use a dedicated shot visual starting at frame zero")
        timing = next(t for t in tl["shots"] if t["id"] == args.shot)
        settings = shot["remotion_effect"]
        w, h = edit.get("size", [1920, 1080])
        spec = dict(settings, width=w, height=h, fps=tl["fps"], frames=timing["frames"])
        spec["params"] = project_params(shot, tl, timing)
        picture = safe_path(base, shot["visual"])
        if any(
            s is not shot and s.get("visual") and safe_path(base, s["visual"]) == picture
            for s in edit["shots"]
        ):
            raise ValueError("Each generated shot needs its own visual path")
        if args.frame is not None and not args.out:
            raise ValueError("Specify --out preview.png")
        if args.frame is None and args.out:
            raise ValueError("A project render writes its configured shot.visual")
        target = args.out.resolve() if args.out else picture
    else:
        if not args.spec or not args.out:
            raise ValueError("Provide a spec and --out")
        base = args.spec.resolve().parent
        spec = json.loads(args.spec.read_text())
        if spec.get("cues"):
            raise ValueError("Word cues require a prepared --project and --shot")
        target = args.out.resolve()
    entry = entries[spec["effect"]]
    if args.frame is not None and (
        not 0 <= args.frame < spec["frames"] or target.suffix.lower() != ".png"
    ):
        raise ValueError("Use a PNG and a frame inside this shot")
    if args.frame is None and target.suffix.lower() != ".mp4":
        raise ValueError("Choose an MP4 output")
    if project_mode and args.frame is None:
        receipt_path = Path(str(target) + ".remotion.json")
        if target.exists() and not receipt_path.is_file():
            raise ValueError("Choose a new shot.visual to avoid replacing an existing source")
    elif target.exists():
        raise ValueError("Choose a new output file")
    binary = RUNTIME / "node_modules/.bin/remotion"
    if not binary.is_file():
        raise ValueError(f"Install the local Remotion runtime at {RUNTIME}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with ExitStack() as scratch:
        work = scratch.enter_context(tempfile.TemporaryDirectory(prefix="library-shot-", dir=RUNTIME))
        out = scratch.enter_context(tempfile.TemporaryDirectory(dir=target.parent))
        work = Path(work)
        output = Path(out) / target.name
        inputs, engine, used = stage(spec, base, work, entry, film=project_mode)
        call = [
            str(binary),
            "still" if args.frame is not None else "render",
            str(work / "index.jsx"),
            "LibraryShot",
            str(output),
            "--public-dir=" + str(work / "public"),
            "--log=error",
        ]
        if args.frame is None:
            call += ["--muted", "--codec=h264", "--color-space=bt709", "--concurrency=2"]
        else:
            call += ["--frame=" + str(args.frame)]
        exe = browser()
        if exe:
            call += ["--browser-executable=" + exe]
        # Retry once on transient headless Chrome Target.createTarget / Target closed startup glitches.
        for attempt in range(2):
            proc = subprocess.run(call, cwd=RUNTIME, capture_output=True, text=True)
            if proc.returncode == 0:
                break
            err_text = (proc.stderr or "") + (proc.stdout or "")
            if attempt == 0 and ("Target closed" in err_text or "ProtocolError" in err_text):
                import time
                time.sleep(1.0)
                continue
            if proc.stderr:
                sys.stderr.write(proc.stderr)
            raise subprocess.CalledProcessError(proc.returncode, call, output=proc.stdout, stderr=proc.stderr)
        if project_mode and args.frame is None:
            inputs.update(bindings)
            for folder, table in ((base, inputs), (SKILL, engine)):
                if any(digest(folder / name) != checksum for name, checksum in table.items()):
                    raise ValueError("An input changed during rendering; retry")
            version = json.loads((RUNTIME / "node_modules/remotion/package.json").read_text())["version"]
            receipt = dict(
                schema=1,
                shot=args.shot,
                start_frame=timing["start_frame"],
                frames=spec["frames"],
                fps=spec["fps"],
                effect=entry["id"],
                remotion_version=version,
                inputs=inputs,
                engine=engine,
                used=used,
                output_sha256=digest(output),
            )
            if "placement" in settings:
                receipt["placement"] = placement_record(settings, spec["params"])
            if target.resolve() in [(base / name).resolve() for name in inputs]:
                raise ValueError("Visual output overlaps a source input")
            atomic_json(receipt_path, receipt)
            os.replace(output, target)
        else:
            os.replace(output, target)
    print(target)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    actions = cli.add_subparsers(dest="action", required=True)
    search = actions.add_parser("list")
    search.add_argument("query", nargs="?", default="")
    search.add_argument("--json", action="store_true")
    for cmd in ("show", "example"):
        actions.add_parser(cmd).add_argument("effect")
    staging = actions.add_parser("stage")
    staging.add_argument("spec", type=Path)
    staging.add_argument("--project", type=Path, required=True)
    draw = actions.add_parser("render")
    draw.add_argument("spec", type=Path, nargs="?")
    draw.add_argument("--project", type=Path)
    draw.add_argument("--shot")
    draw.add_argument("--frame", type=int)
    draw.add_argument("--out", type=Path)
    args = cli.parse_args()
    try:
        entries = catalog()
        if args.action == "list":
            rows = list(entries.values())
            if args.query:
                rows = sorted(
                    (e for e in rows if score(e, args.query)), key=lambda e: -score(e, args.query)
                )
            if args.json:
                print(json.dumps(rows, ensure_ascii=False))
            else:
                for e in rows:
                    print(f"{e['id']:24} {e['category']} · {e['name']}")
        elif args.action in ("show", "example"):
            e = entries[args.effect]
            print(json.dumps(e if args.action == "show" else e["spec"], ensure_ascii=False, indent=2))
        elif args.action == "stage":
            spec = json.loads(args.spec.read_text())
            stage_editable(spec, args.spec.resolve().parent, args.project, entries[spec["effect"]])
        elif args.project and args.shot:
            with project_lock(args.project.resolve()):
                render(args, entries)
        else:
            render(args, entries)
    except (
        OSError,
        KeyError,
        StopIteration,
        ValueError,
        TypeError,
        subprocess.CalledProcessError,
    ) as err:
        cli.exit(1, f"Remotion library: {err}\n")


if __name__ == "__main__":
    main()
