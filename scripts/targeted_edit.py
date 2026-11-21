"""Replace a baked picture interval in a CFR H.264 master without re-rendering other shots.

Frame bounds are zero-based and end-exclusive. Supply a complete replacement shot including
any on-screen captions and brand. The original file and its audio are never overwritten.
Requires the usual FFmpeg/FFprobe command-line tools, including libx264.
"""
import argparse
import color_finish
import concurrent.futures
import fcntl
import hashlib
import json
import os
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path



def command(args):
    proc = subprocess.run([str(x) for x in args], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode:
        raise ValueError(proc.stderr.decode(errors="replace").strip() or f"{args[0]} failed")
    return proc.stdout


def ff(*args):
    return command(["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-xerror", "-y", *args])


def probe(path, *args):
    return json.loads(command(["ffprobe", "-v", "error", *args, "-of", "json", path]))


def digest(path):
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def video(path):
    streams = probe(path, "-show_streams")["streams"]
    pictures = [s for s in streams if s["codec_type"] == "video"]
    if len(pictures) != 1 or any(s["codec_type"] not in ("video", "audio") for s in streams):
        raise ValueError(f"{path}: expected one video track and optional audio tracks")
    return pictures[0]


def timing(path, stream):
    packets = probe(path, "-select_streams", "v:0", "-show_packets",
                    "-show_entries", "packet=pts,dts,duration,flags")["packets"]
    fps = Fraction(stream["avg_frame_rate"])
    unit = Fraction(1, 1) / (fps * Fraction(stream["time_base"]))
    if unit.denominator != 1 or not packets:
        raise ValueError(f"{path}: expected a constant-rate timeline")
    step = int(unit)
    pts = [int(p["pts"]) for p in packets]
    if sorted(pts) != list(range(0, len(pts) * step, step)) or any(int(p["duration"]) != step for p in packets):
        raise ValueError(f"{path}: frame timestamps must form a zero-based CFR sequence")
    order = [x // step for x in pts]
    if order[0] != 0 or "K" not in packets[0]["flags"]:
        raise ValueError(f"{path}: video must start on an independent keyframe")
    return fps, order, packets


def same_picture_format(a, b):
    return all(a.get(k) == b.get(k) for k in ("width", "height", "pix_fmt"))


def copy_video(source, dest, timescale, *, limit=None, seek=None):
    seek_args = ["-ss", seek] if seek is not None else []
    limit_args = ["-frames:v", limit] if limit is not None else []
    ff(*seek_args, "-i", source, "-map", "0:v:0", *limit_args, "-c:v", "copy",
       "-video_track_timescale", timescale, dest)


def safe_frontier(order, start):
    """A copied decode-order prefix must contain exactly the displayed frames [0, k)."""
    largest = -1
    frontier = 0
    for index, frame in enumerate(order[:start], 1):
        largest = max(largest, frame)
        if largest == index - 1:
            frontier = index
    return frontier


def bounds(order, packets, start, end):
    if not (0 <= start < end <= len(order)):
        raise ValueError(f"Interval must be within 0..{len(order)}")
    prefix = safe_frontier(order, start)
    suffix = next((i for i, pkt in enumerate(packets)
                   if i >= end and order[i] == i and "K" in pkt["flags"]), len(order))
    return prefix, suffix


def seconds(frames, fps):
    return f"{float(Fraction(frames, 1) / fps):.9f}"


def frame_hashes(path):
    raw = ff("-i", path, "-map", "0:v:0", "-vsync", 0, "-pix_fmt", "yuv420p",
             "-threads:v", 1, "-f", "framemd5", "-").decode()
    return [s.rsplit(",", 1)[-1].strip() for s in raw.splitlines() if s and not s.startswith("#")]


def verify_random_access(result, fps, order, packets, start, end, sequential):
    # An MP4 can decode in sequence yet fail when a player jumps to a new parameter set.
    # Seek separately to every indicated random-access point and to both sides of the edit.
    keys = [order[i] for i, packet in enumerate(packets) if "K" in packet["flags"]]
    seek_frames = sorted(set(keys + [n for n in (start-1, start, end-1, end, len(order)-1)
                                   if 0 <= n < len(order)]))

    def verify_seek(n):
        raw = ff("-ss", seconds(n, fps), "-i", result, "-map", "0:v:0", "-frames:v", 1,
                 "-pix_fmt", "yuv420p", "-threads:v", 1, "-f", "framemd5", "-").decode()
        hashes = [s.rsplit(",", 1)[-1].strip() for s in raw.splitlines()
                  if s and not s.startswith("#")]
        if hashes != [sequential[n]]:
            raise ValueError(f"Random access at frame {n} differs from sequential decode")
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(verify_seek, seek_frames))
    return seek_frames


def audio_signature(path):
    # Include packet timing and AAC priming/discard data as well as compressed payload.
    data = probe(path, "-select_streams", "a", "-show_streams", "-show_packets",
                 "-show_data_hash", "sha256", "-show_entries",
                 "stream=index,codec_name,time_base,channels,sample_rate,extradata_hash:"
                 "packet=stream_index,pts,dts,duration,flags,data_hash:packet_side_data")
    # When the input's audio is track 0, map it to its audio ordinal for comparison.
    streams = data.get("streams", [])
    ordinal = {s["index"]: i for i, s in enumerate(streams)}
    for p in data.get("packets", []):
        p["stream_index"] = ordinal[p["stream_index"]]
    for s in streams:
        s.pop("index")
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest(), len(streams)


def fold(hashes):
    return hashlib.sha256("".join(hashes).encode()).hexdigest()


def build(args, folder, identity):
    base, replacement = args.base, args.replacement
    b, r = video(base), video(replacement)
    if b["codec_name"] != "h264" or b["pix_fmt"] != "yuv420p" or b.get("sample_aspect_ratio", "1:1") not in ("1:1", "N/A"):
        raise ValueError("The master must be square-pixel H.264 yuv420p")
    if b.get("field_order", "progressive") not in ("progressive", "unknown"):
        raise ValueError("The master must be progressive")
    fps, order, packets = timing(base, b)
    start, end = args.start_frame, args.end_frame
    if not (0 <= start < end <= len(order)):
        raise ValueError(f"Interval must be within 0..{len(order)}")
    rfps, rorder, _ = timing(replacement, r)
    if r.get("sample_aspect_ratio", "1:1") not in ("1:1", "N/A") or r.get("field_order", "progressive") not in ("progressive", "unknown"):
        raise ValueError("The replacement must also be square-pixel, progressive video")
    if not same_picture_format(b, r) or rfps != fps or len(rorder) != end - start:
        raise ValueError("Replacement resolution, pixel format, frame rate and selected frame count must match")
    timescale = Fraction(b["time_base"]).denominator
    step = Fraction(timescale, 1) / fps
    if step.denominator != 1:
        raise ValueError("Unsupported track time base")

    prefix, suffix = bounds(order, packets, start, end)
    if prefix != start or suffix != end:
        raise ValueError(
            f"Requested [{start}, {end}) crosses AVC reference pictures in this master. "
            f"The nearest enclosing stream-copy boundaries are [{prefix}, {suffix}). "
            f"Render that complete local interval and record its scope in the review ticket, "
            f"or use a source master with independent cuts at the requested boundaries. "
            f"No candidate was written."
        )
    parts = []

    def add(name, count):
        parts.append((folder / name, count))
        return parts[-1][0]

    if prefix:
        copy_video(base, add("prefix.mp4", prefix), timescale, limit=prefix)
    middle = add("replacement.mp4", end - start)
    managed = all(b.get(k) == v for k, v in color_finish.OUTPUT.items())
    prepare_color = managed and any(r.get(k) != v for k, v in color_finish.OUTPUT.items())
    copied_replacement = r["codec_name"] == "h264" and not prepare_color
    if copied_replacement:
        copy_video(replacement, middle, timescale)
    else:
        filters = ["-vf", color_finish.source_filter(r)[0]] if prepare_color else []
        flags = color_finish.FLAGS if managed else []
        ff("-i", replacement, "-map", "0:v:0", *filters, "-c:v", "libx264", "-crf", 17, "-preset", "fast",
           "-pix_fmt", "yuv420p", *flags, "-threads", 4, "-video_track_timescale", timescale, middle)
    if suffix < len(order):
        copy_video(base, add("suffix.mp4", len(order) - suffix), timescale, seek=seconds(suffix, fps))

    # CFR composition timestamps must survive joins between GOPs of different B-frame depths.
    # Set one monotonic decode clock, leaving every source packet presentation timestamp intact.
    delay = 0
    concat = ["ffconcat version 1.0"]
    for part, count in parts:
        pfps, porder, _ = timing(part, video(part))
        if pfps != fps or len(porder) != count:
            raise ValueError(f"{part.name}: expected {count} frames at {fps}, found {len(porder)} at {pfps}")
        delay = max(delay, max(i - frame for i, frame in enumerate(porder)))
        concat.extend([f"file {part.name}", f"duration {seconds(count, fps)}"])
    playlist = folder / "list.ffconcat"
    playlist.write_text("\n".join(concat) + "\n")
    result = folder / "candidate.mp4"
    ff("-f", "concat", "-safe", 1, "-i", playlist, "-i", base, "-map", "0:v:0", "-map", "1:a?",
       "-map_metadata", 1, "-map_chapters", 1, "-c", "copy", "-bsf:v",
       f"setts=pts=PTS:dts=(N-{delay})*{int(step)}:duration={int(step)}",
       "-video_track_timescale", timescale, "-movflags", "+faststart", result)

    result_stream = video(result)
    if managed:
        color_finish.verify(result_stream)
    out_fps, new_order, new_packets = timing(result, result_stream)
    if fps != out_fps or len(new_order) != len(order):
        raise ValueError("Candidate changed master timing")
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(frame_hashes, p) for p in (base, result, middle)]
        old, new, expected_middle = (future.result() for future in futures)
    if len(old) != len(order) or len(new) != len(order):
        raise ValueError("Incomplete frame decode")
    if old[:start] != new[:start] or old[end:] != new[end:]:
        raise ValueError("Decoded picture changed outside the requested frames; candidate was discarded")
    if expected_middle != new[start:end]:
        raise ValueError("Candidate contains different pixels from the rendered replacement")
    changed = [i for i in range(start, end) if old[i] != new[i]]
    if not changed:
        raise ValueError("Replacement did not change any visible frames")
    audio, tracks = audio_signature(base)
    if audio_signature(result) != (audio, tracks):
        raise ValueError("Audio packets or timestamps changed")
    seek_frames = verify_random_access(result, fps, new_order, new_packets, start, end, new)
    # Detect a source rewritten while FFmpeg was reading it. The candidate is never published stale.
    if digest(base) != identity["base_sha256"] or digest(replacement) != identity["replacement_sha256"]:
        raise ValueError("Input changed during the patch")
    return dict(schema=2, **identity, candidate_sha256=digest(result), frames=len(order), fps=str(fps),
                requested_frames=[start, end], frames_with_visible_change=len(changed),
                changed_span=[changed[0], changed[-1]+1], verified_outside_frames=len(order)-(end-start),
                outside_frame_md5_sequence_sha256=fold(old[:start]+old[end:]),
                unchanged_audio_tracks=tracks, audio_packet_timing_sha256=audio,
                stream_copied_source_frames=prefix+len(order)-suffix,
                random_access_frames=seek_frames, replacement_picture_sha256=fold(expected_middle),
                replacement_stream_copied=copied_replacement,
                locally_encoded_picture_frames=(0 if copied_replacement else end-start))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("base", type=Path)
    p.add_argument("replacement", type=Path, nargs="?", help="Rendered final-quality shot for this interval")
    p.add_argument("--plan", action="store_true", help="Show the exact copy boundaries before rendering the local shot")
    p.add_argument("--start-frame", type=int, required=True)
    p.add_argument("--end-frame", type=int, required=True)
    p.add_argument("--output", type=Path)
    p.add_argument("--report", type=Path, help="Default: OUTPUT.patch.json")
    p.add_argument("--expect-base", help="SHA-256 of the exact revision examined by reviewers")
    args = p.parse_args()
    if args.plan:
        stream = video(args.base)
        fps, order, packets = timing(args.base, stream)
        region = bounds(order, packets, args.start_frame, args.end_frame)
        actual_sha = digest(args.base)
        if args.expect_base and actual_sha != args.expect_base.lower():
            raise ValueError("Master hash differs from the reviewed revision")
        print(json.dumps({"base_sha256": actual_sha, "fps": str(fps),
                          "size": [stream["width"], stream["height"]], "frames": len(order),
                          "requested_frames": [args.start_frame, args.end_frame],
                          "nearest_stream_copy_frames": region,
                          "exact_stream_copy": region == (args.start_frame, args.end_frame)}, indent=2))
        return
    if args.output is None or args.replacement is None:
        p.error("For editing provide a replacement and --output, or use --plan")
    args.base, args.replacement, args.output = [x.resolve() for x in (args.base, args.replacement, args.output)]
    report = (args.report or args.output.with_suffix(".patch.json")).resolve()
    if args.output in (args.base, args.replacement) or report in (args.base, args.replacement, args.output):
        raise ValueError("Choose distinct output paths and preserve both inputs")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    with (args.output.parent / (args.output.name + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        identity = {"base_sha256": digest(args.base), "replacement_sha256": digest(args.replacement)}
        if args.expect_base and args.expect_base.lower() != identity["base_sha256"]:
            raise ValueError("Master hash differs from the reviewed revision")
        if args.output.exists() and report.exists():
            previous = json.loads(report.read_text())
            same_revision = (all(previous.get(k) == v for k, v in identity.items()) and
                             previous.get("requested_frames") == [args.start_frame, args.end_frame] and
                             previous.get("candidate_sha256") == digest(args.output))
            if same_revision and previous.get("schema") == 2:
                print(json.dumps(previous, ensure_ascii=False, indent=2))
                return
            if same_revision and "schema" not in previous and previous.get("local_lossless_dependency_intervals") == []:
                # Existing exact-copy revisions can receive the random-access checks without editing media.
                fps, order, packets = timing(args.output, video(args.output))
                previous["random_access_frames"] = verify_random_access(
                    args.output, fps, order, packets, args.start_frame, args.end_frame, frame_hashes(args.output))
                previous["schema"] = 2
                result_text = json.dumps(previous, ensure_ascii=False, indent=2)
                with tempfile.NamedTemporaryFile(mode="w", dir=report.parent, delete=False) as f:
                    f.write(result_text + "\n")
                    staged = Path(f.name)
                try:
                    os.replace(staged, report)
                finally:
                    staged.unlink(missing_ok=True)
                print(result_text)
                return
        if report.exists() and not args.output.exists():
            previous = json.loads(report.read_text())
            if (previous.get("candidate") == str(args.output) and
                previous.get("requested_frames") == [args.start_frame, args.end_frame] and
                all(previous.get(k) == v for k, v in identity.items())):
                # Recover an interruption between publishing this report and its candidate.
                report.unlink()
        if args.output.exists() or report.exists():
            raise ValueError("Choose a new candidate and report path; an unrelated or incomplete revision exists")
        with tempfile.TemporaryDirectory(prefix=".picture-patch-", dir=args.output.parent) as work:
            folder = Path(work)
            result = build(args, folder, identity)
            result.update(base=str(args.base), replacement=str(args.replacement), candidate=str(args.output))
            result_text = json.dumps(result, ensure_ascii=False, indent=2)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=report.parent, delete=False) as f:
                staged_report = Path(f.name)
                f.write(result_text + "\n")
            linked = False
            try:
                # No-replace links also protect callers using different lock/report paths.
                os.link(staged_report, report)
                linked = True
                os.link(folder / "candidate.mp4", args.output)
            except Exception:
                if linked:
                    report.unlink(missing_ok=True)
                raise
            finally:
                staged_report.unlink(missing_ok=True)
            print(result_text)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError) as exc:
        raise SystemExit(str(exc))
