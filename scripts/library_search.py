"""Literal-first library ranking with a shared subject/action vocabulary."""
import json
from pathlib import Path
import re
import unicodedata

RULES = json.loads(Path(__file__).with_name('library-search.json').read_text())


def normalize(text):
    return unicodedata.normalize('NFKC', str(text)).lower().strip()


def query_plan(terms, records=()):
    query = normalize(' '.join(terms))
    if any(word in query for word in RULES['negative_qualifiers']):
        return []
    for phrase in RULES['phrases']:
        query = re.sub(phrase['pattern'], phrase['term'], query)
    concepts = RULES['concepts']
    vocabulary = [(normalize(term), index) for index, concept in enumerate(concepts)
                  for term in [*concept['terms'], *concept.get('forms', [])]]
    keywords = {term for row in records for term in normalize(row.get('keywords', '')).split()
                if 2 <= len(term) <= 32}
    vocabulary.extend((term, 'literal') for term in sorted(keywords))
    vocabulary.extend((normalize(term), None) for term in RULES['fillers'])
    # 焊球 (a visible part) must remain a word when 焊 (an action) is also known.
    def priority(entry):
        word, source = entry
        if source is None:
            return 1, -len(word)
        if source == 'literal':
            return 2, -len(word)
        return (3 if len(word) == 1 else 0), -len(word)

    vocabulary.sort(key=priority)
    selected = set()
    known_literals = []
    remaining = []
    position = 0
    while position < len(query):
        match = next(((term, index) for term, index in vocabulary
                      if query.startswith(term, position)), None)
        if match:
            term, index = match
            if index == 'literal':
                known_literals.append(term)
            elif index is not None:
                selected.add(index)
            remaining.append(' ')
            position += len(term)
        else:
            remaining.append(query[position])
            position += 1
    if not selected and not known_literals:
        return []
    # Unknown subjects and qualifiers are mandatory; no verb-only suggestions.
    literals = known_literals + re.findall(r'[a-z0-9_\u3400-\u9fff]+', ''.join(remaining))
    groups = [(list(map(normalize, concepts[i]['terms'])), concepts[i].get('evidence') == 'card')
              for i in sorted(selected)]
    return groups + [([word], False) for word in dict.fromkeys(literals)]


def query_groups(terms, records=()):
    return [words for words, _ in query_plan(terms, records)]


def group_score(record, groups):
    return sum(max((sum(weight for field, weight in RULES['weights'].items()
                        if term in record.get(field, '')) for term in words), default=0)
               for words, _ in groups)


def rank(records, terms):
    """Preserve literal AND matches; ground described actions in the card itself."""
    terms = [normalize(term) for term in terms if normalize(term)]
    if not terms:
        return {'mode': 'all', 'ids': [row['id'] for row in records]}
    records = [{**row, **{field: normalize(row.get(field, '')) for field in RULES['weights']}}
               for row in records]
    candidates = [row for row in records if all(term in row['text'] for term in terms)]
    groups = [([term], False) for term in terms]
    mode = 'exact'
    if not candidates:
        groups = query_plan(terms, records)
        candidates = []
        for row in records:
            card = row['title'] + ' ' + row['keywords']
            if groups and all(any(term in (card if on_card else row['text']) for term in words)
                              for words, on_card in groups):
                candidates.append(row)
        mode = 'related'
    candidates.sort(key=lambda row: (-group_score(row, groups), row['id']))
    if mode == 'related':
        candidates = candidates[:RULES['fallback_limit']]
    return {'mode': mode, 'ids': [row['id'] for row in candidates]}
