"""Maintain a local, portable media library and check out attributable assets into projects."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import quote, urlsplit

import library_search

CATEGORIES = {
    'capture':'自有实拍与产品录屏', 'evidence':'图片、档案与证据', 'explain':'解释动效与图表模板',
    'foley':'动作拟音与提示音', 'ambience':'环境声', 'music':'BGM 与可剪乐段',
    'continuity':'基础转场与镜头衔接', 'brand':'品牌与平面包装', 'voice':'旁白与发音',
    'texture':'纹理、叠加与背景', 'effects':'风格化转场与特效', 'illustration':'图标、插画、地图与 3D',
    'broll':'专题 B-roll 与空镜', 'grade':'LUT 与调色', 'stings':'音乐短标与进阶声音',
    'sequence':'完整段落与工程范例', 'reference':'优秀作品研究',
}
SCOPE = {'production':'可用于制作', 'editorial':'限相关报道使用', 'reference':'仅供研习'}
VISUAL_TYPES = {'motion': '动效', 'style': '视觉风格', 'light': '用光'}
KINDS = {'video','audio','image','document','template','data'}
MIME_KIND = {'.mp4':'video','.webm':'video','.mov':'video','.m4a':'audio','.mp3':'audio',
             '.wav':'audio','.png':'image','.jpg':'image','.jpeg':'image','.webp':'image','.svg':'image'}
INFO = ('title','category','kind','tags','use','source','license','scope','author','editorial')


def digest(path):
    with path.open('rb') as source:
        h=hashlib.sha256()
        for chunk in iter(lambda:source.read(1024*1024),b''):
            h.update(chunk)
    return h.hexdigest()


@contextmanager
def exclusive(directory):
    directory.mkdir(parents=True,exist_ok=True)
    with (directory/'.library.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        yield


def atomic(path, contents):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        temp=Path(tmp.name)
        try:
            tmp.write(contents.encode('utf-8'))
            tmp.flush()
            os.fsync(tmp.fileno())
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    try:
        os.chmod(temp,0o644)
        os.replace(temp,path)
    finally:
        temp.unlink(missing_ok=True)


def json_text(data):
    return json.dumps(data,ensure_ascii=False,indent=2)+'\n'


def load(root):
    index=root/'catalog.json'
    if not index.exists():
        return {}
    db=json.loads(index.read_text())
    if db.get('version')!=1:
        raise ValueError('Unknown catalog version')
    return db['items']


def local(root, relative):
    path=(root/relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        raise ValueError(f'Path leaves library: {relative}')
    return path


def ffmpeg(args):
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-y',*map(str,args)],check=True)


def duration(path):
    return float(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','csv=p=0',path],text=True).strip())


def check_visual(visual, companions):
    if not isinstance(visual, dict) or not isinstance(visual.get('type'), str) or visual['type'] not in VISUAL_TYPES:
        raise ValueError('visual.type must be motion, style or light')
    if not isinstance(visual.get('prompt'), str) or not visual['prompt'].strip():
        raise ValueError('A visual card needs a nonempty prompt')
    terms = visual.get('terms', [])
    if not isinstance(terms, list) or not all(isinstance(t, str) and t.strip() for t in terms):
        raise ValueError('visual.terms must be a list of search terms')
    samples = visual.get('samples', {})
    if not isinstance(samples, dict):
        raise ValueError('visual.samples must map view names to companion labels')
    for name, label in samples.items():
        paths = [p for part, p in companions if part == label]
        if not name.strip() or len(paths) != 1 or MIME_KIND.get(paths[0].suffix.lower()) not in ('image', 'video'):
            raise ValueError(f'Visual sample {name!r} must name one image or video companion in this item: {label!r}')
    sources = visual.get('sources', [])
    if not isinstance(sources, list):
        raise ValueError('visual.sources must be a list of original references')
    for source in sources:
        if not isinstance(source, dict) or not all(isinstance(source.get(k), str) and source[k].strip()
                                                  for k in ('title', 'url', 'role')):
            raise ValueError('Each visual source needs a title, URL and its contribution to this card')
        url = urlsplit(source['url'])
        if url.scheme not in ('https', 'http') or not url.hostname:
            raise ValueError('A visual source must link to an HTTP(S) page')
    for field in ('parents', 'related'):
        links = visual.get(field, [])
        if not isinstance(links, list):
            raise ValueError(f'visual.{field} must be a list of card IDs or {{id, role}} records')
        for link in links:
            card_id = link.get('id') if isinstance(link, dict) else link
            if not isinstance(card_id, str) or not re.fullmatch('[a-z0-9][a-z0-9-]{0,79}', card_id):
                raise ValueError(f'Invalid visual card ID in {field}: {card_id!r}')
            if isinstance(link, dict) and not isinstance(link.get('role', ''), str):
                raise ValueError(f'visual.{field} role must be text')


def read_spec(row, parent):
    id_=row['id']
    if not re.fullmatch('[a-z0-9][a-z0-9-]{0,79}',id_):
        raise ValueError(f'Invalid ID {id_}')
    meta={key:row[key] for key in INFO}
    if meta['category'] not in CATEGORIES or meta['scope'] not in SCOPE or meta['kind'] not in KINDS:
        raise ValueError(f'Unknown category, scope or kind for {id_}')
    if meta['category']=='reference' and meta['scope']!='reference':
        raise ValueError('Research citations use reference scope')
    if not isinstance(meta['tags'],list) or not meta['tags'] or not all(isinstance(t,str) and t.strip() for t in meta['tags']):
        raise ValueError(f'Missing tags for {id_}')
    for key in ('title','use','source','license','author'):
        if not isinstance(meta[key],str) or not meta[key].strip():
            raise ValueError(f'Missing {key} for {id_}')
    if not isinstance(meta['editorial'],dict):
        raise ValueError('editorial must be an object')
    files=[('主文件',(parent/row['file']).resolve())]
    for extra in row.get('companions',[]):
        files.append((extra['label'],(parent/extra['file']).resolve()))
    if 'visual' in meta['editorial']:
        check_visual(meta['editorial']['visual'], files[1:])
    names=[p.name for label,p in files]
    if len(names)!=len(set(names)) or any(not p.is_file() for label,p in files):
        raise ValueError(f'Missing source or duplicate basename in {id_}')
    from attachment_credits import validate as validate_credits
    validate_credits(meta['editorial'], names)
    hashes=[digest(p) for label,p in files]
    if row.get('expected_sha256') and row['expected_sha256']!=hashes[0]:
        raise ValueError(f'Original differs from recorded checksum for {id_}')
    preview=(parent/row['preview']).resolve() if row.get('preview') else None
    key=json.dumps([meta,[(label,p.name,h) for (label,p),h in zip(files,hashes)],digest(preview) if preview else None],ensure_ascii=False,sort_keys=True)
    return id_,meta,files,hashes,preview,hashlib.sha256(key.encode()).hexdigest()


def check_times(meta,seconds):
    edit=meta['editorial']
    def check_number(x):
        return isinstance(x,(float,int)) and not isinstance(x,bool) and math.isfinite(x)
    if not check_number(seconds) or seconds<=0:
        raise ValueError('Invalid media duration')
    lo,hi=edit.get('in',0),edit.get('out',seconds)
    if not (check_number(lo) and check_number(hi) and 0<=lo<hi<=seconds+.05):
        raise ValueError(f'Unusable source interval {lo}..{hi} / {seconds}')
    for k in ('loop','tail'):
        if k in edit:
            span=edit[k]
            if not isinstance(span,list) or len(span)!=2 or not all(check_number(t) for t in span) or not 0<=span[0]<span[1]<=seconds+.05:
                raise ValueError(f'Invalid {k} interval')
    if 'cue' in edit and not (check_number(edit['cue']) and lo<=edit['cue']<=hi):
        raise ValueError('Action cue falls outside usable interval')
    edit.setdefault('in',0)
    edit.setdefault('out',round(seconds,6))


def create_preview(folder, source, meta, supplied):
    if supplied:
        kind=MIME_KIND.get(supplied.suffix.lower())
        if not kind:
            raise ValueError('Supply a browser preview as MP4, MP3, WAV, JPEG, PNG, SVG or WebP')
        dest=folder/('preview'+supplied.suffix.lower())
        shutil.copyfile(supplied,dest)
    elif meta['kind'] in ('video','audio'):
        edit=meta['editorial']
        start=edit['in']
        span=min(6.,edit['out']-start)
        if meta['kind']=='video':
            dest=folder/'preview.mp4'
            ffmpeg(['-ss',start,'-i',source,'-t',span,'-map','0:v:0','-map','0:a:0?','-vf','fps=20,scale=480:270:force_original_aspect_ratio=decrease,pad=480:270:(ow-iw)/2:(oh-ih)/2','-c:v','libx264','-pix_fmt','yuv420p','-crf',25,'-preset','veryfast','-c:a','aac','-b:a','96k','-movflags','+faststart',dest])
        else:
            dest=folder/'preview.mp3'
            ffmpeg(['-ss',start,'-i',source,'-t',span,'-vn','-c:a','libmp3lame','-q:a','4',dest])
    elif meta['kind']=='image' and source.suffix.lower() in MIME_KIND:
        if source.suffix.lower()=='.svg':
            dest=folder/'preview.svg'
            shutil.copyfile(source,dest)
        else:
            dest=folder/'preview.png'
            ffmpeg(['-i',source,'-frames:v','1','-vf','scale=540:330:force_original_aspect_ratio=decrease',dest])
    else:
        raise ValueError('Supply a preview for documents, data, or templates')
    if not dest.stat().st_size:
        raise ValueError('Empty preview')
    return dest


def register(root, rows, parent, replace):
    with exclusive(root):
        items=load(root)
        for row in rows:
            id_,meta,inputs,hashes,provided,rev=read_spec(row,parent)
            prev=items.get(id_)
            aliases=row.get('aliases',prev.get('aliases',[]) if prev else [])
            if not isinstance(aliases,list) or not all(isinstance(term,str) and term.strip() for term in aliases):
                raise ValueError(f'Invalid search aliases in {id_}')
            aliases=list(dict.fromkeys(term.strip() for term in aliases))
            if prev and prev['revision']==rev:
                # Discovery terms do not change footage or an existing project's pinned revision.
                if aliases or 'aliases' in prev:
                    prev['aliases']=aliases
                continue
            if prev and not replace:
                raise ValueError(f'ID already has different content: {id_}. Use a new id or --replace.')
            base=local(root,f'items/{id_}')
            base.mkdir(parents=True,exist_ok=True)
            dest=base/rev
            source=inputs[0][1]
            if meta['kind'] in ('video','audio'):
                secs=duration(source)
                check_times(meta,secs)
                meta['seconds']=secs
            with tempfile.TemporaryDirectory(dir=base) as tmp:
                staging=Path(tmp)/'version'
                (staging/'files').mkdir(parents=True)
                files=[]
                for (label,p),h in zip(inputs,hashes):
                    asset=staging/'files'/p.name
                    shutil.copyfile(p,asset)
                    if digest(asset)!=h:
                        raise ValueError(f'Source changed while registering {p.name}')
                    files.append({'label':label,'path':str((dest/'files'/p.name).relative_to(root)),'sha256':h})
                preview=create_preview(staging,staging/'files'/source.name,meta,provided)
                record=dict(id=id_,revision=rev,**meta,files=files,
                            preview=str((dest/preview.name).relative_to(root)),preview_sha256=digest(preview))
                if not dest.exists():
                    os.rename(staging,dest)
                else:
                    for asset in [*files,dict(path=record['preview'],sha256=record['preview_sha256'])]:
                        if digest(local(root,asset['path']))!=asset['sha256']:
                            raise ValueError(f'Existing version differs: {id_}')
            if aliases:
                record['aliases']=aliases
            items[id_]=record
        save(root,items)


def claims(root, item):
    return [(local(root,f['path']), f['sha256']) for f in item['files']]+[(local(root,item['preview']),item['preview_sha256'])]


def use(root, item, project, purpose):
    if item['scope']=='reference' or (item['scope']=='editorial' and purpose!='editorial'):
        raise ValueError(f"{item['id']} has scope {item['scope']}. Review its license and choose an appropriate asset/purpose.")
    parent=project/'assets'/'library'
    with exclusive(parent):
        target=parent/item['id']
        links=[]
        with tempfile.TemporaryDirectory(dir=parent) as tmp:
            staging=Path(tmp)/'asset'
            staging.mkdir()
            for f in item['files']:
                source=local(root,f['path'])
                dest=target/source.name
                if target.exists():
                    if not dest.exists() or digest(dest)!=f['sha256']:
                        raise ValueError(f'Project copy has changed: {dest}. Restore or keep it separately.')
                else:
                    shutil.copyfile(source,staging/source.name)
                    if digest(staging/source.name)!=f['sha256']:
                        raise ValueError(f'Library file failed integrity check: {source}')
                links.append(dict(label=f['label'],path=str(dest.relative_to(project)),sha256=f['sha256']))
            log=project/'library-uses.json'
            uses=json.loads(log.read_text()) if log.exists() else {}
            if item['id'] in uses and uses[item['id']]['revision']!=item['revision']:
                raise ValueError('Project already uses another revision. Reconcile that item explicitly.')
            uses[item['id']]={k:item[k] for k in ('revision','title','source','license','author','scope','editorial')}
            uses[item['id']].update(files=links,purpose=purpose)
            if not target.exists():
                os.rename(staging,target)
            atomic(log,json_text(uses))
        return project/links[0]['path']


def search_keywords(item):
    visual = item['editorial'].get('visual', {})
    return [*item['tags'], *item.get('aliases', []), *item.get('learned_aliases', []),
            *visual.get('terms', [])]


def remember_query(root, asset, revision, query):
    # Index updates share the registration lock. A new media revision must earn
    # its own learned terms, and re-registering an old input manifest is harmless.
    with exclusive(root):
        items = load(root)
        item = items[asset]
        if item['revision'] != revision:
            raise ValueError('The selected card changed; inspect the current preview before teaching this query')
        if query not in item.get('learned_aliases', []) and query not in item.get('aliases', []):
            item.setdefault('learned_aliases', []).append(query)
        save(root, items)


def search_text(item):
    words = [item['id'], item['title'], item['use'], item['category'], CATEGORIES[item['category']],
             item['author'], item['source'], *search_keywords(item)]
    # A source title, content synopsis or timecoded editing note can identify a useful
    # document even when its asset title describes something else.
    words.extend(value for value in item['editorial'].values() if isinstance(value, str))
    words.extend(file['label'] for file in item['files'][1:])
    visual = item['editorial'].get('visual')
    if visual:
        words.extend([visual['type'], VISUAL_TYPES[visual['type']], visual['prompt'], *visual.get('terms', [])])
        for field in ('parents', 'related'):
            words.extend(link_description(link) for link in visual.get(field, []))
        for source in visual.get('sources', []):
            words.extend((source['title'], source['role']))
    return ' '.join(words).casefold()


def link_description(link):
    if isinstance(link, str):
        return link
    return ' · '.join(filter(None, [link['id'], link.get('role')]))


def matches(item, terms, content_root=None):
    metadata = search_text(item)
    missing = [term.casefold() for term in terms if term.casefold() not in metadata]
    if missing and content_root is not None:
        for file in item['files']:
            if Path(file['path']).suffix.lower() not in ('.md', '.txt', '.srt', '.vtt'):
                continue
            body = local(content_root, file['path']).read_text(encoding='utf-8-sig', errors='replace').casefold()
            missing = [term for term in missing if term not in body]
            if not missing:
                break
    return not missing


def search_record(item, content_root=None, extra_text=''):
    text = search_text(item)
    if content_root is not None:
        for file in item['files']:
            if Path(file['path']).suffix.lower() in ('.md', '.txt', '.srt', '.vtt'):
                text += ' ' + local(content_root, file['path']).read_text(
                    encoding='utf-8-sig', errors='replace')
    return dict(id=item['id'], title=item['title'],
                keywords=' '.join(search_keywords(item)),
                use=item['use'], text=text + ' ' + extra_text)


def search_items(items, terms, content_root=None, extra_text=None):
    records = [search_record(item, content_root, (extra_text or {}).get(item['id'], ''))
               for item in items.values()]
    ranking = library_search.rank(records, terms)
    return [items[id_] for id_ in ranking['ids']]


def research_applications(item, items):
    """Resolve a study's examples or its own method attachments for offline use."""
    entries = item['editorial'].get('applications', [])
    if not isinstance(entries, list):
        raise ValueError(f"{item['id']}: applications must be a list")
    results = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get('role'), str) or not entry['role'].strip():
            raise ValueError(f"{item['id']}: describe how each example applies")
        if ('id' in entry) == ('file' in entry):
            raise ValueError(f"{item['id']}: choose a library ID or a method attachment")
        if 'id' in entry:
            target = items.get(entry['id']) if isinstance(entry['id'], str) else None
            if target is None or target['scope'] == 'reference':
                raise ValueError(f"{item['id']}: missing usable example {entry['id']!r}")
            results.append(dict(id=target['id'], revision=target['revision'], title=target['title'],
                                scope=target['scope'], path=target['preview'], role=entry['role']))
        else:
            matches = [f for f in item['files'] if f['label'] == entry['file']]
            if len(matches) != 1:
                raise ValueError(f"{item['id']}: missing or ambiguous method attachment {entry['file']!r}")
            results.append(dict(title=matches[0]['label'], scope=item['scope'],
                                path=matches[0]['path'], role=entry['role']))
    return results


