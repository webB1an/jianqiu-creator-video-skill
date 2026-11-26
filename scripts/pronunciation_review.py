"""Identify words whose intended reading, ZipVoice lexicon split, or final vowel calls for review."""
import argparse
from functools import lru_cache
import json
import re
from pathlib import Path

from prosody_markup import parse_prosody_markup

# The reading of 行, 重 and similar characters depends on the surrounding words.
# These are localizable prompts for review, not blanket substitutions.
CONTEXT = [
    (r'重写|重读|重算|重排|重做|重画|重录|重建|重渲|重试|重构|重塑|重复|重新', '再次进行通常读 chóng；核对具体语义'),
    (r'[一二两三四五六七八九十百千万亿几多整每各上下]行|行数|行列|行距|代码行', '文字行、代码行、表格行读 háng；队伍或行走可能读 xíng'),
    (r'还得|才得|就得|净得|[说订变跑做写画看听读讲走拿好急]得', '得可作补语标记 de、获得 dé 或必须 děi'),
    (r'长[任务期途时线串]|[链波帧句镜全片边步字总]长|延长|增长|多长|很长|变长', '长度或持续较久读 cháng；增大或首领读 zhǎng'),
    (r'衡量|测量|要量|用量|数量|量一', '测量动作读 liáng；数量、用量读 liàng'),
    (r'偏好|好几|好处|挣钱|挣到|挣扎', '爱好读 hào；挣钱读 zhèng；挣扎读 zhēng'),
    (r'约为|成为|为[客公他她其]', '表示等于或成为读 wéi；表示替谁读 wèi'),
    (r'了当|了得|了却|了解', '跨词边界尤其要核对 le 与 liǎo'),
    (r'干不干净|干干净净|晒干|擦干|风干|榨干', '干燥、洁净读 gān；做事、主干读 gàn'),
    (r'相差|反差|落差|温差|时差|价差|误差|偏差', '差值类名词与动词相差在普通话读 chā，ZipVoice 词表默认多为 chà'),
    (r'倒数|数一数|数清|数出|数着|数不过来', '倒数在数学读 dào shù、倒计时读 dào shǔ；点数动作读 shǔ'),
    (r'调优|调参|调节|调整|调度|调用|校色|校验|校准|校对', '核对调 tiáo/diào 与校 jiào/xiào'),
    (r'切换|切开|切掉|切出|切回|切到|切入|切点|切分', '切割或切换读 qiē；贴切、一切读 qiè'),
    (r'弹出|弹窗|反弹|背景乐|主题乐|古典乐', '弹跳读 tán、子弹读 dàn；音乐读 yuè、快乐读 lè'),
]

# High-risk heteronyms whose ZipVoice single-char default frequently causes clear misreadings
# when an unindexed compound falls back to 1-character lookup (low false-positive set).
HIGH_RISK_SINGLE_CHARS = {
    '行': ('x0 ing2', '单字默认 xíng；指代码行/行列/行业须锁定 h0 ang2'),
    '重': ('zh0 ong4', '单字默认 zhòng；表再次/重复（如重算、重写）须锁定 ch0 ong2'),
    '长': ('zh0 ang3', '单字默认 zhǎng；表长度/时间长（如链长、多长、变长）须锁定 ch0 ang2'),
    '干': ('g0 an4', '单字默认 gàn；表洁净/干燥（如干不干净、晒干）须锁定 g0 an1'),
    '差': ('ch0 a4', '单字默认 chà；表差值（如价差、色差）须锁定 ch0 a1'),
    '调': ('d0 iao4', '单字默认 diào；表调节/调参/调优须锁定 t0 iao2'),
    '切': ('q0 ie4', '单字默认 qiè；表切割/切换/切出须锁定 q0 ie1'),
    '弹': ('d0 an4', '单字默认 dàn；表弹出/弹回须锁定 t0 an2'),
    '校': ('x0 iao4', '单字默认 xiào；表校对/校色/校准须锁定 j0 iao4'),
    '乐': ('l0 e4', '单字默认 lè；表音乐/配乐/背景乐须锁定 y0 ue4'),
}

