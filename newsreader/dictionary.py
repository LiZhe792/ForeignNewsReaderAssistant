"""Offline bilingual lookup and an explicitly selectable online dictionary.

Network fetching is injected by content.lookup_word, keeping the existing public
interface and allowing deterministic tests without contacting external services.
"""
import json
import re
from pathlib import Path
from urllib.parse import quote

from .db import get_db, now
from .network import FetchError

DATA = Path(__file__).resolve().parent.parent / 'data'
CORE = json.loads((DATA / 'dictionary.json').read_text(encoding='utf-8'))
EXTRA = json.loads((DATA / 'dictionary-extra.json').read_text(encoding='utf-8'))['entries']
NOTES = json.loads((DATA / 'dictionary-knowledge.json').read_text(encoding='utf-8'))
TOKEN = r"[a-z]+(?:['-][a-z]+)*"
TERM = re.compile(TOKEN + r'(?: ' + TOKEN + r'){0,5}')
PROVIDERS = ('auto', 'offline', 'free')
FORM_LABELS = {'p': '过去式', 'd': '过去分词', 'i': '现在分词', '3': '第三人称单数',
               'r': '比较级', 't': '最高级', 's': '复数'}
TAG_LABELS = {'zk': '中考', 'gk': '高考', 'cet4': 'CET-4', 'cet6': 'CET-6',
              'ky': '考研', 'ielts': 'IELTS', 'toefl': 'TOEFL', 'gre': 'GRE'}


def normalize_term(value):
    if not isinstance(value, str) or len(value) > 120:
        raise ValueError('请输入不超过 60 个字符的英文单词或短语。')
    term = re.sub(r'\s+', ' ', value.strip().lower().replace('’', "'"))
    if len(term) > 60 or not TERM.fullmatch(term):
        raise ValueError('请输入 1–6 个英文单词组成的单词或短语（可含连字符或撇号）。')
    return term


def exchange(word):
    result = {}
    for field in EXTRA.get(word, {}).get('exchange', '').split('/'):
        key, sep, value = field.partition(':')
        if sep:
            result[key] = [v for v in value.split(',') if re.fullmatch(TOKEN, v)]
    return result


# Only attested inflections are indexed: indiscriminately removing "s" would
# incorrectly turn news into new, and removing "ing" can invent nonwords.
INFLECTIONS = {}
for _lemma in EXTRA:
    for _kind, _forms in exchange(_lemma).items():
        if _kind in FORM_LABELS:
            for _form in _forms:
                INFLECTIONS.setdefault(_form, []).append(_lemma)
FAMILIES = {word: group for group in NOTES['families'] for word in group}


def candidates(term):
    yield term
    if term.endswith("'s"):
        yield term[:-2]
    # Direct entries retain their own senses (e.g. saw is also a noun). For an
    # absent inflection, use the dictionary's attested headword relationships.
    for lemma in INFLECTIONS.get(term, []):
        yield lemma
    # Restore an inflected verb in an attested phrase, not arbitrary sentences.
    if ' ' in term:
        first, rest = term.split(' ', 1)
        for lemma in INFLECTIONS.get(first, []):
            yield lemma + ' ' + rest


def offline_entry(term):
    for lemma in dict.fromkeys(candidates(term)):
        if lemma in NOTES['phrases']:
            meaning, example = NOTES['phrases'][lemma]
            return lemma, {'phonetic': '', 'part_of_speech': 'phrase', 'meaning': meaning,
                           'example': example, 'source': 'Margin 离线短语与习语'}
        if lemma in CORE:
            row = CORE[lemma]
            return lemma, {'phonetic': row[0], 'part_of_speech': row[1], 'meaning': row[2],
                           'example': row[3] if len(row) > 3 else '', 'source': '内置英汉词库 · Margin'}
        if lemma in EXTRA:
            row = EXTRA[lemma]
            # Entries such as cities sometimes contain only an inflection gloss.
            bases = exchange(lemma).get('0', [])
            if bases and lemma not in NOTES['relations'] and lemma not in NOTES['pronunciations']:
                base = bases[0]
                if base != lemma and (row['bnc'] in ('', '0')) and (base in CORE or base in EXTRA):
                    return offline_entry(base)
            meaning = row['translation'][:3000]
            pos = list(dict.fromkeys(re.findall(r'(?:^|\n)([a-z]+\.)', meaning)))
            return lemma, {'phonetic': '/' + row['phonetic'].strip('/') + '/' if row['phonetic'] else '',
                           'part_of_speech': ' / '.join(pos)[:100], 'meaning': meaning,
                           'example': '', 'source': 'ECDICT 离线英汉词库'}
    return term, None


def knowledge(lemma):
    row = EXTRA.get(lemma, {})
    notes = NOTES['relations'].get(lemma, {})
    group = FAMILIES.get(lemma, [])
    result = {key: notes.get(key, []) for key in ('synonyms', 'antonyms', 'collocations', 'roots')}
    result.update(
        family=[word for word in group if word != lemma],
        confusables=[entry for entry in NOTES['confusables'] if lemma in entry['words']],
        frequency={key: int(row[key]) if str(row.get(key, '')).isdigit() and int(row[key]) > 0 else None
                   for key in ('bnc', 'frq')},
        levels=[label for key, label in TAG_LABELS.items() if key in row.get('tag', '').split()],
        oxford_core=row.get('oxford') == '1',
        collins_stars=int(row['collins']) if str(row.get('collins', '')).isdigit() else 0,
        forms=[{'label': FORM_LABELS[key], 'word': form} for key, forms in exchange(lemma).items()
               if key in FORM_LABELS for form in forms if form != lemma],
    )
    return result


