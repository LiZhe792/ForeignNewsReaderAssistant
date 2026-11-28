"""Word counts and explainable reading-difficulty estimates."""

import math
import re

UNICODE_PATTERN = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]|[^\W\d_]+(?:['’-][^\W\d_]+)*", re.UNICODE)


def words(text):
    return UNICODE_PATTERN.findall(text)


def article_language(text, preferred='auto'):
    """Respect an explicit language; detect CJK scripts, default Latin text to English.

    Latin-script languages cannot be reliably distinguished by this lightweight
    rule. Users importing French/German/Spanish should select that language.
    """
    if preferred not in ('auto', None):
        return preferred
    if re.search(r'[\u3040-\u30ff]', text):
        return 'ja'
    if re.search(r'[\uac00-\ud7af]', text):
        return 'ko'
    if len(re.findall(r'[\u3400-\u9fff]', text)) > len(text) * .1:
        return 'zh'
    return 'en' if re.search(r'[A-Za-z]', text) else 'und'


def syllables(word):
    word = re.sub(r"[^a-z]", "", word.lower())
    if len(word) <= 3:
        return 1
    count = len(re.findall(r"[aeiouy]+", word))
    if word.endswith("e") and not word.endswith(("le", "ye")):
        count -= 1
    return max(1, count)


def assess_difficulty(text, language='auto'):
    tokens = words(text)
    count = len(tokens)
    sentences = max(1, len([s for s in re.split(r"[.!?]+(?:\s|$)", text) if s.strip()]))
    mean_sentence = count / sentences
    mean_syllables = sum(syllables(w) for w in tokens) / max(count, 1)
    ease = max(0.0, min(100.0, 206.835 - 1.015 * mean_sentence - 84.6 * mean_syllables))
    # These are rough reading bands, not an official CEFR assessment.
    level = "A2" if ease >= 85 else "B1" if ease >= 65 else "B2" if ease >= 45 else "C1" if ease >= 25 else "C2"
    non_english = article_language(text, language) != 'en'
    result = {
        "level": level, "score": round(ease, 1), "word_count": count,
        "reading_minutes": max(1, math.ceil(count / 180)),
        "average_sentence_length": round(mean_sentence, 1),
        "method": "基于 Flesch 阅读易读度估算；CEFR 标签仅供选文参考。",
    }
    if non_english:
        result.update(level='N/A',score=0,method='本篇不采用英语 Flesch/CEFR 分级；词数按 Unicode 词或东亚文字单位估算，阅读时长供参考。')
    return result