# Context-gated fallback patterns for heteronyms whose 1-char default is usually right
# (e.g. 数=shù, 倒=dào, 塞=sāi, 盛=shèng) except in these specific unindexed compounds.
CONTEXT_FALLBACK_PATTERNS = [
    (r'数[一清出着过落]', '数', 'sh0 u4', '单字默认 shù；此处为点数动词，须锁定 sh0 u3'),
    (r'(?:倒[下掉塌闭霉]|[扳难放摔打跌压倾颠]倒)', '倒', 'd0 ao4', '单字默认 dào；此处读 d0 ao3'),
    (r'(?:[堵阻闭拥淤]塞|[边要]塞)', '塞', 's0 ai1', '单字默认 sāi；堵塞读 s0 e4，要塞读 s0 ai4'),
    (r'盛[饭汤满水]', '盛', 'sh0 eng4', '单字默认 shèng；盛放动作读 ch0 eng2'),
    (r'(?:[反自内]省|省悟)', '省', 'sh0 eng3', '单字默认 shěng；反省读 x0 ing3'),
    (r'(?:[转打]圈|转盘)', '转', 'zh0 uan3', '单字默认 zhuǎn；旋转绕圈读 zh0 uan4'),
]

# Broad polyphonic character set used to detect cross-word greedy collisions that change a reading.
POLYPHONIC_CHARS = set(HIGH_RISK_SINGLE_CHARS) | set(
    '为得还只着分卷解传量空强降处当都发更供好号会几假教结尽劲看累没蒙难宁铺奇任散丧舍识似说宿帖通吐委系鲜相兴旋压应与脏曾扎占折正中种了卡薄血核削剥挑称乘冲创答担缝给冠划混间将禁落埋脉抹磨辟片漂屏悄翘圈扫什提瓦巷咽涨轴爪作'
)

# Upstream ZipVoice lexicon entries that are either wrong in standard Mandarin or commonly misread by context.
RISKY_LEXICON_ENTRIES = {
    '相差': ('x0 iang1 ch0 a4', 'ZipVoice 词表将「相差」标为 chà，标准普通话应为 x0 iang1 ch0 a1'),
    '反差': ('f0 an3 ch0 a4', 'ZipVoice 词表将「反差」标为 chà，标准普通话应为 f0 an3 ch0 a1'),
    '落差': ('l0 uo4 ch0 a4', 'ZipVoice 词表将「落差」标为 chà，标准普通话应为 l0 uo4 ch0 a1'),
    '温差': ('w0 en1 ch0 a4', 'ZipVoice 词表将「温差」标为 chà，标准普通话应为 w0 en1 ch0 a1'),
    '时差': ('sh0 i2 ch0 a4', 'ZipVoice 词表将「时差」标为 chà，标准普通话应为 sh0 i2 ch0 a1'),
    '净得': ('j0 ing4 d0 e5', 'ZipVoice 词表将「净得」标为轻声 de，净收益应为 j0 ing4 d0 e2'),
    '一行': ('y0 i4 x0 ing2', 'ZipVoice 词表默认 yī xíng（一行人）；指一行字/代码需改为 y0 i4 h0 ang2'),
    '三行': ('s0 an1 x0 ing2', 'ZipVoice 词表默认 sān xíng；指三行字/代码需改为 s0 an1 h0 ang2'),
    '五行': ('w0 u3 x0 ing2', 'ZipVoice 词表默认 wǔ xíng（五行）；指五行代码/表格需改为 w0 u3 h0 ang2'),
    '下行': ('x0 ia4 x0 ing2', 'ZipVoice 词表默认 xià xíng；指表格/文本下一行需核对 h0 ang2'),
    '总长': ('z0 ong2 zh0 ang3', 'ZipVoice 词表默认 zǒng zhǎng（首领）；指总长度须改为 z0 ong3 ch0 ang2'),
    '倒数': ('d0 ao4 sh0 u3', 'ZipVoice 词表默认 dào shǔ（倒计时）；数学倒数须改为 d0 ao4 sh0 u4'),
    '了当': ('l0 iao3 d0 ang4', '核对是「直截了当」(liǎo dàng) 还是跨词「……了 + 当……」'),
}


def mark_context(words):
    return [dict(term=m.group(), offset=m.start(), question=meaning)
            for expr, meaning in CONTEXT for m in re.finditer(expr, words)]


