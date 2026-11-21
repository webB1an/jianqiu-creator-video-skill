"""Public attribution of individual, sometimes time-limited, library attachments."""
import math
from pathlib import Path


def interval(value, label):
    if (not isinstance(value, list) or len(value) != 2
            or any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in value)
            or not 0 <= value[0] < value[1]):
        raise ValueError(f'{label} requires an increasing nonnegative [start, end] in seconds')
    return value


def intersect(a, b):
    if a is None:
        return b
    if b is None:
        return a
    start, end = max(a[0], b[0]), min(a[1], b[1])
    return [start, end] if start < end else False


def validate(editorial, filenames):
    if 'file_credits' not in editorial and 'credit_sources' not in editorial:
        return
    sources = editorial.get('credit_sources')
    mapping = editorial.get('file_credits')
    if (not isinstance(sources, dict) or not sources or not isinstance(mapping, dict)
            or not all(isinstance(v, str) and v.strip() for v in sources.values())):
        raise ValueError('Declare credit_sources with complete public text and file_credits for each attachment')
    if set(mapping) != set(filenames):
        raise ValueError('file_credits must cover exactly the attachments: '
                         + ', '.join(sorted(set(mapping) ^ set(filenames))))
    for name, contributors in mapping.items():
        if not isinstance(contributors, list) or not contributors:
            raise ValueError(f'Declare the contributors for {name}')
        for entry in contributors:
            source = entry.get('source') if isinstance(entry, dict) else entry
            if not isinstance(source, str) or source not in sources:
                raise ValueError(f'Unknown credit source for {name}: {source}')
            if isinstance(entry, dict):
                if set(entry) - {'source', 'range', 'stream'} or entry.get('stream') not in (None, 'picture', 'audio'):
                    raise ValueError(f'Invalid contributor metadata for {name}')
                if 'range' in entry:
                    interval(entry['range'], f'Contribution range in {name}')


def selected(editorial, source_files, role, direct_window=None):
    """Ranges are in the source attachment's clock, never the mixed stem's clock."""
    result = {}
    for file in source_files:
        name = Path(file['path']).name
        window = file.get('source_range', direct_window)
        for entry in editorial['file_credits'][name]:
            if isinstance(entry, dict):
                stream = 'picture' if role in ('picture', 'visual_card') else 'audio'
                if entry.get('stream', stream) != stream or intersect(entry.get('range'), window) is False:
                    continue
                source = entry['source']
            else:
                source = entry
            result[source] = editorial['credit_sources'][source]
    return result
