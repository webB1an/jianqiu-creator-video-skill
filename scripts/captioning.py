"""Short Chinese reading units while preserving the natural take and its timing."""
import logging
import re
import unicodedata
from functools import lru_cache

import numpy as np

# Preserve numerical grouping, decimal points, clock times, initials, hyphens and middle dots.
STOP = re.compile(r'[。！？；、…!?;]+|(?<!\d)[：:]|[：:](?!\d)|(?<!\d)[，,]|[，,](?!\d{3}(?!\d))|(?<![A-Za-z0-9])\.|\.(?=\s|$)')
QUOTES = '“”‘’「」『』'
MIN_CUE_SECONDS = .45
LIMIT = 28  # half-width columns, roughly fourteen Chinese characters

PROTECTED_TERMS = (
    '原尺寸', '原尺寸画面', '采样率', '比特率', '帧间隔',
    '大模型', '人工智能', '收益率', '市盈率', '毛利率', '净利率', '现金流',
    '资本开支', '供应链', '分辨率', '刷新率', '对比度', '白平衡', '色域',
    '动态范围', '透明度', '时间线', '关键帧', '工作流', '上下文', '提示词',
    '吞吐量', '延迟', '功耗', '算力', '芯片', '显存', '内存', '带宽',
    '百分点', '同比增长', '环比增长', '成交量', '市占率', '资产负债表',
    '自由现金流', '净利润', '营业收入', '毛利', '估值', '市值', '汇率',
)

# Keep numbers paired with their unit or ordinal counter atomic when wrapping.
NUMERIC_UNIT = re.compile(
    r'第\s*[0-9一二两三四五六七八九十百千万]+\s*[行格步层镜条版代次项笔位]|'
    r'[+-]?[0-9]+(?:[.,·][0-9]+)*\s*(?:个百分点|%|％|万亿|亿元|万元|美元|欧元|英镑|港元|日元|元|倍|成|折|'
    r'毫秒|微秒|纳秒|秒|分钟|小时|天|年|月|日|帧|像素|LUFS|dBTP|dB|fps|px|'
    r'kWh|mAh|MHz|GHz|kHz|Hz|TB|GB|MB|KB|MW|kW|mW|W|mV|V|mA|A|℃|度|公里|千米|厘米|毫米|米|公斤|千克|克|吨|毫升|升)'
)
ORPHAN_START = frozenset('的了着过吗呢吧啊地得们')


def width(text):
    return sum(2 if unicodedata.east_asian_width(c) in ('F', 'W') else 1 for c in text)


def clean_parts(text):
    return [s for raw in STOP.split(text) if (s := raw.strip().strip(QUOTES).strip())]


@lru_cache(maxsize=2)
def tokenizer(protect_words):
    import jieba
    jieba.setLogLevel(logging.ERROR)
    segmenter = jieba.Tokenizer()
    if protect_words:
        for word in PROTECTED_TERMS:
            segmenter.add_word(word, freq=1000000)
    return segmenter


def _tokenize_clause(clause, protect_words):
    tokens = []
    if protect_words:
        pos = 0
        for m in NUMERIC_UNIT.finditer(clause):
            if m.start() > pos:
                tokens.extend(_tokenize_clause(clause[pos:m.start()], False))
            tokens.append(m.group(0))
            pos = m.end()
        if pos > 0:
            if pos < len(clause):
                tokens.extend(_tokenize_clause(clause[pos:], False))
            return tokens
    seg = tokenizer(protect_words)
    for run in re.findall(r'[\u3400-\u9fff]+|[A-Za-z0-9]+(?:[.·:：/，,-][A-Za-z0-9]+)*|\s+|.', clause):
        tokens.extend(seg.cut(run) if '\u3400' <= run[0] <= '\u9fff' else [run])
    return tokens