@lru_cache(maxsize=2)
def load_lexicon(lexicon_path=None):
    """Load the pinned local ZipVoice Mandarin lexicon if installed."""
    if lexicon_path is None:
        from setup_zipvoice import MODEL_DIR as zipvoice_dir
        path = zipvoice_dir / 'lexicon.txt'
    else:
        path = Path(lexicon_path)
    if not path.is_file():
        return {}
    lex = {}
    with path.open(encoding='utf-8') as handle:
        for raw in handle:
            parts = raw.split()
            if len(parts) > 1:
                lex[parts[0]] = ' '.join(parts[1:])
    return lex


def split_syllables(phones):
    """Split ZipVoice Mandarin phone sequence into per-character syllables (each ends with tone 1-5)."""
    syls, cur = [], []
    for tok in phones.split():
        cur.append(tok)
        if tok and tok[-1] in '12345':
            syls.append(' '.join(cur))
            cur = []
    return [] if cur else syls


def _directed_spans(words, directed_phones):
    """Return sorted non-overlapping (start, end, term, phones) ranges covered by pronunciation.phones."""
    if not directed_phones:
        return []
    terms = [t for t in sorted(directed_phones, key=len, reverse=True) if t and t in words]
    if not terms:
        return []
    pattern = re.compile('|'.join(re.escape(t) for t in terms))
    return [(m.start(), m.end(), m.group(), ' '.join(directed_phones[m.group()].split()))
            for m in pattern.finditer(words)]


def segment_zipvoice(words, lexicon=None, directed_phones=None):
    """Simulate sherpa-onnx ZipVoice phrase-matcher.cc (FMM max_search_len=10) with DirectedVoice aliases."""
    lex = load_lexicon() if lexicon is None else lexicon
    if not lex:
        return []
    normalized = words.replace('：', '，').replace('；', '。')
    spans = _directed_spans(normalized, directed_phones)
    segments = []
    pos = 0
    for s_start, s_end, term, phones in spans:
        if s_start > pos:
            segments.append((pos, normalized[pos:s_start], None))
        segments.append((s_start, term, phones))
        pos = s_end
    if pos < len(normalized):
        segments.append((pos, normalized[pos:], None))

    tokens = []
    for base, chunk, locked_phones in segments:
        if locked_phones is not None:
            tokens.append(dict(offset=base, token=chunk, phones=locked_phones, directed=True))
            continue
        for m in re.finditer(r'[\u4e00-\u9fff]+', chunk):
            run, run_base = m.group(), base + m.start()
            i = 0
            while i < len(run):
                matched = None
                for length in range(min(10, len(run) - i), 1, -1):
                    cand = run[i:i + length]
                    if cand in lex:
                        matched = cand
                        break
                if matched:
                    tokens.append(dict(offset=run_base + i, token=matched, phones=lex[matched], directed=False))
                    i += len(matched)
                else:
                    ch = run[i]
                    tokens.append(dict(offset=run_base + i, token=ch, phones=lex.get(ch, '<OOV>'), directed=False))
                    i += 1
    return tokens


