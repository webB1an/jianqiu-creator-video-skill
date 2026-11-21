"""Register independent screenings, discuss tickets and sign one exact video for delivery."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile

import review_bundle
import story_review
import editorial_evidence
from review_minutes import render_minutes
from perception_review import missing_spots, scope_for, unchanged_motion, validate_spot

DEFAULT_LEDGER = Path("qa/editorial/ledger.json")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def words(data, field):
    value = data.get(field)
    require(isinstance(value, str) and value.strip(), f"Missing text: {field}")
    return value.strip()


def rows(data, field, required=True):
    value = data.get(field)
    require(isinstance(value, list) and (value or not required), f"Expected list: {field}")
    require(all(isinstance(row, dict) for row in value), f"Expected objects in {field}")
    return value


def name(data, field):
    return words(data, field).casefold()


def choice(data, field, options):
    value = words(data, field)
    require(value in options, f"{field} must be one of {', '.join(options)}")
    return value


def interval(data, frames):
    start, end = data.get("start_frame"), data.get("end_frame")
    require(type(start) is int and type(end) is int and 0 <= start < end <= frames,
            f"Expected half-open frame range within 0..{frames}, found {start}..{end}")
    return start, end


def viewed(regions, start, end):
    """Adjoining observation windows can jointly cover a ticket or its seams."""
    for left, right in sorted((r["start_frame"], r["end_frame"]) for r in regions):
        if left > start:
            break
        start = max(start, right)
        if start >= end:
            return True
    return False


def fingerprint(path):
    with path.open("rb") as stream:
        h = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def locked(ledger):
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.with_name(ledger.name + ".lock").open("a+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def commit(path, data):
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as out:
        staged = Path(out.name)
        try:
            json.dump(data, out, indent=2, ensure_ascii=False, allow_nan=False)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        except BaseException:
            staged.unlink(missing_ok=True)
            raise
    try:
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def media_info(path):
    output = subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
        "stream=nb_frames,avg_frame_rate,width,height", "-of", "json", str(path)
    ])
    streams = json.loads(output)["streams"]
    require(streams, "Candidate has no video stream")
    stream = streams[0]
    frames = stream.get("nb_frames")
    if not str(frames).isdigit():
        frames = subprocess.check_output([
            "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
            "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path)
        ], text=True).strip()
    fps = stream["avg_frame_rate"]
    require(int(frames) > 0 and Fraction(fps) > 0, "Unreadable frame count or frame rate")
    return int(frames), fps, [stream['width'], stream['height']]


def patch_summary(path, parent, sha, frames, fps):
    require(parent is not None, "Use --from with a local patch report")
    raw = path.read_bytes()
    report = json.loads(raw)
    require(isinstance(report, dict) and report.get("base_sha256") == parent
            and report.get("candidate_sha256") == sha, "Patch report is for a different base or candidate")
    bounds = report.get("requested_frames")
    require(isinstance(bounds, list) and len(bounds) == 2, "Patch needs requested_frames=[start,end]")
    start, end = interval(dict(start_frame=bounds[0], end_frame=bounds[1]), frames)
    if "frames" in report:
        require(type(report["frames"]) is int and report["frames"] == frames, "Patch report has a different frame count")
    if "fps" in report:
        require(Fraction(str(report["fps"])) == Fraction(fps), "Patch report has a different frame rate")
    if "verified_outside_frames" in report:
        require(report["verified_outside_frames"] == frames - (end - start), "Incorrect outside-frame count")
    proof = dict(report_sha256=hashlib.sha256(raw).hexdigest(), base_sha256=parent,
                 candidate_sha256=sha, requested_frames=bounds)
    for key in ("verified_outside_frames", "unchanged_audio_tracks", "changed_span", "requested_samples"):
        if key in report:
            proof[key] = report[key]
    if report.get("outside_frame_md5_sequence_sha256") and report.get("audio_packet_timing_sha256") and "verified_outside_frames" in report:
        proof["av_change_window"] = bounds
    elif (report.get("all_video_packets_and_timestamps_identical") is True
          and report.get("outside_decoder_guard_decoded_error", {}).get("changed_sample_frames") == 0):
        guard = report["decoder_guard_sample_range"]
        scale = Fraction(fps) / report["sample_rate"]
        proof["av_change_window"] = [math.floor(guard[0] * scale), math.ceil(guard[1] * scale)]
    return proof


def bind_editorial_scope(record, media):
    receipt = review_bundle.receipt_for(media)
    manifest = json.loads(receipt.read_text()) if record['bundle_sha256'] else {}
    scope = review_bundle.source_scope(media, manifest)
    choices = manifest.get('visual_cards', [])
    if not manifest and media.resolve().is_relative_to(Path.cwd()) and Path('edit.json').is_file():
        choices = review_bundle.frozen_cards(Path('edit.json'))
    if choices:
        require(all(0 <= c['start'] < c['end'] <= record['frames'] for c in choices),
                'Visual card selection exceeds the candidate timeline')
        record['visual_cards'] = choices
    require(all(0 <= t["start_frame"] < t["end_frame"] <= record["frames"] for t in scope["targets"]),
            "Source target exceeds the candidate timeline")
    record.update(editorial_targets=scope.pop('targets'), editorial_scope=scope)
    record['text_tracks'] = ([manifest["timeline_sha256"], manifest["sidecars"]["captions.srt"]]
                             if "timeline_sha256" in manifest else None)


def fresh_round(ledger, media, sha, bundle_sha, parent, patch):
    previous = ledger["rounds"][-1] if ledger["rounds"] else None
    retry = (previous and parent == previous["parent_sha256"] and bundle_sha == previous["bundle_sha256"]
             and (not previous["decision"] or parent != sha))
    if previous and previous["candidate_sha256"] == sha and (parent is None or retry):
        require(previous["bundle_sha256"] == bundle_sha, "Candidate companions changed; open a new review round")
        if patch:
            proof = patch_summary(patch, parent, sha, previous["frames"], previous["fps"])
            require(proof["report_sha256"] == (previous.get("patch") or {}).get("report_sha256"),
                    "Patch report differs from the registered round")
        return previous
    inherited = {}
    if previous:
        require(parent == previous["candidate_sha256"], "Start a revision with --from and the current head SHA")
        require(previous.get("decision"), "Record a decision on the current cut before opening a revision")
        inherited = outstanding(previous)
        if inherited:
            require(sha != parent or bundle_sha != previous["bundle_sha256"], "Open fixes require a changed candidate")
    else:
        require(not parent, "First round has no parent")
    frames, fps, size = media_info(media)
    proof = patch_summary(patch, parent, sha, frames, fps) if patch else None
    if proof:
        require(previous and previous["frames"] == frames and Fraction(previous["fps"]) == Fraction(fps),
                "A fixed-window edit must preserve master timing")
    require(fingerprint(media) == sha, "Candidate changed while it was being registered")
    record = dict(number=len(ledger["rounds"]) + 1, candidate_sha256=sha,
                  candidate_path=os.path.relpath(media), bundle_sha256=bundle_sha, parent_sha256=parent, created_at=stamp(),
                  frames=frames, fps=fps, frame_size=size, perception_checks=scope_for(media), patch=proof, reviews=[], inherited=inherited, checks={},
                  meeting=None, decision=None)
    bind_editorial_scope(record, media)
    if bundle_sha:
        intent_source = json.loads(review_bundle.receipt_for(media).read_text())
    elif record['editorial_scope']['origin'] == 'local_project':
        intent_source = json.loads(Path('edit.json').read_text())
    else:
        intent_source = {}
    story_review.open_round(record, previous, intent_source)
    if previous and proof:
        record["unchanged_sources"] = editorial_evidence.unchanged_sources(previous, record, proof.get("av_change_window"))
        record["unchanged_motion"] = unchanged_motion(previous, record, proof.get("av_change_window"))
    ledger["rounds"].append(record)
    return record


def submitted_issues(record):
    return {issue["id"]: issue for review in record["reviews"] for issue in review["issues"]}


def outstanding(record):
    unresolved = {key: issue for key, issue in record["inherited"].items()
                  if record["checks"].get(key, {}).get("result") != "pass"}
    # After a failed recheck on a retimed cut, its current coordinates travel with the ticket.
    for key, issue in list(unresolved.items()):
        check = record["checks"].get(key, {})
        if "start_frame" in check:
            unresolved[key] = dict(issue, start_frame=check["start_frame"], end_frame=check["end_frame"])
    if record["meeting"]:
        issues = submitted_issues(record)
        for item in record["meeting"]["tickets"]:
            if item["action"] == "fix":
                assigned = issues[item["id"]].copy()
                for field in ("implementer", "checker"):
                    if field in item:
                        assigned[field] = item[field]
                unresolved[item["id"]] = assigned
    return unresolved


def validate_observations(record, data, *, supplemental=False):
    observations = rows(data, "observations")
    for row in observations:
        words(row, "shot")
        words(row, "evidence")
        interval(row, record["frames"])
        if supplemental:
            require(any(k in row for k in ("perception", "editorial", "ending_listen")), "Supplement needs a specific review finding")
        if "perception" in row:
            validate_spot(row, record.get("perception_checks", {}), record["frames"], record.get("frame_size"))
    claimed = set(record["inherited"]) | set(submitted_issues(record))
    issues = [] if supplemental and "issues" not in data else rows(data, "issues", required=False)
    for issue in issues:
        key = words(issue, "id")
        require(key == issue["id"] and key not in claimed, f"Duplicate or padded ticket ID: {key}")
        claimed.add(key)
        for field in ("shot", "evidence", "owner", "acceptance"):
            words(issue, field)
        choice(issue, "priority", ("critical", "major", "minor"))
        start, end = interval(issue, record["frames"])
        require(viewed(observations, start, end), f"{key} is outside the reviewer's observed windows")
    tickets = submitted_issues(record)
    tickets.update({issue["id"]: issue for issue in issues})
    for row in observations:
        if "editorial" in row:
            editorial_evidence.validate(row, record, tickets)
        spot = row.get('perception', {})
        if spot.get('kind') == 'alignment' and 'issue_id' in spot:
            ticket = tickets.get(words(spot, 'issue_id'))
            require(ticket and row["start_frame"] < ticket["end_frame"] and ticket["start_frame"] < row["end_frame"],
                    'An alignment defect must have an overlapping repair ticket')


def observe(record, data):
    require(not record["meeting"], "Independent observations must arrive before discussion")
    reviewer, role = name(data, "reviewer"), name(data, "role")
    require(all(name(row, "reviewer") != reviewer and name(row, "role") != role for row in record["reviews"]),
            "Use a distinct reviewer and role for each screening")
    words(data, "viewing")
    validate_observations(record, data)
    record["reviews"].append(data)


def supplement(record, data):
    """Add candidate-bound evidence from an existing seat and reopen discussion if already held."""
    reviewer = name(data, "reviewer")
    report = next((r for r in record["reviews"] if name(r, "reviewer") == reviewer), None)
    require(report is not None, "First submit the reviewer's original observation")
    validate_observations(record, data, supplemental=True)
    if record["meeting"]:
        record.setdefault("prior_meetings", []).append(record["meeting"])
        record["meeting"] = None
    report["observations"].extend(data["observations"])
    report["issues"].extend(data.get("issues", []))


def discuss(record, data):
    require(not record["meeting"], "Discussion already recorded")
    reviewers = {name(row, "reviewer") for row in record["reviews"]}
    require(len(reviewers) >= 3, "Obtain independent screenings from at least three reviewers in distinct roles")
    words(data, "chair")
    speakers = set()
    for row in rows(data, "discussion"):
        who = name(row, "reviewer")
        require(who in reviewers or who == name(data, "chair"), "Unknown discussion participant")
        speakers.add(who)
        words(row, "point")
    require(reviewers <= speakers, "Discussion is missing a reviewer's position")
    issues = submitted_issues(record)
    covered = set()
    for row in rows(data, "tickets", required=False):
        key = words(row, "id")
        require(key in issues and key not in covered, f"Unknown or duplicate ticket disposition: {key}")
        action = choice(row, "action", ("fix", "retain"))
        words(row, "reason")
        require(action == "fix" or issues[key]["priority"] != "critical",
                f"Critical issue {key} must be fixed or held")
        if action == "fix":
            implementer = name(row, "implementer")
            if "checker" in row:
                checker = name(row, "checker")
                require(checker != implementer, "The implementer cannot verify their own repair")
                require(checker in reviewers, "Select a participating reviewer or leave checker open")
        covered.add(key)
    require(covered == set(issues), "Give every submitted issue a disposition")
    story_review.validate_meeting(record, data)
    record["meeting"] = data


def check_fix(record, data):
    key = words(data, "id")
    require(key in record["inherited"] and key not in record["checks"],
            "Each inherited ticket gets one recheck per revision")
    issue = record["inherited"][key]
    reviewer = name(data, "reviewer")
    review = next((r for r in record["reviews"] if name(r, "reviewer") == reviewer), None)
    require(review is not None, "The checker must screen this candidate")
    if "implementer" in issue:
        require(reviewer != name(issue, "implementer"), "The implementer cannot verify their own repair")
    else:
        require(name(issue, "owner") not in (reviewer, name(review, "role")), "A screened reviewer other than the fix owner checks the result")
    if "checker" in issue and name(issue, "checker") != reviewer:
        words(data, "handoff_reason")
    region = issue
    if "start_frame" in data or "end_frame" in data:
        region = data
        words(data, "remap_reason")
    start, end = interval(region, record["frames"])
    if record.get("patch"):
        require(start <= issue["start_frame"] and end >= issue["end_frame"],
                "A fixed-window revision must recheck the original ticket interval")
    require(viewed(review["observations"], start, end), "The checker must observe the ticket's entire window on this candidate")
    choice(data, "result", ("pass", "fail"))
    words(data, "evidence")
    record["checks"][key] = data


def decide(record, data, media):
    decision = choice(data, "decision", ("deliver", "revise", "hold"))
    require(record["meeting"], "Discuss the independent screenings first")
    require(name(data, "chair") == name(record["meeting"], "chair"), "Only the discussion chair signs this decision")
    words(data, "reason")
    unresolved = outstanding(record)
    if decision == "deliver":
        missing = editorial_evidence.missing(record)
        require(not missing, f"Missing candidate-level editorial evidence: {', '.join(missing)}")
        missing = missing_spots(record)
        require(not missing, f"Missing candidate-level perceptual spot checks: {', '.join(missing)}")
        require(not unresolved, f"Unresolved fixes: {', '.join(unresolved)}")
        from ending_signoff import verify_endings
        from picture_holds import check_picture_holds
        verify_endings(media, record, data)
        check_picture_holds(media, record, data)
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i", str(media),
                        "-map", "0:v:0", "-map", "0:a?", "-f", "null", "-"], check=True)
        data["decoded_to_end"] = True
        if record.get('story_intent'):
            data["story_evidence_version"] = 1
        data["editorial_evidence_version"] = 1
        data["alignment_evidence_version"] = 1
        data["motion_window_evidence_version"] = 1
        require(fingerprint(media) == record["candidate_sha256"], "Candidate changed during final decode")
    if decision == "revise":
        require(unresolved, "Revision needs a specific open ticket")
    record["decision"] = data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    subs = parser.add_subparsers(dest="command", required=True)
    for command in ("begin", "first-view", "observe", "supplement", "discuss", "check", "decide", "verify", "publish", "status"):
        sub = subs.add_parser(command)
        sub.add_argument("media", type=Path)
        if command == "first-view":
            sub.add_argument("--trial", required=True, type=Path, help="Sealed blind_review.py first-view --story trial directory")
        if command == "begin":
            sub.add_argument("--from", dest="parent", metavar="PARENT_SHA256")
            sub.add_argument("--patch", type=Path, help="Verified fixed-window edit report for this candidate")
        if command in ("observe", "supplement", "discuss", "check", "decide"):
            sub.add_argument("report", type=Path, help="JSON object bound to candidate_sha256")
        if command == "publish":
            sub.add_argument("--output", type=Path, default=Path("out/final.mp4"))
    args = parser.parse_args()
    with locked(args.ledger):
        ledger = json.loads(args.ledger.read_text()) if args.ledger.exists() else {"schema_version": 1, "rounds": []}
        require(ledger.get("schema_version") == 1, "Unsupported editorial ledger version")
        args.media = review_bundle.released_snapshot(args.media)
        sha = fingerprint(args.media)
        bundle_sha = review_bundle.fingerprint(args.media)
        if args.command == "begin":
            record = fresh_round(ledger, args.media, sha, bundle_sha, args.parent, args.patch)
        else:
            require(ledger["rounds"], "Register this candidate with begin")
            record = ledger["rounds"][-1]
            require(record["candidate_sha256"] == sha, "Media differs from the current reviewed candidate")
            require(record["bundle_sha256"] == bundle_sha, "Candidate companions differ from the current reviewed bundle")
        if not record["decision"] and "editorial_scope" not in record:
            bind_editorial_scope(record, args.media)
        if not record["decision"] and "frame_size" not in record:
            # A review opened by an older installation gains the same layout inventory before sign-off.
            _, _, record["frame_size"] = media_info(args.media)
            scope = scope_for(args.media)
            if "alignment" in scope:
                record.setdefault("perception_checks", {})["alignment"] = scope["alignment"]
        if args.command == 'first-view':
            story_review.record_trial(record, ledger['rounds'][:-1], args.trial)
        if hasattr(args, "report"):
            require(not record["decision"], "Decision already sealed; begin a revision")
            data = json.loads(args.report.read_text())
            require(isinstance(data, dict) and data.get("candidate_sha256") == sha, "Report is for a different cut")
            reused = any(r["candidate_sha256"] == sha for r in ledger["rounds"][:-1])
            if reused or "round" in data:
                require(data.get("round") == record["number"], "For reused media, bind each fresh report to the current round number")
            if "bundle_sha256" in data:
                require(data["bundle_sha256"] == bundle_sha, "Report is for different subtitles or covers")
            data.update(round=record["number"], bundle_sha256=bundle_sha)
            data["recorded_at"] = stamp()
            if args.command == "decide":
                decide(record, data, args.media)
            else:
                {"observe": observe, "supplement": supplement, "discuss": discuss, "check": check_fix}[args.command](record, data)
        legacy_motion = args.command == "verify" and not (record["decision"] or {}).get("motion_window_evidence_version")
        if args.command in ("publish", "verify"):
            decision = record["decision"] or {}
            require(decision.get("decision") == "deliver" and decision.get("decoded_to_end")
                    and decision.get("candidate_sha256") == sha and not outstanding(record)
                    and not missing_spots(record, legacy_motion=legacy_motion),
                    "Current cut has no deliver decision for these bytes")
            if args.command == "publish":
                require(decision.get("editorial_evidence_version") == 1 and not editorial_evidence.missing(record),
                        "Legacy sign-off: verify can confirm history. Begin a new review before publication")
                require(decision.get("alignment_evidence_version") == 1 or "alignment" not in scope_for(args.media),
                        "Historical sign-off has no layout review. Verify the old release or begin a new review for publication.")
                require(args.output.resolve() != args.ledger.resolve(), "Output must differ from ledger")
                review_bundle.promote(args.media, args.output, sha, record["bundle_sha256"])
            elif decision.get("editorial_evidence_version"):
                require(not editorial_evidence.missing(record), "Signed cut is missing its editorial findings")
        if args.command in ("begin", "first-view") or hasattr(args, "report"):
            require(review_bundle.fingerprint(args.media) == record["bundle_sha256"], "Bundle changed during review")
            commit(args.ledger, ledger)
            candidate = args.media.resolve()
            if (args.command == "begin" and bundle_sha and args.ledger.resolve() == DEFAULT_LEDGER.resolve()
                    and candidate.name == "candidate.mp4" and candidate.parent.parent == Path("out/review").resolve()):
                review_bundle.set_latest(candidate)
            if args.command == "decide":
                render_minutes(ledger, args.ledger.with_name("minutes.md"))
        print(json.dumps(dict(round=record["number"], sha256=sha, frames=record["frames"],
                              reviewers=len(record["reviews"]), discussion=bool(record["meeting"]),
                              open_fixes=list(outstanding(record)), missing_perception=missing_spots(record, legacy_motion=legacy_motion),
                              missing_editorial=editorial_evidence.missing(record), story_intent=record.get("story_intent"),
                              first_viewers=list(story_review.eligible(record)), source_targets=record.get("editorial_targets", []),
                              editorial_scope=record.get("editorial_scope", {"origin":"historical"}),
                              carried_sources=len(record.get("unchanged_sources", [])),
                              carried_motion_windows=[item["window"] for item in record.get("unchanged_motion", [])],
                              decision=(record["decision"] or {}).get("decision", "pending")),
                         ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error))
