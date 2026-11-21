"""Identify words whose intended reading or final vowel calls for listening or phoneme review."""
import re
from pathlib import Path

# The reading of 行, 重 and similar characters depends on the surrounding words.
# These are localizable prompts for review, not blanket substitutions.
CONTEXT = [
    (r'重写|重读|重算|重排|重做|重画|重录|重建|重复|重新', '再次进行通常读 chóng；核对具体语义'),
    (r'[一二两三四五六七八九十几多整每各上下]行|行数|行列|行距','文字行、表格行读 háng；队伍或行走可能读 xíng'),
    (r'还得|才得|净得|[说订变跑做写画看听读好急]得','得可作补语标记 de、获得 dé 或必须 děi'),
    (r'长[任务期途时线]|延长|增长','持续较久读 cháng；增大读 zhǎng'),
    (r'衡量|测量|要量|用量|数量','测量动作读 liáng；数量、用量读 liàng'),
    (r'偏好|好几|好处|挣钱|挣到|挣扎','爱好读 hào；挣钱读 zhèng；挣扎读 zhēng'),
    (r'约为|成为|为[客公他她其]','表示等于或成为读 wéi；表示替谁读 wèi'),
    (r'了当|了得|了却|了解','跨词边界尤其要核对 le 与 liǎo'),
]
def mark_context(words):
    return [dict(term=m.group(),offset=m.start(),question=meaning) for expr,meaning in CONTEXT for m in re.finditer(expr,words)]

def summary(takes):
    rows=[]
    for number,take in enumerate(takes,1):
        text=take.get('spoken','')
        cues=mark_context(text)
        direct=take.get('pronunciation',{}); end=take.get('ending_check',{})
        if end.get('attempts'):
            end=end['attempts'][end.get('selected_attempt',0)]
        rows.append(dict(line=number,shot=take.get('shot'),spoken=text,contexts=cues,
                         directed=direct.get('phones',{}),acoustic_boundary=end.get('acoustic_boundary'),
                         original_audio=take.get('file'),ending_status=end.get('status')))
    return rows

# Only these contexts identify a reading unambiguously. Ambiguous cases remain
# in the review list above, including “一行人” and both meanings of “重读”.
CLEAR_READINGS = [
    (r'重写','重写','ch0 ong2 x0 ie3'),
    (r'三行(?=数字|账目|表格|数据|字|放在一起)','三行','s0 an1 h0 ang2'),
    (r'长任务','长任务','ch0 ang2 r0 en4 w0 u4'),
    (r'净得','净得','j0 ing4 d0 e2'),
    (r'约为','约为','y0 ue1 w0 ei2'),
    (r'还得(?=等|看|要|做|去|补|付|花|按)','还得','h0 ai2 d0 ei3'),
    (r'偏好','偏好','p0 ian1 h0 ao4'),
    (r'挣到','挣到','zh0 eng4 d0 ao4'),
]
def with_context_directions(edit):
    """Apply safe defaults only to locally synthesized Mandarin, before choosing its cache."""
    from jianqiu_voice import VOICE_ID
    if (edit.get('presenter') or {}).get('recording'):return edit
    shots=[]
    for shot in edit['shots']:
        lines=[]
        for line in shot['lines']:
            if (line.get('pronunciation') is None and not line.get('audio') and not line.get('source_audio')
                    and line.get('voice',edit.get('voice',VOICE_ID))==VOICE_ID):
                words=line.get('spoken',line['text'])
                pairs={term:phones for exp,term,phones in CLEAR_READINGS if re.search(exp,words)}
                if pairs:line=dict(line,pronunciation={'phones':pairs})
            lines.append(line)
        shots.append(dict(shot,lines=lines))
    return dict(edit,shots=shots)