def audit_lexicon(words, directed_phones=None, *, lexicon=None):
    """Detect real ZipVoice pronunciation failure modes before or after synthesis."""
    lex = load_lexicon() if lexicon is None else lexicon
    if not lex:
        return []
    normalized = words.replace('：', '，').replace('；', '。')
    locked = _directed_spans(normalized, directed_phones)
    locked_idx = {i for s, e, _, _ in locked for i in range(s, e)}
    tokens = segment_zipvoice(normalized, lex, directed_phones)
    risks = []
    seen = set()

    def add_risk(kind, offset, term, phones, detail, suggestion=None):
        key = (kind, offset, term)
        if key in seen:
            return
        seen.add(key)
        row = dict(kind=kind, offset=offset, term=term, zipvoice_phones=phones, detail=detail)
        if suggestion:
            row['suggestion'] = suggestion
        risks.append(row)

    for tok in tokens:
        if tok['directed']:
            continue
        w, off, ph = tok['token'], tok['offset'], tok['phones']
        # 1. Cross-word greedy mis-segmentation that changes a polyphonic character's reading
        if len(w) >= 2:
            w_syls = split_syllables(ph)
            if len(w_syls) == len(w):
                for sub in range(1, len(w)):
                    st = off + sub
                    # Stop before punctuation or directed alias boundaries
                    tail_chars = []
                    for p in range(st, min(len(normalized), st + 10)):
                        if p in locked_idx or not ('\u4e00' <= normalized[p] <= '\u9fff'):
                            break
                        tail_chars.append(normalized[p])
                    tail = ''.join(tail_chars)
                    for length in range(len(tail), len(w) - sub, -1):
                        cand = tail[:length]
                        if cand not in lex:
                            continue
                        c_syls = split_syllables(lex[cand])
                        if len(c_syls) != len(cand):
                            continue
                        overlap_len = len(w) - sub
                        diffs = [
                            (w[sub + k], w_syls[sub + k], c_syls[k])
                            for k in range(overlap_len)
                            if w[sub + k] in POLYPHONIC_CHARS and w_syls[sub + k] != c_syls[k]
                        ]
                        if diffs:
                            diff_desc = '、'.join(f'「{c}」被切读为 {a}（原词「{cand}」应读 {b}）' for c, a, b in diffs)
                            ctx_span = normalized[max(0, off - 2):min(len(normalized), st + len(cand) + 2)]
                            add_risk(
                                'cross_word_collision', off, f'{w} / {cand}', ph,
                                f'跨词贪婪误切（…{ctx_span}…）：ZipVoice 先匹配「{w}」，导致 {diff_desc}',
                                f'在 line.pronunciation.phones 中锁定 "{cand}": "{lex[cand]}"',
                            )
                        break

        # 2. Known upstream wrong or context-sensitive multi-char lexicon entries
        if w in RISKY_LEXICON_ENTRIES:
            default_ph, hint = RISKY_LEXICON_ENTRIES[w]
            if ph == default_ph:
                add_risk('wrong_lexicon_default', off, w, ph, hint)

        # 3. High-risk polyphonic single-character fallback (compound missing from lexicon.txt)
        if len(w) == 1 and w in HIGH_RISK_SINGLE_CHARS:
            _, hint = HIGH_RISK_SINGLE_CHARS[w]
            ctx_span = normalized[max(0, off - 2):min(len(normalized), off + 3)]
            add_risk(
                'single_char_fallback', off, w, ph,
                f'词表缺复合词，退回单字「{w}={ph}」（语境：…{ctx_span}…）；{hint}',
                '核对所在词组的标准普通话读音，若不符请在 line.pronunciation.phones 显式锁定完整词组音素',
            )
        elif len(w) == 1:
            for expr, ch, default_ph, hint in CONTEXT_FALLBACK_PATTERNS:
                if w == ch and ph == default_ph:
                    for m in re.finditer(expr, normalized):
                        if m.start() <= off < m.end():
                            add_risk(
                                'single_char_fallback', off, m.group(), ph,
                                f'词表缺复合词「{m.group()}」，退回单字「{w}={ph}」；{hint}',
                                '核对所在词组的标准普通话读音，若不符请在 line.pronunciation.phones 显式锁定完整词组音素',
                            )
    return risks


def summary(takes):
    rows = []
    for number, take in enumerate(takes, 1):
        text = take.get('spoken', '')
        cues = mark_context(text)
        direct = take.get('pronunciation', {})
        directed_phones = direct.get('phones', {}) if isinstance(direct, dict) else {}
        lex_risks = audit_lexicon(text, directed_phones)
        end = take.get('ending_check', {})
        if end.get('attempts'):
            end = end['attempts'][end.get('selected_attempt', 0)]
        rows.append(dict(
            line=number, shot=take.get('shot'), spoken=text, contexts=cues,
            lexicon_risks=lex_risks,
            directed=directed_phones, acoustic_boundary=end.get('acoustic_boundary'),
            original_audio=take.get('file'), ending_status=end.get('status'),
        ))
    return rows


