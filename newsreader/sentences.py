"""Deterministic reading units, retaining every original character in each paragraph."""
import re

ABBREVIATIONS = {"mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "st.", "vs.",
                 "etc.", "e.g.", "i.e.", "fig.", "no.", "dept.", "inc.", "approx."}
STARTERS = {"The", "This", "That", "These", "Those", "It", "He", "She", "They", "We", "However", "But"}
BOUNDARY = re.compile(r"[.!?…]+[\"”’')\]]*(?:\s+|$)")


def split_sentences(text):
    start, parts = 0, []
    for match in BOUNDARY.finditer(text):
        ending = text[start:match.end()].rstrip().rstrip("\"”’')]")
        token = re.search(r"([A-Za-z][A-Za-z.]*)\.$", ending)
        following = text[match.end():].split(maxsplit=1)
        if following and token:
            abbreviation = token.group(0)
            if abbreviation.lower() in ABBREVIATIONS or len(abbreviation) == 2:
                continue
            if re.fullmatch(r"(?:[A-Za-z]\.){2,}", abbreviation):
                next_word = following[0].lstrip('\"“\'‘')
                ends_time = abbreviation.lower() in ("a.m.", "p.m.") and next_word[:1].isupper()
                if next_word not in STARTERS and not ends_time:
                    continue
        if following and ending.endswith("...") and following[0][:1].islower():
            continue
        parts.append(text[start:match.end()])
        start = match.end()
    if start < len(text):
        parts.append(text[start:])
    # Very long unpunctuated material is still playable, without dropping words.
    result = []
    for part in parts:
        while len(part) > 1800:
            space = part.rfind(" ", 900, 1800)
            end = space + 1 if space >= 0 else 1800
            result.append(part[:end])
            part = part[end:]
        if part:
            result.append(part)
    return result


def article_segments(content):
    result = []
    for paragraph, text in enumerate(p.strip() for p in re.split(r"\n\s*\n|\n", content) if p.strip()):
        for sentence, part in enumerate(split_sentences(text)):
            result.append({"index": len(result), "paragraph": paragraph, "sentence": sentence, "text": part})
    return result