def wrap(clause, protect_words=True):
    if width(clause) <= LIMIT:
        return [clause]
    tokens = _tokenize_clause(clause, protect_words)
    # Balance whole words; favor a fresh clause starting on a connective, avoid orphan leading particles.
    options = {len(tokens): (0, [])}
    for i in range(len(tokens) - 1, -1, -1):
        best = None
        for j in range(i + 1, len(tokens) + 1):
            s = ''.join(tokens[i:j]).strip()
            if width(s) > LIMIT and j > i + 1:
                break
            if not s or j not in options:
                continue
            cost = (width(s) - 22) ** 2 + options[j][0]
            if protect_words and j < len(tokens) and tokens[j - 1] in ('和', '与', '或', '及', '把', '从', '对', '向', '将'):
                cost += 80
            if protect_words and i > 0 and s[0] in ORPHAN_START:
                cost += 120
            if j < len(tokens) and tokens[j] in ('同时', '随后', '因为', '已经', '也', '并', '将', '由', '在', '但', '而', '所以'):
                cost -= 22
            if best is None or cost < best[0]:
                best = (cost, [s] + options[j][1])
        if best is not None:
            options[i] = best
    return options[0][1]


def parts(line):
    manual = line.get('caption_parts')
    if manual is None:
        automatic = [s for clause in clean_parts(line['text'])
                     for s in wrap(clause, protect_words='caption_offsets' not in line)]
        if not automatic:
            raise ValueError('A caption needs words')
        return automatic
    compact = lambda seq: re.sub(r'\s+', '', ''.join(seq))
    if not isinstance(manual, list) or not manual or any(not isinstance(s, str) or not s.strip() or clean_parts(s) != [s] for s in manual) or compact(manual) != compact(clean_parts(line['text'])):
        raise ValueError('caption_parts must reproduce the displayed words, without clause punctuation')
    if any(width(s) > LIMIT for s in manual):
        raise ValueError('An authored caption is wider than 14 Chinese characters')
    return manual


def boundaries(units, samples, rate, offsets=None):
    n = len(samples)
    if offsets is not None:
        cuts = [0, *(round(v * rate) for v in offsets), n]
        if len(cuts) != len(units) + 1 or any(a >= b for a, b in zip(cuts, cuts[1:])):
            raise ValueError('caption_offsets must be ascending internal boundaries in seconds')
        return cuts
    if len(units) == 1:
        return [0, n]
    hop = max(1, rate // 100)
    pad = (-n) % hop
    data = np.pad(samples.astype('float32'), (0, pad)).reshape(-1, hop)
    energy = np.sqrt(np.mean(data * data, axis=1))
    active = np.flatnonzero(energy > max(100, energy.max() * .035))
    first, last = (active[0], active[-1] + 1) if len(active) else (0, len(energy))
    weights = [sum(max(1, len(w) // 3) if w.isascii() else 1 for w in re.findall(r'[A-Za-z0-9]+|[^\s]', s)) for s in units]
    cuts = [0]
    for index, f in enumerate(np.cumsum(weights)[:-1] / sum(weights)):
        expected = first + (last - first) * f
        lo = max(cuts[-1] // hop + 1, round(expected) - 24)
        hi = min(len(energy) - len(units) + index + 1, round(expected) + 24)
        if hi < lo:
            raise ValueError('Recording too short for caption units')
        positions = np.arange(lo, hi + 1)
        cost = energy[positions] / max(1, energy.max()) + .12 * ((positions - expected) / 24) ** 2
        cuts.append(int(positions[np.argmin(cost)]) * hop)
    return cuts + [n]


def timed_parts(line, samples, rate):
    units = parts(line)
    cuts = boundaries(units, samples, rate, line.get('caption_offsets'))
    if 'caption_parts' in line or 'caption_offsets' in line or len(units) < 2:
        return units, cuts

    compact = lambda value: ''.join(value.split())
    hard_stops = set()
    characters = 0
    for sentence in re.split(r'[。！？；!?;]+|\.(?=\s|$)', line['text']):
        characters += sum(len(compact(part)) for part in clean_parts(sentence))
        hard_stops.add(characters)
    minimum = MIN_CUE_SECONDS * rate
    i = 0
    while i < len(units):
        if cuts[i + 1] - cuts[i] < minimum:
            prefix = sum(len(compact(unit)) for unit in units[:i])
            for left, boundary in ((i, prefix + len(compact(units[i]))), (i - 1, prefix)):
                if left < 0 or left + 1 >= len(units) or boundary in hard_stops:
                    continue
                joined = units[left] + ' ' + units[left + 1]
                if width(joined) <= LIMIT:
                    units[left:left + 2] = [joined]
                    del cuts[left + 1]
                    i = left
                    break
            else:
                i += 1
        else:
            i += 1
    return units, cuts