# Only these contexts identify a reading unambiguously (all phonemes verified against ZipVoice tokens.txt).
# Ambiguous cases remain in CONTEXT / RISKY_LEXICON_ENTRIES / HIGH_RISK_SINGLE_CHARS for review.
_ROW_AFTER = r'(?=\s*(?:[A-Za-z0-9_+.-]+\s*)?(?:代码|源码|程序|指令|语句|字|数字|数据|文本|表格|账目|记录|配置|日志|脚本|注释|文字|放在一起))'
CLEAR_READINGS = [
    # Cross-word collision guards & unambiguous háng compounds
    (r'行列式', '行列式', 'h0 ang2 l0 ie4 sh0 i4'),
    (r'代码行', '代码行', 'd0 ai4 m0 a3 h0 ang2'),
    (r'行数', '行数', 'h0 ang2 sh0 u4'),
    (r'一行' + _ROW_AFTER, '一行', 'y0 i4 h0 ang2'),
    (r'两行' + _ROW_AFTER, '两行', 'l0 iang3 h0 ang2'),
    (r'三行' + _ROW_AFTER, '三行', 's0 an1 h0 ang2'),
    (r'几行' + _ROW_AFTER, '几行', 'j0 i3 h0 ang2'),
    (r'多行' + _ROW_AFTER, '多行', 'd0 uo1 h0 ang2'),
    (r'每行' + _ROW_AFTER, '每行', 'm0 ei3 h0 ang2'),
    (r'各行' + _ROW_AFTER, '各行', 'g0 e4 h0 ang2'),
    (r'十行' + _ROW_AFTER, '十行', 'sh0 i2 h0 ang2'),
    (r'百行' + _ROW_AFTER, '百行', 'b0 ai3 h0 ang2'),
    (r'千行' + _ROW_AFTER, '千行', 'q0 ian1 h0 ang2'),
    (r'万行' + _ROW_AFTER, '万行', 'w0 an4 h0 ang2'),
    (r'亿行' + _ROW_AFTER, '亿行', 'y0 i4 h0 ang2'),
    # Unambiguous chóng compounds missing from ZipVoice lexicon.txt
    (r'重写', '重写', 'ch0 ong2 x0 ie3'),
    (r'重算', '重算', 'ch0 ong2 s0 uan4'),
    (r'重排', '重排', 'ch0 ong2 p0 ai2'),
    (r'重做', '重做', 'ch0 ong2 z0 uo4'),
    (r'重画', '重画', 'ch0 ong2 h0 ua4'),
    (r'重录', '重录', 'ch0 ong2 l0 u4'),
    (r'重渲', '重渲', 'ch0 ong2 x0 uan4'),
    (r'重试', '重试', 'ch0 ong2 sh0 i4'),
    (r'重构', '重构', 'ch0 ong2 g0 ou4'),
    (r'重塑', '重塑', 'ch0 ong2 s0 u4'),
    (r'重来', '重来', 'ch0 ong2 l0 ai2'),
    (r'重回', '重回', 'ch0 ong2 h0 ui2'),
    # Unambiguous cháng compounds missing from ZipVoice lexicon.txt (single-char 长 defaults to zhǎng)
    (r'链长', '链长', 'l0 ian4 ch0 ang2'),
    (r'帧长', '帧长', 'zh0 en1 ch0 ang2'),
    (r'句长', '句长', 'j0 u4 ch0 ang2'),
    (r'镜长', '镜长', 'j0 ing4 ch0 ang2'),
    (r'全长', '全长', 'q0 uan2 ch0 ang2'),
    (r'片长', '片长', 'p0 ian4 ch0 ang2'),
    (r'边长', '边长', 'b0 ian1 ch0 ang2'),
    (r'步长', '步长', 'b0 u4 ch0 ang2'),
    (r'字长', '字长', 'z0 i4 ch0 ang2'),
    (r'长任务', '长任务', 'ch0 ang2 r0 en4 w0 u4'),
    (r'多长(?!点|出|大|高|胖|肉|芽|草|树)', '多长', 'd0 uo1 ch0 ang2'),
    (r'很长', '很长', 'h0 en3 ch0 ang2'),
    (r'变长', '变长', 'b0 ian4 ch0 ang2'),
    # Unambiguous gān / chā / dé / wéi / děi / hào / zhèng / tiáo / jiào / qiē / tán / yuè compounds
    (r'干不干净', '干不干净', 'g0 an1 b0 u4 g0 an1 j0 ing4'),
    (r'相差', '相差', 'x0 iang1 ch0 a1'),
    (r'反差', '反差', 'f0 an3 ch0 a1'),
    (r'落差', '落差', 'l0 uo4 ch0 a1'),
    (r'温差', '温差', 'w0 en1 ch0 a1'),
    (r'时差', '时差', 'sh0 i2 ch0 a1'),
    (r'价差', '价差', 'j0 ia4 ch0 a1'),
    (r'净得', '净得', 'j0 ing4 d0 e2'),
    (r'约为', '约为', 'y0 ue1 w0 ei2'),
    (r'还得(?=等|看|要|做|去|补|付|花|按|靠|查|问|再|先|把)', '还得', 'h0 ai2 d0 ei3'),
    (r'偏好', '偏好', 'p0 ian1 h0 ao4'),
    (r'挣到', '挣到', 'zh0 eng4 d0 ao4'),
    (r'调优', '调优', 't0 iao2 y0 ou1'),
    (r'调参', '调参', 't0 iao2 c0 an1'),
    (r'校色', '校色', 'j0 iao4 s0 e4'),
    (r'切换', '切换', 'q0 ie1 h0 uan4'),
    (r'切开', '切开', 'q0 ie1 k0 ai1'),
    (r'切掉', '切掉', 'q0 ie1 d0 iao4'),
    (r'切出', '切出', 'q0 ie1 ch0 u1'),
    (r'切回', '切回', 'q0 ie1 h0 ui2'),
    (r'切到', '切到', 'q0 ie1 d0 ao4'),
    (r'弹出', '弹出', 't0 an2 ch0 u1'),
    (r'弹窗', '弹窗', 't0 an2 ch0 uang1'),
    (r'背景乐', '背景乐', 'b0 ei4 j0 ing3 y0 ue4'),
    (r'主题乐', '主题乐', 'zh0 u3 t0 i2 y0 ue4'),
    (r'古典乐', '古典乐', 'g0 u3 d0 ian3 y0 ue4'),
]


