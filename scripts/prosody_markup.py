"""Inline pause and emphasis notation shared by narration and caption rendering."""
import re

PROSODY_VERSION = 'prosody-v2'
_PAUSE_TAG_RE = re.compile(r'\[(?:pause|停顿)\s+([0-9]+(?:\.[0-9]+)?)\s*s?\]', re.IGNORECASE)
_EMPH_TAG_RE = re.compile(r'\*([^*\n]{1,24})\*')


def parse_prosody_markup(text):
    """Return clean speech plus pause and emphasis positions in that text."""
    raw = str(text or '').strip()
    highlights, pauses, emphasis = [], [], []
    pieces = []
    pos = length = 0
    tags = re.compile(f'{_PAUSE_TAG_RE.pattern}|{_EMPH_TAG_RE.pattern}', re.IGNORECASE)
    for tag in tags.finditer(raw):
        prefix = raw[pos:tag.start()]
        pieces.append(prefix)
        length += len(prefix)
        if tag.group(1) is not None:
            pauses.append((length, max(0.05, min(2.5, float(tag.group(1))))))
            if prefix and prefix[-1].isascii() and prefix[-1].isalnum():
                suffix = raw[tag.end():]
                if suffix and suffix[0].isascii() and suffix[0].isalnum():
                    pieces.append(' ')
                    length += 1
        else:
            content = tag.group(2)
            term = content.strip()
            if term:
                start = length + len(content) - len(content.lstrip())
                emphasis.append((start, start + len(term)))
                if term not in highlights:
                    highlights.append(term)
            pieces.append(content)
            length += len(content)
        pos = tag.end()
        if tag.group(1) is not None and pieces[-1].endswith((' ', '\t')):
            while pos < len(raw) and raw[pos] in ' \t':
                pos += 1
    pieces.append(raw[pos:])
    return dict(clean_text=''.join(pieces), highlights=highlights, pauses=pauses, emphasis=emphasis,
                has_markup=bool(pauses or emphasis), has_pause=bool(pauses))
