"""Record a viewer's first observation of a cut or compare two anonymous edits."""
import argparse
import base64
import hashlib
import html
import json
import math
import secrets
import shutil
import tempfile
from pathlib import Path

import library


# Keep the producer's intended answer and genre outside the public viewing task.
STORY_QUESTIONS = {
    'main_point': '转述给一个没看过的人，你觉得这条片主要在讲什么？',
    'viewer_value': '看完后你多明白了什么，或者留下了怎样的感受？如果没有，也请直说。',
    'attention': '哪个时刻让你想继续看、走了神或想划走？是什么让你这样觉得？',
}


def validate_story_response(task, response):
    if task.get('story_version') != 1:
        return
    for key in STORY_QUESTIONS:
        if not isinstance(response.get(key), str) or not response[key].strip():
            raise ValueError('Preserve the viewer’s own answer to ' + key + ', including uncertainty')
    mode = response.get('playback')
    if mode not in ('continuous', 'sampled', 'partial'):
        raise ValueError('State the actual viewing mode: continuous (whole film at 1x), sampled or partial')
    ranges = response.get('played_seconds')
    if mode == 'continuous' and ranges is not None:
        tolerance = min(.15, task['seconds'] * .02)
        covered = 0
        for start, end in ranges:
            if not (math.isfinite(start) and math.isfinite(end) and 0 <= start <= covered + tolerance and start < end):
                raise ValueError('Recorded playback has gaps; mark partial or watch the entire candidate')
            covered = max(covered, end)
        if covered < task['seconds'] - tolerance:
            raise ValueError('Recorded playback ended early; mark partial or watch the entire candidate')


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def prepare(folder, base, candidate, question):
    choices = [('baseline', base), ('candidate', candidate)]
    if secrets.randbelow(2):
        choices.reverse()
    stage_task(folder, dict(zip(('A', 'B'), choices)), question)


def prepare_first(folder, media, question, device_page=False, *, story=False):
    stage_task(folder, {'film': ('candidate', media)}, question, device_page, story=story)


def stage_task(folder, choices, question, device_page=False, *, story=False):
    if not question.strip() or folder.exists():
        raise ValueError('Give a viewing task and choose a new trial directory')
    folder.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=folder.parent) as temp:
        stage = Path(temp) / 'trial'
        public = stage / 'viewer'
        public.mkdir(parents=True)
        identity, files = {}, {}
        for label, (name, path) in choices.items():
            shutil.copyfile(path, public / (label + '.mp4'))
            files[label] = library.digest(public / (label + '.mp4'))
            identity[label] = name
        if len(set(files.values())) != len(files):
            raise ValueError('Choose two different versions')
        task = dict(question=question, files=files)
        if 'film' in choices:
            duration = library.duration(public / 'film.mp4')
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError('First-view media needs a finite duration')
            task.update(mode='first_view', seconds=duration)
            if story:
                task['story_version'] = 1
                task['story_questions'] = STORY_QUESTIONS
            if device_page:
                write_device_page(public, task)
        else:
            task['response_version'] = 2
            template = Path(__file__).with_name('blind-player.html').read_text(encoding='utf-8')
            library.atomic(public / 'index.html', template.replace('<!--QUESTION-->', html.escape(question)))
        library.atomic(public / 'task.json', library.json_text(task))
        library.atomic(stage / 'key.json', library.json_text(identity))
        stage.rename(folder)


def write_device_page(public, task):
    template = Path(__file__).with_name('first-view-device.html').read_text(encoding='utf-8')
    template = template.replace('<!--QUESTION-->', html.escape(task['question']))
    template = template.replace('<!--DIGEST-->', task['files']['film'])
    template = template.replace('<!--TASK-->', hashlib.sha256(library.json_text(task).encode()).hexdigest())
    fields = ''
    if 'story_questions' in task:
        fields = ''.join(f'<label>{html.escape(prompt)}<textarea required id="{key}"></textarea></label>'
                         for key, prompt in task['story_questions'].items())
        fields += ('<label>实际观看方式<select id="playback" required><option value="">请选择</option>'
                   '<option value="continuous">原速连续看完全片</option><option value="sampled">抽帧或跳看</option>'
                   '<option value="partial">只看了部分或使用变速</option></select></label>')
    template = template.replace('<!--STORY-FIELDS-->', fields)
    before, after = template.split('<!--FILM-->')
    with (public / 'device.html').open('wb') as page, (public / 'film.mp4').open('rb') as media:
        page.write(before.encode())
        for chunk in iter(lambda: media.read(3 * 1024 * 1024), b''):
            page.write(base64.b64encode(chunk))
        page.write(after.encode())


