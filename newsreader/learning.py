"""Word counts and explainable reading-difficulty estimates."""

import math
import re

WORD_PATTERN = re.compile(r"[A-Za-z]+(?:['’-][A-Za-z]+)*")


def words(text):
    return WORD_PATTERN.findall(text)


def syllables(word):
    word = re.sub(r"[^a-z]", "", word.lower())
    if len(word) <= 3:
        return 1
    count = len(re.findall(r"[aeiouy]+", word))
    if word.endswith("e") and not word.endswith(("le", "ye")):
        count -= 1
    return max(1, count)


def assess_difficulty(text):
    tokens = words(text)
    count = len(tokens)
    sentences = max(1, len([s for s in re.split(r"[.!?]+(?:\s|$)", text) if s.strip()]))
    mean_sentence = count / sentences
    mean_syllables = sum(syllables(w) for w in tokens) / max(count, 1)
    ease = max(0.0, min(100.0, 206.835 - 1.015 * mean_sentence - 84.6 * mean_syllables))
    # These are rough reading bands, not an official CEFR assessment.
    level = "A2" if ease >= 85 else "B1" if ease >= 65 else "B2" if ease >= 45 else "C1" if ease >= 25 else "C2"
    return {
        "level": level, "score": round(ease, 1), "word_count": count,
        "reading_minutes": max(1, math.ceil(count / 180)),
        "average_sentence_length": round(mean_sentence, 1),
        "method": "基于 Flesch 阅读易读度估算；CEFR 标签仅供选文参考。",
    }
