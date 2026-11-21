"""Resolve flagged endings against the actual candidate before delivery."""
import json
from pathlib import Path
from review_bundle import digest


def verify_endings(media, record, decision):
    folder = media.parent
    timeline_file = folder / 'timeline.json'
    if not timeline_file.is_file():
        return
    timeline = json.loads(timeline_file.read_text())
    if not any('ending_check' in take for take in timeline.get('voice_takes', [])):
        return  # Existing archived timelines predate sentence-ending checks.
    report_path = Path(decision.get('ending_report', folder / 'qa/content-check.json'))
    if not report_path.is_file():
        raise ValueError('Run review.py on this candidate before delivery, or supply decision.ending_report')
    report = json.loads(report_path.read_text())
    if (report.get('media_sha256') != record['candidate_sha256']
            or report.get('timeline_sha256') != digest(timeline_file)
            or report.get('captions_sha256') != digest(folder / 'captions.srt')):
        raise ValueError('Ending report differs from candidate, timeline or captions; rerun review.py')
    rows = report.get('voice_endings')
    if not isinstance(rows, list) or not rows or len(rows) != len(timeline['voice_takes']):
        raise ValueError('Review every prepared sentence in the actual mix before delivery')
    suspects = [(i, row) for i, row in enumerate(rows, 1)
                if 'possible_missing_ending' in (row['source_status'], row['status'])]
    accepted = []
    for i, row in suspects:
        found = False
        for seat in record['reviews']:
            for item in seat['observations']:
                listen = item.get('ending_listen', {})
                if (listen.get('sentence') == i and listen.get('result') == 'complete'
                    and item['start_frame'] / timeline['fps'] <= row['start'] + .001
                    and item['end_frame'] / timeline['fps'] >= row['end'] - .001
                    and all(isinstance(listen.get(k), str) and listen[k].strip()
                            for k in ('heard_tail', 'listening_method'))):
                    accepted.append(dict(sentence=i, reviewer=seat['reviewer'], **{
                        k: listen[k] for k in ('heard_tail', 'listening_method')}))
                    found = True
                    break
            if found:
                break
        if not found:
            raise ValueError(f"Sentence {i} at {row['start']:.2f}s may end early: {row['spoken']}. "
                             "Re-record it, or have a reviewer listen to the complete mixed line and record ending_listen.")
    decision['ending_checks'] = dict(sentences=len(rows), report_sha256=digest(report_path),
                                    no_asr=sum(r['status']=='asr_unavailable' for r in rows),
                                    listened=accepted)
