"""Rebuild the bundled ECDICT subset from a local checkout, without network access.

Usage: python data/build_dictionary.py /path/to/ECDICT
The upstream commit, selection rule and license are recorded alongside the data.
"""
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parent
SOURCE = Path(sys.argv[1])
LIMIT = 12000
TERM = re.compile(r"[a-z]+(?:['-][a-z]+)*")


def rank(row):
    values = [int(row[key]) for key in ('bnc', 'frq') if row[key].isdigit() and int(row[key]) > 0]
    return min(values, default=10**9), row['word']


with (SOURCE / 'ecdict.csv').open(encoding='utf-8', newline='') as stream:
    rows = {row['word']: row for row in csv.DictReader(stream)
            if TERM.fullmatch(row['word']) and row['translation'].strip()}
selected = sorted(rows.values(), key=rank)[:LIMIT]
notes = json.loads((TARGET / 'dictionary-knowledge.json').read_text(encoding='utf-8'))
related = set(notes['relations']) | set(notes['pronunciations'])
related.update(word for group in notes['families'] for word in group)
related.update(word for group in notes['confusables'] for word in group['words'])
related.update(word for phrase in notes['phrases'] for word in phrase.split())
selected_by_word = {row['word']: row for row in selected}
selected_by_word.update({word: rows[word] for word in related if word in rows})
entries = {}
for row in selected_by_word.values():
    entries[row['word']] = {key: row[key].replace('\\n', '\n') for key in
                           ('phonetic', 'translation', 'pos', 'tag', 'bnc', 'frq', 'collins', 'oxford', 'exchange')}
revision = subprocess.check_output(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD'], text=True).strip()
output = {
    'source': 'https://github.com/skywind3000/ECDICT', 'revision': revision,
    'selection': '12,000 lowercase headwords by minimum positive BNC/frq rank (ties by headword), plus headwords used by curated learning notes.',
    'entries': dict(sorted(entries.items())),
}
(TARGET / 'dictionary-extra.json').write_text(json.dumps(output, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
(TARGET / 'ECDICT-LICENSE.txt').write_text((SOURCE / 'LICENSE').read_text(encoding='utf-8'), encoding='utf-8')
print(f'Wrote {len(entries):,} ECDICT headwords at {revision}.')