def save(root,items):
    for item in items.values():
        if 'applications' in item['editorial']:
            research_applications(item, items)
    ordered=dict(sorted(items.items()))
    atomic(root/'catalog.json',json_text({'version':1,'categories':[dict(id=k,title=v,priority='core' if i<9 else 'secondary') for i,(k,v) in enumerate(CATEGORIES.items())],'items':ordered}))
    write_html(root,ordered)


def media_view(path, title, loop=False):
    src = quote(path)
    kind = MIME_KIND[Path(path).suffix.lower()]
    if kind == 'image':
        return f'<img loading="lazy" src="{src}" alt="{html.escape(title)}">'
    extra = ' playsinline' if kind == 'video' else ''
    if loop:
        extra += ' loop'
    return f'<{kind} controls preload="metadata"{extra} src="{src}"></{kind}>'


def visual_views(item, visual):
    labels = {f['label']: f for f in item['files'][1:]}
    views = [('预览', item['preview'])]
    for name, label in visual.get('samples', {}).items():
        file = labels[label]
        # A designated sample may also be the default preview.
        if file['sha256'] == item['preview_sha256']:
            views[0] = (name, item['preview'])
        else:
            views.append((name, file['path']))
    options = ''.join(f'<option value="{i}">{html.escape(name)}</option>'
                      for i, (name, _) in enumerate(views))
    selector = (f'<label>示范 <select class="sample-select">{options}</select></label>'
                if len(views) > 1 else '')
    looping = ('<label><input type="checkbox" class="loop-toggle" checked> 循环</label>'
               if any(MIME_KIND[Path(path).suffix.lower()] == 'video' for _, path in views) else '')
    controls = f'<div class="sample-controls">{selector}{looping}</div>' if selector or looping else ''
    panels = []
    for i, (name, path) in enumerate(views):
        hidden = ' hidden' if i else ''
        player = media_view(path, item['title'] + ' · ' + name, loop=True)
        panels.append(f'<div class="sample-view"{hidden}>{player}</div>')
    return ''.join(panels) + controls


