"""Render human-readable editorial decisions directly from the recorded screenings."""
import argparse
from fractions import Fraction
import json
import os
from pathlib import Path
from perception_review import describe_spot
from editorial_evidence import describe as describe_editorial
import tempfile


def time_window(item, fps):
    a, b = item['start_frame'], item['end_frame']
    return f"{a / fps:.2f}–{b / fps:.2f} 秒（第 {a} 至 {b - 1} 帧）"


def render_minutes(ledger, path):
    decisions = {'deliver': '通过，可交付', 'revise': '退回定点修改', 'hold': '暂缓交付'}
    paragraphs = ['# 团队审片记录']
    for record in ledger['rounds']:
        decision = record.get('decision') or {}
        summary = decisions.get(decision.get('decision'), '正在审片')
        paragraphs.extend([f"## 第 {record['number']} 轮 · {summary}",
                           f"审片版本 `{record['candidate_sha256']}`。"])
        fps = float(Fraction(record['fps']))
        for kept in record.get('unchanged_sources', []):
            row = kept['observation']
            paragraphs.append(f"沿用第 {kept['round']} 轮 {kept['reviewer']} 对 {row['shot']} 的逐句核对。"
                              f"{time_window(row, fps)} 的声画与字幕已验证相同。")
        for kept in record.get('unchanged_motion', []):
            start, end = kept['window']
            paragraphs.append(f"第 {start} 至 {end - 1} 帧的画面已验证相同，沿用第 {kept['round']} 轮"
                              f"{kept['reviewer']} 从第 {kept['trace_start']} 帧起的连续动作测量。")
        for report in record['reviews']:
            paragraphs.extend([f"**{report['role']} · {report['reviewer']}**", report['viewing']])
            for item in report['observations']:
                paragraphs.append(f"- {item['shot']}，{time_window(item, fps)}。{item['evidence']}")
                if 'perception' in item:
                    paragraphs.append(describe_spot(item))
                if 'editorial' in item:
                    paragraphs.append(describe_editorial(item))
            for issue in report['issues']:
                paragraphs.append(f"**修改票 {issue['id']}**　{issue['shot']}，{time_window(issue, fps)}。"
                                  f"{issue['evidence']} 负责人 {issue['owner']}。验收条件为{issue['acceptance']}")
        if record.get('story_intent'):
            intent = record['story_intent']
            paragraphs.append(f"本片希望观众明白「{intent['main_point']}」，看完能带走「{intent['viewer_value']}」。")
            for entry in record.get('first_views', {}).values():
                response = entry['response']
                paragraphs.append(f"**{response['reviewer']} 的原话**　主旨「{response['main_point']}」。"
                                  f"收获或感受「{response['viewer_value']}」。注意力「{response['attention']}」。"
                                  f"观看方式 {response['method']}，限制 {response['limits']}。")
        meeting = record.get('meeting')
        if meeting:
            paragraphs.append('### 会审')
            for speaker in meeting['discussion']:
                paragraphs.append(f"- **{speaker['reviewer']}**　{speaker['point']}")
            grades = {'matched': '吻合', 'partial': '部分吻合', 'missed': '未传达'}
            for finding in meeting.get('story_readings', []):
                attention = '保留' if finding['attention'] == 'keep' else '返修'
                paragraphs.append(f"主编对照 {finding['reviewer']} 的首看：主旨{grades[finding['main_point']]}，"
                                  f"观众收获{grades[finding['viewer_value']]}，吸引力判断为{attention}。{finding['basis']}")
            for row in meeting['tickets']:
                disposition = '安排修改' if row['action'] == 'fix' else '保留现状'
                paragraphs.append(f"**{row['id']} · {disposition}**　{row['reason']}")
                if row.get('implementer'):
                    assignment = f"执行人 {row['implementer']}。"
                    if row.get('checker'):
                        assignment += f"预派复核人 {row['checker']}。"
                    paragraphs.append(assignment)
        for key, check in record['checks'].items():
            outcome = '通过' if check['result'] == 'pass' else '仍需修改'
            paragraphs.append(f"**复审 {key} · {outcome}**　{check['reviewer']}。{check['evidence']}")
            issue = record.get('inherited', {}).get(key, {})
            if issue.get('checker') and check.get('handoff_reason'):
                paragraphs.append(f"复核由 {issue['checker']} 转交 {check['reviewer']}。{check['handoff_reason']}")
        if decision:
            for note in decision.get('picture_holds', []):
                paragraphs.append(f"低变化画面 {note['target']}。观众在这里辨认{note['focus']}。{note['reason']}")
            for listen in decision.get('ending_checks', {}).get('listened', []):
                paragraphs.append(f"第 {listen['sentence']} 句由{listen['reviewer']}复听，辨出末尾“{listen['heard_tail']}”。{listen['listening_method']}")
            paragraphs.append(f"**主编 {decision['chair']} · {summary}**　{decision['reason']}")
    path = Path(path)
    with tempfile.NamedTemporaryFile('w', dir=path.parent, encoding='utf8', delete=False) as stream:
        tmp = Path(stream.name)
        stream.write('\n\n'.join(paragraphs) + '\n')
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ledger', type=Path)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    render_minutes(json.loads(args.ledger.read_text()), args.out or args.ledger.with_name('minutes.md'))
