"""Assemble a local submission draft and attach sourced feedback to the exact edit."""
import argparse
from datetime import datetime
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from urllib.parse import urlparse

from PIL import Image
from review import captions
from review_bundle import digest, fingerprint, receipt_for, released_snapshot, write_json

KINDS = {
    "first_view": "首次观看",
    "real_phone": "实体手机",
    "platform_preview": "平台转码回看",
    "retention": "平台观看数据",
    "comment": "观众评论",
}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def required(row, key):
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"缺少 {key}")
    return value.strip()


def probe_shots(video, timeline):
    info = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
        "stream=width,height,avg_frame_rate,nb_frames", "-of", "json", str(video),
    ]))["streams"][0]
    fps = Fraction(str(timeline["fps"]))
    if fps <= 0 or abs(float(fps - Fraction(info["avg_frame_rate"]))) > 0.001:
        raise ValueError("镜头表帧率与投稿视频不同")
    cursor = 0
    seen = set()
    for shot in timeline["shots"]:
        name = required(shot, "id")
        if (name in seen or shot["start_frame"] != cursor or
                type(shot["frames"]) is not int or shot["frames"] < 1):
            raise ValueError(f"镜头范围无效或不连续：{name}")
        seen.add(name)
        cursor += shot["frames"]
    if cursor != int(info["nb_frames"]) or cursor != timeline.get("frames", cursor):
        raise ValueError("镜头表未覆盖投稿视频的全部帧")
    return {"width": info["width"], "height": info["height"], "frames": cursor, "fps": str(fps)}


def handoff_text(spec, files, scope, credits):
    tags = spec.get("tags", [])
    if not isinstance(tags, list) or any(not isinstance(t, str) or not t.strip() for t in tags):
        raise ValueError("tags 应是文字数组")
    main = [
        f"# {required(spec, 'platform')}投稿稿件", "", f"本地版本　{scope}", "",
        f"[视频]({files['video']}) · [封面]({files['cover']}) · [字幕]({files['captions']})", "",
        "## 标题", "", required(spec, "title"), "",
        "## 简介（含署名，可整段复制）", "", required(spec, "description"), "",
    ]
    if spec.get("ai_usage"):
        main += [required(spec, "ai_usage"), ""]
    main += [credits, ""]
    if tags:
        main += ["## 标签", "", "、".join(tags), ""]
    if spec.get("ai_usage"):
        main += ["## 投稿时的创作声明", "", required(spec, "declaration"), ""]
        if spec.get("declaration_source"):
            main += [f"[平台说明]({required(spec, 'declaration_source')})", ""]
    return "\n".join(main)


def make_bundle(project, spec_path, out):
    spec = read(spec_path)
    media = released_snapshot(project / required(spec, "video"))
    receipt = receipt_for(media)
    registered = receipt.is_file()
    approved = media.with_name(media.name + ".review.json").is_file()
    scope = "已签发成片" if approved else "试剪候选"
    if registered:
        fingerprint(media)
    signed = read(receipt) if registered else {}
    paths = {"video": media}
    for key in ("cover", "captions", "credits", "timeline"):
        paths[key] = project / required(spec, key)
    # Resolve signed sidecars through the pinned version, even during a local release update.
    if registered:
        sidecars = signed["sidecars"]
        allowed = {"captions": {"captions.srt": sidecars["captions.srt"]},
                   "cover": {name: sha for name, sha in sidecars.items() if name.startswith("covers/")},
                   "credits": signed.get("delivery_credits", {})}
        for key, inventory in allowed.items():
            checksum = digest(paths[key])
            matches = [name for name, sha in inventory.items() if sha == checksum]
            if not matches:
                raise ValueError(f"{key} 不属于已登记的这版成片")
            paths[key] = receipt.parent / matches[0]
    if signed.get("timeline_sha256") and signed["timeline_sha256"] != digest(paths["timeline"]):
        raise ValueError("镜头表与成片回执不符")
    names = {k: k + p.suffix.lower() for k, p in paths.items()}
    out.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=out.parent) as temp:
        draft = Path(temp) / "bundle"
        draft.mkdir()
        for k, p in paths.items():
            shutil.copyfile(p, draft / names[k])
        if registered:
            frozen = {**signed.get("sidecars", {}), **signed.get("delivery_credits", {})}
            expected = {"video": signed["sha256"], "timeline": signed.get("timeline_sha256")}
            for k in ("cover", "captions", "credits"):
                expected[k] = frozen[paths[k].relative_to(receipt.parent).as_posix()]
            if any(sha and sha != digest(draft / names[k]) for k, sha in expected.items()):
                raise ValueError("成片或附件在取用期间已变化")
        timeline = read(draft / names["timeline"])
        info = probe_shots(draft / names["video"], timeline)
        with Image.open(draft / names["cover"]) as im:
            im.verify()
        if names["captions"] != "captions.srt":
            raise ValueError("需提供本版 SRT 字幕")
        cues = list(captions(draft))
        fps = float(Fraction(info["fps"]))
        if not cues or any(not 0 <= a < b <= info["frames"] / fps + .001 for a, b, _ in cues):
            raise ValueError("字幕时间超出本版影片")
        expected_cues = timeline.get("captions", [])
        if expected_cues and all(isinstance(c, dict) and {"text","start_frame","end_frame"} <= c.keys()
                                 for c in expected_cues):
            actual = [(round(a*fps), round(b*fps), txt) for a, b, txt in cues]
            expected = [(c["start_frame"], c["end_frame"], c["text"]) for c in expected_cues]
            if actual != expected:
                raise ValueError("字幕时序或文字与镜头表不符")
        credits = (draft / names["credits"]).read_text(encoding="utf-8").strip()
        if not credits:
            raise ValueError("本版署名为空")
        page = handoff_text(spec, names, scope, credits)
        page_name = "投稿稿件.md"
        (draft / page_name).write_text(page, encoding="utf-8")
        names["copy"] = page_name
        manifest = {"platform": spec["platform"], "scope": scope, "video": info, "files": names,
                    "sha256": {k: digest(draft / name) for k, name in names.items()}}
        write_json(draft / "manifest.json", manifest)
        if out.exists() and (out / "manifest.json").is_file() and read(out / "manifest.json") == manifest:
            return status(out)
        try:
            draft.rename(out)
        except OSError:
            if out.exists():
                raise ValueError(f"输出已有其他内容，请为新版选新的 --out：{out}") from None
            raise
    return status(out)