def visual_links(visual, items):
    paragraphs = []
    for field, heading in [('parents', '组合来源'), ('related', '相关示例')]:
        links = []
        for relation in visual.get(field, []):
            card_id = relation['id'] if isinstance(relation, dict) else relation
            role = relation.get('role', '') if isinstance(relation, dict) else ''
            other = items.get(card_id)
            if other:
                label = html.escape(other['title'])
                scope = SCOPE[other['scope']]
                links.append(f'<a href="#{card_id}">{label}</a>（{scope}）' + html.escape(role))
            else:
                links.append(html.escape(link_description(relation)))
        if links:
            paragraphs.append(f'<p>{heading}　' + '、'.join(links) + '</p>')
    if visual.get('sources'):
        entries = []
        for source in visual['sources']:
            title, url, role = (html.escape(source[k]) for k in ('title', 'url', 'role'))
            entries.append(f'<li><a href="{url}" target="_blank" rel="noopener noreferrer">{title}</a> · {role}</li>')
        paragraphs.append('<details class="source-pages"><summary>参考技法与原站</summary><ul>' +
                          ''.join(entries) + '</ul></details>')
    return ''.join(paragraphs)


def review_history(root, item, items):
    records = []
    notes = [json.loads(path.read_text()) for path in (root / 'visual-reviews' / item['id']).glob('*.json')]
    for review in sorted(notes, key=lambda r: (r.get('reviewed_on', ''), r.get('round', 0)), reverse=True):
        verdict = str(review['verdict'])
        outcome = html.escape({'deliver': '通过', 'revise': '退改', 'hold': '暂缓'}.get(verdict, verdict))
        lesson = html.escape(str(review['lesson']))
        findings = review.get('evidence', [])
        if isinstance(findings, str):
            evidence = '<p>' + html.escape(findings) + '</p>'
        else:
            observations = []
            for finding in findings:
                byline = ' · '.join(html.escape(str(finding[k])) for k in ('reviewer', 'role') if finding.get(k))
                interval = f'第 {int(finding["start_frame"])}–{int(finding["end_frame"])} 帧'
                observations.append(f'<li>{byline} · {interval}<p>{html.escape(finding["evidence"])}</p></li>')
            evidence = '<ul>' + ''.join(observations) + '</ul>' if observations else ''
        context = [review.get('reviewed_on'), review.get('shot'),
                   '当前卡版本' if review.get('card_revision') == item['revision'] else '当时卡版本']
        scope = ' · '.join(html.escape(str(v)) for v in context if v)
        intent = f'<p>镜头意图　{html.escape(review["intent"])}</p>' if review.get('intent') else ''
        reason = f'<p>裁决理由　{html.escape(review["decision_reason"])}</p>' if review.get('decision_reason') else ''
        related_id = review.get('related_card_id', '')
        related = items.get(related_id)
        link = (f' · <a href="#{related_id}">{html.escape(related["title"])}</a>（{SCOPE[related["scope"]]}）'
                if related else '')
        records.append(f'<li><strong>{outcome}</strong> · {scope}{link}{intent}{reason}<p>{lesson}</p>{evidence}</li>')
    history = ''
    if records:
        history = (f'<details class="reviews"><summary>实片审片记录（{len(records)}）</summary>'
                   '<ul>' + ''.join(records) + '</ul></details>')
    results = []
    for path in sorted((root / 'learning-results' / item['id']).glob('*.json')):
        row = json.loads(path.read_text())
        assessment = {'supported': '支持', 'failed': '未奏效', 'inconclusive': '尚无定论'}[row['assessment']]
        method = '匿名对照试剪' if row.get('evidence_type') == 'blind_trial' else '成片审片'
        ref = html.escape(path.relative_to(root).as_posix(), quote=True)
        results.append(f'<li><strong>{assessment}</strong> · {method}<p>{html.escape(row["reason"])}</p>'
                       f'<a href="{ref}">查看本次使用及观察记录</a></li>')
    if results:
        history += ('<details class="reviews"><summary>在其他镜头的使用效果</summary><ul>'
                    + ''.join(results) + '</ul></details>')
    return history