def with_context_directions(edit):
    """Apply safe defaults to locally synthesized Mandarin without overriding explicit user phones."""
    from jianqiu_voice import VOICE_ID
    if (edit.get('presenter') or {}).get('recording'):
        return edit
    shots = []
    for shot in edit['shots']:
        lines = []
        for line in shot['lines']:
            if (not line.get('audio') and not line.get('source_audio')
                    and line.get('voice', edit.get('voice', VOICE_ID)) == VOICE_ID):
                words = parse_prosody_markup(line.get('spoken', line['text']))['clean_text']
                existing_spec = line.get('pronunciation')
                existing_phones = dict((existing_spec or {}).get('phones') or {})
                merged = dict(existing_phones)
                for exp, term, phones in CLEAR_READINGS:
                    if term in words and re.search(exp, words):
                        # Never collide with or overwrite an explicit user-directed span.
                        if not any(term in k or k in term for k in existing_phones):
                            merged.setdefault(term, phones)
                if merged and merged != existing_phones:
                    new_spec = dict(existing_spec or {}, phones=merged)
                    line = dict(line, pronunciation=new_spec)
            lines.append(line)
        shots.append(dict(shot, lines=lines))
    return dict(edit, shots=shots)


TECH_LEXICON_PATH = Path(__file__).resolve().parents[1] / 'assets/voices/jianqiu/tech-pronunciation.json'


@lru_cache(maxsize=1)
def load_tech_lexicon():
    if not TECH_LEXICON_PATH.is_file():
        return {}
    try:
        return json.loads(TECH_LEXICON_PATH.read_text(encoding='utf-8')).get('terms') or {}
    except (OSError, ValueError):
        return {}


def audit_tech_terms(words, phones=None):
    """Report unresolved dictionary terms and unlisted all-caps abbreviations."""
    terms = load_tech_lexicon()
    if not words:
        return []
    directed = _directed_spans(words, phones or {})
    spans = []
    found = {}
    for term in sorted(terms, key=len, reverse=True):
        variant = re.escape(term).replace(r'\ ', r'\s+')
        pattern = rf'(?<![A-Za-z0-9]){variant}(?![A-Za-z0-9])'
        for match in re.finditer(pattern, words, flags=re.IGNORECASE if len(term) >= 4 else 0):
            start, end = match.span()
            if any(start < stop and begin < end for begin, stop in spans):
                continue
            spans.append((start, end))
            if any(begin <= start and end <= stop for begin, stop, _, _ in directed):
                continue
            info = terms[term]
            found.setdefault(term, dict(term=term, recommended_read=info.get('read', term), hint=info.get('hint', '')))
    for match in re.finditer(r'(?<![A-Za-z0-9])(?:[A-Z]{2,}[0-9]*)(?![A-Za-z0-9])', words):
        start, end = match.span()
        term = match.group()
        if term in {'OK', 'AM', 'PM'} or any(start < stop and begin < end for begin, stop in spans):
            continue
        if any(begin <= start and end <= stop for begin, stop, _, _ in directed):
            continue
        found.setdefault(term, dict(term=term, recommended_read=term,
                                   hint='词典未收录。请核对逐字母或连读方式，必要时在 spoken 写出读法'))
    return list(found.values())


