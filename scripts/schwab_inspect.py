#!/usr/bin/env python
"""Describe a Schwab export's structure without revealing its contents.

    .venv/bin/python scripts/schwab_inspect.py .local/schwab/downloads/FILE

Prints field names, preamble line shapes, per-field value shapes (digits -> 9,
letters -> a) and counts per Action / security type. Values are never printed
except Schwab's own action and type vocabulary.
"""
from collections import Counter, defaultdict
import csv
import io
import json
from pathlib import Path
import re
import sys

VOCABULARY_FIELDS = re.compile(r'^(action|security type|term|cost basis method|wash sale\??)$', re.I)


def shape(value):
    text = str(value)
    text = re.sub(r'[0-9]', '9', text)
    text = re.sub(r'[A-Za-z]', 'a', text)
    text = re.sub(r'a{2,}', 'a+', text)
    text = re.sub(r'9{4,}', '9+', text)
    return text[:40]


def describe_rows(rows, header):
    shapes, vocab = defaultdict(Counter), defaultdict(Counter)
    for row in rows:
        for name in header:
            value = row.get(name)
            if value is None:
                continue
            if VOCABULARY_FIELDS.match(name.strip()):
                vocab[name][' '.join(str(value).split())[:40]] += 1
            else:
                shapes[name][shape(value) if value != '' else '(blank)'] += 1
    return {'rows': len(rows),
            'fields': {name: dict(shapes[name].most_common(6)) for name in header if name in shapes},
            'vocabulary': {name: dict(counter.most_common(40)) for name, counter in vocab.items()}}


def inspect_json(text):
    data = json.loads(text)
    report = {'format': 'json'}
    if isinstance(data, dict):
        report['top_level'] = {key: (type(value).__name__ if not isinstance(value, (str, int, float))
                                     else shape(value)) for key, value in data.items()}
        for key, value in data.items():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                header = list(dict.fromkeys(k for row in value for k in row))
                report[key] = describe_rows(value, header)
    return report


def inspect_csv(text):
    lines = text.splitlines()
    # A header has at least three non-empty fields; titles padded with commas do not.
    header_index = next((i for i, line in enumerate(lines[:10])
                         if sum(1 for field in next(csv.reader([line]), []) if field.strip()) >= 3), None)
    if header_index is None:
        # No header-like line: never print a title or row verbatim.
        return {'format': 'csv', 'lines': len(lines), 'line_shapes': [shape(line) for line in lines[:10]]}
    report = {'format': 'csv', 'preamble': [shape(line) for line in lines[:header_index]]}
    reader = csv.DictReader(io.StringIO('\n'.join(lines[header_index:])))
    rows = list(reader)
    report['header'] = reader.fieldnames
    report.update(describe_rows(rows, reader.fieldnames or []))
    return report


def main():
    path = Path(sys.argv[1])
    text = path.read_bytes().decode('utf-8-sig')
    report = inspect_json(text) if path.suffix.lower() == '.json' else inspect_csv(text)
    print(json.dumps(report, indent=1))


if __name__ == '__main__':
    main()