def asset_card(root, item, items):
    esc = html.escape
    visual = item['editorial'].get('visual')
    visual_type = visual['type'] if visual else ''
    viewer = visual_views(item, visual) if visual else media_view(item['preview'], item['title'])
    tools = ''
    if visual:
        prompt = esc(visual['prompt'])
        terms = ' · '.join(esc(t) for t in [VISUAL_TYPES[visual_type], *visual.get('terms', [])])
        tools = (f'<p class="visual-terms">{terms}</p><details class="prompt">'
                 f'<summary>提示词</summary><textarea readonly aria-label="提示词">\n{prompt}</textarea>'
                 f'<button type="button" class="copy">复制提示词</button> '
                 f'<span class="copy-status" role="status"></span></details>'
                 f'{visual_links(visual, items)}'
                 '<label class="mix-select"><input type="checkbox" class="mix-choice"> 加入混搭</label>'
                 '<a href="#mix" class="mix-jump" hidden>查看混搭草案</a>')
    url = urlsplit(item['source'])
    source = esc(item['source'])
    original = f'<a href="{source}">来源原页</a>' if url.scheme in ('http','https') else source
    files = ' · '.join(f'<a href="{quote(f["path"])}">{esc(f["label"])}</a>' for f in item['files'])
    timing = ' · '.join(esc(str(k) + ' ' + str(v)) for k, v in item['editorial'].items() if k not in ('visual','file_credits','credit_sources','applications'))
    for example in research_applications(item, items):
        title, role = esc(example['title']), esc(example['role'])
        path = quote(example['path'])
        if 'id' in example:
            tools += (f'<p><a href="#{esc(example["id"])}">{title}</a> · '
                      f'<a href="{path}">看示范</a>（{SCOPE[example["scope"]]}）<br>{role}</p>')
        else:
            tools += f'<p><a href="{path}">{title}</a><br>{role}</p>'
    scope = SCOPE[item['scope']]
    return (f'<article id="{item["id"]}" data-id="{item["id"]}" data-scope="{item["scope"]}" data-visual="{visual_type}" '
            f'data-terms="{esc(search_text(item))}" data-title="{esc(item["title"])}" '
            f'data-keywords="{esc(" ".join(search_keywords(item)))}" '
            f'data-use="{esc(item["use"])}">{viewer}<h3>{esc(item["title"])}</h3>'
            f'<p>{esc(item["use"])}</p><small>{scope} · {esc(" · ".join(item["tags"]))}</small>{tools}'
            f'<details><summary>来源、剪点与原件</summary><p>{scope} · {esc(item["license"])} · '
            f'{esc(item["author"])}</p><p>{original} · {files}</p><p>{timing}</p>'
            f'<a href="#{item["id"]}"><code>{item["id"]}</code></a></details>'
            f'{review_history(root, item, items)}</article>')