def audit_edit(edit, timeline=None, *, lexicon_path=None):
    """Audit an edit.json (pre- or post-prepare) for unresolved ZipVoice polyphonic & tech-term risks."""
    from jianqiu_voice import VOICE_ID
    lex = load_lexicon(lexicon_path)
    effective = with_context_directions(edit)
    rows = []
    auto_applied = []
    bypassed_audio = []
    tech_hints = []
    for orig_shot, eff_shot in zip(edit.get('shots', []), effective.get('shots', [])):
        sid = orig_shot.get('id')
        for idx, (orig_line, eff_line) in enumerate(zip(orig_shot.get('lines', []), eff_shot.get('lines', [])), 1):
            lid = orig_line.get('id', f'{sid}.L{idx}')
            words = parse_prosody_markup(orig_line.get('spoken', orig_line.get('text', '')))['clean_text']
            voice = orig_line.get('voice', edit.get('voice', VOICE_ID))
            if orig_line.get('source_audio'):
                continue
            uses_external_audio = bool(orig_line.get('audio'))
            explicit_phones = dict(((orig_line.get('pronunciation') or {}).get('phones')) or {})
            eff_phones = {} if uses_external_audio else dict(((eff_line.get('pronunciation') or {}).get('phones')) or {})
            added = {k: v for k, v in eff_phones.items() if k not in explicit_phones}
            if added:
                auto_applied.append(dict(shot=sid, line_id=lid, phones=added))
            t_hits = audit_tech_terms(words, eff_phones)
            if t_hits:
                tech_hints.append(dict(shot=sid, line_id=lid, text=words, terms=t_hits))
            if voice != VOICE_ID and not uses_external_audio:
                continue
            risks = audit_lexicon(words, eff_phones, lexicon=lex) if lex else []
            if risks:
                entry = dict(shot=sid, line_id=lid, spoken=words, directed=eff_phones, risks=risks)
                if uses_external_audio:
                    entry['audio_bypass'] = orig_line['audio']
                    entry['note'] = '该句绑定了 line.audio，绕过了 ZipVoice 定向词表；请改用 line.pronunciation.phones 或人工复听确认录音无误'
                    bypassed_audio.append(lid)
                rows.append(entry)
    return dict(
        lexicon_loaded=bool(lex),
        lexicon_entries=len(lex),
        auto_directed_count=len(auto_applied),
        auto_directed=auto_applied,
        bypassed_audio_risks=bypassed_audio,
        unresolved_count=sum(len(r['risks']) for r in rows),
        unresolved_lines=rows,
        tech_term_count=sum(len(h['terms']) for h in tech_hints),
        tech_term_hints=tech_hints,
    )


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--project', type=Path, default=Path('.'), help='Project directory containing edit.json')
    cli.add_argument('--raw', action='store_true', help='Audit edit.json without applying CLEAR_READINGS defaults')
    args = cli.parse_args()
    edit = json.loads((args.project / 'edit.json').read_text(encoding='utf-8'))
    if args.raw:
        lex = load_lexicon()
        rows = []
        for shot in edit.get('shots', []):
            for idx, line in enumerate(shot.get('lines', []), 1):
                words = line.get('spoken', line.get('text', ''))
                phones = dict(((line.get('pronunciation') or {}).get('phones')) or {})
                risks = audit_lexicon(words, phones, lexicon=lex) if lex else []
                if risks:
                    rows.append(dict(shot=shot['id'], line_id=line.get('id', f"{shot['id']}.L{idx}"),
                                     spoken=words, directed=phones, risks=risks))
        print(json.dumps(dict(lexicon_loaded=bool(lex), mode='raw', unresolved_count=sum(len(r['risks']) for r in rows),
                              unresolved_lines=rows), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(audit_edit(edit), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