def strings(value, limit=12):
    return list(dict.fromkeys(item[:80] for item in value if isinstance(item, str) and item.strip()))[:limit] if isinstance(value, list) else []


def text(value, limit=1000):
    return value[:limit] if isinstance(value, str) else ''


def online_entry(term, fetch):
    db = get_db()
    # Provider and schema version are part of the key. Old cached definitions
    # must never override an explicitly chosen offline dictionary.
    key = 'v7:free:' + term
    cached = db.execute('SELECT result FROM dictionary_cache WHERE word=?', (key,)).fetchone()
    if cached:
        try:
            result = json.loads(cached['result'])
            if (isinstance(result, dict) and result.get('found') is True and
                    isinstance(result.get('meaning'), str) and result['meaning'] and
                    isinstance(result.get('phonetics'), dict) and
                    all(isinstance(result['phonetics'].get(region), str) for region in ('uk', 'us', 'other')) and
                    all(isinstance(result.get(field), str) for field in
                        ('phonetic', 'part_of_speech', 'example', 'source')) and
                    all(isinstance(result.get(k), list) for k in ('synonyms', 'antonyms'))):
                return {**result, 'cached': True}
        except (ValueError, TypeError):
            pass
    from .data_management import offline
    if offline():
        return None
    try:
        raw, _, _ = fetch('https://api.dictionaryapi.dev/api/v2/entries/en/' + quote(term, safe=''), max_bytes=300000)
        entries = json.loads(raw)
        if not isinstance(entries, list):
            raise ValueError('Invalid dictionary response')
        definitions, synonyms, antonyms, parts = [], [], [], []
        phonetics = {'uk': '', 'us': '', 'other': ''}
        example = ''
        for entry in entries[:8]:
            if not isinstance(entry, dict):
                continue
            phonetics['other'] = phonetics['other'] or text(entry.get('phonetic'), 150)
            sounds = entry.get('phonetics')
            for sound in sounds[:20] if isinstance(sounds, list) else []:
                if not isinstance(sound, dict):
                    continue
                ipa = text(sound.get('text'), 150)
                audio = text(sound.get('audio'))
                # A regional label is shown only when the source identifies it.
                region = 'uk' if re.search(r'(?:-uk|_gb_)[\W_\d]', audio) else 'us' if re.search(r'(?:-us|_us_)[\W_\d]', audio) else 'other'
                phonetics[region] = phonetics[region] or ipa
            meanings = entry.get('meanings')
            for meaning in meanings[:12] if isinstance(meanings, list) else []:
                if not isinstance(meaning, dict):
                    continue
                parts.append(text(meaning.get('partOfSpeech'), 50))
                synonyms.extend(strings(meaning.get('synonyms')))
                antonyms.extend(strings(meaning.get('antonyms')))
                senses = meaning.get('definitions')
                for sense in senses[:12] if isinstance(senses, list) else []:
                    if not isinstance(sense, dict):
                        continue
                    definition = text(sense.get('definition'))
                    if definition.strip():
                        definitions.append(definition)
                    example = example or text(sense.get('example'))
                    synonyms.extend(strings(sense.get('synonyms')))
                    antonyms.extend(strings(sense.get('antonyms')))
        if not definitions:
            raise ValueError('No definitions')
        result = {'found': True, 'meaning': '\n'.join(dict.fromkeys(definitions))[:3000],
                  'phonetic': phonetics['uk'] or phonetics['us'] or phonetics['other'],
                  'phonetics': phonetics, 'part_of_speech': ' / '.join(dict.fromkeys(filter(None, parts)))[:100],
                  'example': example, 'source': 'Free Dictionary API · 英英释义',
                  'synonyms': strings(synonyms), 'antonyms': strings(antonyms)}
        db.execute('INSERT OR REPLACE INTO dictionary_cache VALUES (?, ?, ?)',
                   (key, json.dumps(result, ensure_ascii=False), now()))
        db.commit()
        return result
    except (FetchError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        return None


def lookup(term, provider, fetch):
    term = normalize_term(term)
    if provider not in PROVIDERS:
        raise ValueError('请选择有效的词典。')
    lemma, local = offline_entry(term)
    bases = exchange(lemma).get('0', [])
    reference = bases[0] if bases and (bases[0] in CORE or bases[0] in EXTRA) else lemma
    if lemma not in CORE:
        lemma = reference
    details = knowledge(lemma)
    pronunciation = NOTES['pronunciations'].get(lemma, ['', ''])
    result = {'word': term, 'lemma': lemma, 'provider': provider, 'found': False,
              'phonetic': '', 'part_of_speech': '', 'meaning': '', 'example': '',
              'source': '未找到释义', 'knowledge': details,
              'phonetics': {'uk': pronunciation[0], 'us': pronunciation[1], 'other': ''}}
    if provider in ('auto', 'offline') and local:
        result.update(local, found=True)
        result['phonetics']['other'] = local['phonetic'] if not any(pronunciation) else ''
        return result
    if provider != 'offline':
        remote = online_entry(term, fetch)
        if remote:
            result.update(remote)
            for relation in ('synonyms', 'antonyms'):
                details[relation] = strings(remote.pop(relation, []) + details[relation])
            for region in ('uk', 'us'):
                result['phonetics'][region] = result['phonetics'][region] or {'uk': pronunciation[0], 'us': pronunciation[1]}[region]
            return result
    from .data_management import offline
    if offline():
        result['message']='离线模式下，所选词典没有可用的本地词条或缓存。可切换离线英汉，或手动填写释义。'
        return result
    result['message'] = ('离线词库未收录此词或短语，可切换自动或在线词典，或手动填写释义。'
                         if provider == 'offline' else '在线词典未返回释义或网络不可用，可切换离线词典、重试或手动填写释义。')
    return result