def write_html(root,items):
    esc=html.escape
    sections=[]
    opts=[]
    for i,(key,category) in enumerate(CATEGORIES.items()):
        group=[r for r in items.values() if r['category']==key]
        opts.append(f'<option value="{key}">{esc(category)} ({len(group)})</option>')
        cards = [asset_card(root, r, items) for r in group]
        priority='必备' if i<9 else '专题扩展'
        empty='<p class="empty">待入库</p>' if not group else ''
        sections.append(f'<section data-category="{key}"><h2>{esc(category)} <small>{priority} · {len(group)}</small></h2><div class="grid">{"".join(cards)}</div>{empty}</section>')
    page=Path(__file__).with_name('library-index.html').read_text()
    visual_opts = ''.join(f'<option value="{key}">{title}</option>' for key, title in VISUAL_TYPES.items())
    page = page.replace('<!-- VISUAL OPTIONS -->', visual_opts)
    rules = json.dumps(library_search.RULES, ensure_ascii=False).replace('<', r'\u003c')
    page = page.replace('<!-- SEARCH RULES -->', rules)
    engine = Path(__file__).with_name('library-search.js').read_text()
    page = page.replace('<!-- SEARCH ENGINE -->', engine)
    atomic(root/'index.html',page.replace('<!-- OPTIONS -->',''.join(opts)).replace('<!-- CONTENT -->',''.join(sections)))