def record(folder, response):
    task = load(folder / 'viewer/task.json')
    task_hash = library.digest(folder / 'viewer/task.json')
    if 'task_sha256' in response and response['task_sha256'] != task_hash:
        raise ValueError('This observation belongs to another viewing question')
    if response.get('files') != task['files']:
        raise ValueError('Report the file digests from the viewed task')
    for label, sha in task['files'].items():
        if library.digest(folder / 'viewer' / (label + '.mp4')) != sha:
            raise ValueError(f'Viewed file {label} changed')
    first = task.get('mode') == 'first_view'
    fields = ('observation',) if first else ('observed_a', 'observed_b', 'basis')
    for field in ('reviewer', 'method', 'limits', *fields):
        if not isinstance(response.get(field), str) or not response[field].strip():
            raise ValueError('Missing observation: ' + field)
    if first:
        validate_story_response(task, response)
        if type(response.get('unbriefed')) is not bool:
            raise ValueError('State whether the viewer saw the script, earlier cut, or intended answer beforehand')
        if response.get('status') not in ('located', 'uncertain', 'missed'):
            raise ValueError('Record located, uncertain, or missed')
        if response['status'] == 'located' and (not isinstance(response.get('location'), str)
                                                or not response['location'].strip()):
            raise ValueError('Describe the picture or sound you are referring to')
        when = response.get('at_seconds')
        if when is not None and (type(when) not in (float, int) or not 0 <= when < task['seconds']):
            raise ValueError('Use a timestamp inside the viewed cut')
    elif response.get('preference') not in ('A', 'B', 'tie', 'undetermined'):
        raise ValueError('Choose A, B, tie or undetermined')
    if first or task.get('response_version') == 2:
        response = dict(response, task_sha256=task_hash)
    name = response['reviewer'].strip().casefold()
    answers = folder / 'responses'
    with library.exclusive(answers):
        path = answers / (hashlib.sha256(name.encode()).hexdigest() + '.json')
        if path.exists() and load(path) != response:
            raise ValueError('Keep the original independent observation; run a new trial to repeat it')
        library.atomic(path, library.json_text(response))
    return path


def result(folder):
    task = load(folder / 'viewer/task.json')
    labels = load(folder / 'key.json')
    first = task.get('mode') == 'first_view'
    if (first and labels != {'film': 'candidate'}) or (not first and
            (set(labels) != {'A', 'B'} or set(labels.values()) != {'baseline', 'candidate'})):
        raise ValueError('Invalid blind comparison key')
    for label in labels:
        if library.digest(folder / 'viewer' / (label + '.mp4')) != task['files'][label]:
            raise ValueError('The viewed media changed after preparation')
    votes = []
    for path in sorted((folder / 'responses').glob('*.json')):
        row = load(path)
        binds_task = first or task.get('response_version') == 2 or 'task_sha256' in row
        if row['files'] != task['files'] or (binds_task and row.get('task_sha256') != library.digest(folder / 'viewer/task.json')):
            raise ValueError('An observation belongs to another viewing task')
        if first:
            validate_story_response(task, row)
        votes.append(row if first else dict(row, version=labels.get(row['preference'], row['preference'])))
    if first:
        return dict(mode='first_view', question=task['question'], media_sha256=task['files']['film'],
                    responses=votes, **({'story_version': 1} if task.get('story_version') == 1 else {}))
    return dict(question=task['question'], versions={labels[k]: v for k, v in task['files'].items()},
                responses=votes)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(required=True, dest='action')
    pre = sub.add_parser('prepare')
    pre.add_argument('baseline', type=Path)
    pre.add_argument('candidate', type=Path)
    pre.add_argument('--question', required=True)
    first = sub.add_parser('first-view', help='Show one cut without revealing the intended answer')
    first.add_argument('media', type=Path)
    first.add_argument('--story', action='store_true', help='Ask neutral questions about the whole film, viewer value and attention')
    first.add_argument('--device-page', action='store_true', help='Add a single portable offline viewing page with embedded MP4 and response export')
    first.add_argument('--question', default='片中有什么让你留意？请用自己的话说说。记得的话，可以指出是哪处画面或哪段声音。')
    rec = sub.add_parser('record')
    rec.add_argument('response', type=Path)
    show = sub.add_parser('result')
    for cmd in (pre, first, rec, show):
        cmd.add_argument('--folder', type=Path, required=True)
    args = p.parse_args()
    if args.action == 'prepare':
        prepare(args.folder, args.baseline, args.candidate, args.question)
        print(args.folder / 'viewer')
    elif args.action == 'first-view':
        prepare_first(args.folder, args.media, args.question, args.device_page, story=args.story)
        print(args.folder / 'viewer')
    elif args.action == 'record':
        print(record(args.folder, load(args.response)))
    else:
        print(library.json_text(result(args.folder)))


if __name__ == '__main__':
    main()