def status(bundle):
    manifest = read(bundle / "manifest.json")
    for k, name in manifest["files"].items():
        if digest(bundle / name) != manifest["sha256"][k]:
            raise ValueError(f"本地投稿稿件与登记版本不同：{name}")
    records = []
    for path in sorted((bundle / "feedback").glob("*/record.json")):
        rec = read(path)
        if rec["media_sha256"] != manifest["sha256"]["video"]:
            raise ValueError("反馈对应另一版视频")
        if rec.get("evidence_file") and digest(path.parent / rec["evidence_file"]) != rec["evidence_sha256"]:
            raise ValueError("反馈来源附件已变化")
        public = {k: rec[k] for k in ("kind", "observed_at", "start", "end", "shots", "note", "next_test") if k in rec}
        public["record"] = str(path.relative_to(bundle))
        records.append(public)
    return {"platform": manifest["platform"], "scope": manifest["scope"],
            "media_sha256": manifest["sha256"]["video"], "files": manifest["files"],
            "observations": records, "awaiting_evidence": [name for k, name in KINDS.items()
                if not any(row["kind"] == k for row in records)]}


def web_url(value):
    url = urlparse(value)
    return url.scheme in ("http", "https") and bool(url.netloc)


def add_feedback(bundle, source):
    report = status(bundle)
    row = read(source)
    kind = required(row, "kind")
    if kind not in KINDS or required(row, "media_sha256") != report["media_sha256"]:
        raise ValueError("反馈种类或视频版本不符")
    when = required(row, "observed_at")
    datetime.fromisoformat(when)
    record = {"kind": kind, "media_sha256": report["media_sha256"], "observed_at": when,
              "note": required(row, "note"), "shots": []}
    if kind in ("comment", "retention"):
        url = required(row, "video_url")
        if not web_url(url):
            raise ValueError("请给出对应稿件的网址")
        record["video_url"] = url
    if "start" in row or "end" in row:
        timeline = read(bundle / report["files"]["timeline"])
        start, end = float(row["start"]), float(row.get("end", row["start"]))
        fps = float(Fraction(str(timeline["fps"])))
        total = sum(s["frames"] for s in timeline["shots"])
        if not (math.isfinite(start) and 0 <= start <= end <= total / fps and start < total / fps):
            raise ValueError("反馈时码不在本版时间线内")
        record.update(start=start, end=end)
        for shot in timeline["shots"]:
            left = shot["start_frame"] / fps
            right = (shot["start_frame"] + shot["frames"]) / fps
            if (left <= start < right) or (start < right and end > left):
                record["shots"].append(shot["id"])
    if row.get("next_test"):
        record["next_test"] = required(row, "next_test")
    evidence = required(row, "evidence")
    folder = bundle / "feedback"
    folder.mkdir(exist_ok=True)
    with TemporaryDirectory(dir=folder) as temp:
        slot = Path(temp) / "entry"
        slot.mkdir()
        if web_url(evidence):
            record["evidence_url"] = evidence
        else:
            original = source.parent / evidence
            name = "evidence" + original.suffix.lower()
            shutil.copyfile(original, slot / name)
            record.update(evidence_file=name, evidence_sha256=digest(slot / name))
        key = hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        write_json(slot / "record.json", record)
        final = folder / key
        try:
            slot.rename(final)
        except OSError:
            if not (final / "record.json").is_file() or read(final / "record.json") != record:
                raise
    return {**record, "record": str(final / "record.json")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    for name in ("project", "spec", "out"):
        prepare.add_argument("--" + name, type=Path, required=True)
    sub.add_parser("status").add_argument("--bundle", type=Path, required=True)
    feedback = sub.add_parser("feedback")
    feedback.add_argument("--bundle", type=Path, required=True)
    feedback.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = make_bundle(args.project, args.spec, args.out)
        elif args.command == "feedback":
            result = add_feedback(args.bundle, args.input)
        else:
            result = status(args.bundle)
    except (KeyError, ValueError, OSError, subprocess.CalledProcessError) as err:
        parser.exit(1, str(err) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