def config_path():
    home = os.environ.get('XDG_CONFIG_HOME')
    return (Path(home).expanduser() if home else Path.home()/'.config')/'creator-video'/'library.json'


def find_root(explicit, start=None):
    if explicit:
        return explicit.expanduser().resolve()
    if os.environ.get('CREATOR_MEDIA_LIBRARY'):
        return Path(os.environ['CREATOR_MEDIA_LIBRARY']).expanduser().resolve()
    start = start or Path.cwd()
    for parent in (start, *start.parents):
        library=parent/'media-library'
        if library.is_dir():
            return library
    path=config_path()
    if path.is_file():
        configured=Path(json.loads(path.read_text())['root']).expanduser().resolve()
        if not (configured/'catalog.json').is_file():
            raise ValueError(f'Configured library has moved: {configured}. Use set-default PATH to relink it.')
        return configured
    return Path.cwd()/'media-library'


def set_default(root):
    if not (root/'catalog.json').is_file():
        raise ValueError(f'No existing library catalog in {root}')
    load(root)
    dest=config_path()
    with exclusive(dest.parent):
        conf=json.loads(dest.read_text()) if dest.is_file() else {}
        conf['root']=str(root)
        atomic(dest,json_text(conf))
    print(root)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root',type=Path)
    sub=ap.add_subparsers(dest='cmd',required=True)
    add=sub.add_parser('add')
    add.add_argument('spec',type=Path,nargs='+')
    add.add_argument('--replace',action='store_true')
    search=sub.add_parser('search')
    search.add_argument('terms',nargs='*')
    search.add_argument('--category',choices=list(CATEGORIES))
    search.add_argument('--scope',choices=[*SCOPE, 'all'],
                        help='Include research notes and all other use scopes with all')
    search.add_argument('--content', action='store_true',
                        help='Also search the text of notes, scripts and subtitles stored as MD, TXT, SRT or VTT')
    search.add_argument('--visual',choices=list(VISUAL_TYPES),help='Filter visual cards by technique')
    search.add_argument('--json',action='store_true')
    take=sub.add_parser('use')
    take.add_argument('id')
    take.add_argument('--project',type=Path,required=True)
    take.add_argument('--purpose',choices=['production','editorial'],default='production')
    cfg=sub.add_parser('set-default',help='Use this library from projects with no local library')
    cfg.add_argument('path',nargs='?',type=Path)
    sub.add_parser('verify')
    sub.add_parser('browse')
    args=ap.parse_args()
    if args.cmd=='set-default' and args.path:
        if args.root and args.root.expanduser().resolve()!=args.path.expanduser().resolve():
            ap.error('Supply the default library through either PATH or --root.')
        root=args.path.expanduser().resolve()
    else:
        root=find_root(args.root)
    if args.cmd=='set-default':
        set_default(root)
        return
    if args.cmd=='add':
        for spec in args.spec:
            data=json.loads(spec.read_text())
            register(root,data if isinstance(data,list) else [data],spec.resolve().parent,args.replace)
        print(len(load(root)),'assets')
        return
    if not (root/'catalog.json').is_file():
        raise ValueError(f'No catalog in {root}. Run add with an asset record first.')
    items=load(root)
    if args.cmd=='search':
        eligible = {id_: v for id_, v in items.items()
                    if (not args.category or v['category'] == args.category)
                    and (not args.visual or v['editorial'].get('visual', {}).get('type') == args.visual)
                    and (args.scope == 'all' or
                         (v['scope'] == args.scope if args.scope else v['scope'] != 'reference'))}
        results = search_items(eligible, args.terms, root if args.content else None)
        print(json_text(results) if args.json else '\n'.join(f"{r['id']}\t{r['title']}\t{r['scope']}" for r in results))
    if args.cmd=='use':
        print(use(root,items[args.id],args.project.resolve(),args.purpose))
    if args.cmd=='verify':
        for item in items.values():
            research_applications(item, items)
            for path,h in claims(root,item):
                if digest(path)!=h:
                    raise ValueError(f'Bad hash: {path}')
        print(f'{len(items)} items, all original, companion and preview checksums verified')
    if args.cmd=='browse':
        with exclusive(root):
            write_html(root,load(root))
        print(root/'index.html')


if __name__=='__main__':
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as exc:
        sys.exit(str(exc))
