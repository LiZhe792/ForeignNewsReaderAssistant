"""Readable article extraction and a dictionary with a bundled offline base."""

import json
import re
from pathlib import Path
from urllib.parse import quote, urlsplit

from bs4 import BeautifulSoup

from .db import get_db, now
from .network import FetchError, fetch_public

LEXICON = json.loads((Path(__file__).resolve().parent.parent / "data" / "dictionary.json").read_text(encoding="utf-8"))


def extract_article(html, url):
    soup = BeautifulSoup(html, "html.parser")
    title_node = soup.find("meta", property="og:title")
    title = title_node.get("content", "") if title_node else ""
    if not title:
        heading = soup.find("h1") or soup.title
        title = heading.get_text(" ", strip=True) if heading else urlsplit(url).hostname
    for node in soup.select("script, style, nav, footer, header, aside, form, noscript, iframe, button, [hidden]"):
        node.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    candidates = [root] + root.select("div, section")
    if not soup.find("article"):
        def score(node):
            paragraphs = node.find_all("p", recursive=True)
            return sum(len(p.get_text(" ", strip=True)) for p in paragraphs) - len(node.find_all("a")) * 40
        root = max(candidates, key=score)
    paragraphs = []
    for paragraph in root.find_all(["p", "h2", "h3"]):
        text = re.sub(r"\s+", " ", paragraph.get_text(" ", strip=True))
        if len(text) >= 25:
            paragraphs.append(text)
    content = "\n\n".join(paragraphs)
    if len(re.findall(r"[A-Za-z]+", content)) < 40:
        raise FetchError("未找到足够的英文正文。该网站可能需要登录或动态加载，请复制正文后粘贴导入。")
    return {"title": title[:250], "content": content[:100000], "source": urlsplit(url).hostname, "source_url": url}


def import_url(url):
    raw, content_type, final_url = fetch_public(url)
    if content_type and not any(t in content_type.lower() for t in ("text/html", "application/xhtml")):
        raise FetchError("该链接不是 HTML 文章页面，请使用粘贴导入。")
    return extract_article(raw, final_url)


def base_forms(word):
    candidates = [word]
    irregular = {"grew": "grow", "grown": "grow", "built": "build", "better": "good", "children": "child", "cities": "city", "people": "person"}
    if word in irregular:
        candidates.append(irregular[word])
    if word.endswith("ies"):
        candidates.append(word[:-3] + "y")
    if word.endswith("s"):
        candidates += [word[:-1], word[:-2]]
    for ending in ("ing", "ed"):
        if word.endswith(ending):
            stem = word[:-len(ending)]
            candidates += [stem, stem + "e"]
            if len(stem) > 2 and stem[-1] == stem[-2]:
                candidates.append(stem[:-1])
    return [c for c in candidates if c]


def lookup_word(word):
    word = word.lower().strip().replace("’", "'")
    if not re.fullmatch(r"[a-z]+(?:['-][a-z]+)*", word) or len(word) > 60:
        raise ValueError("请输入一个英文单词（可含连字符或撇号）。")
    for candidate in base_forms(word):
        if candidate in LEXICON:
            entry = LEXICON[candidate]
            return {"word": word, "lemma": candidate, "phonetic": entry[0], "part_of_speech": entry[1],
                    "meaning": entry[2], "example": entry[3] if len(entry) > 3 else "",
                    "source": "内置英汉词库", "found": True}
    db = get_db()
    cached = db.execute("SELECT result FROM dictionary_cache WHERE word=?", (word,)).fetchone()
    if cached:
        return {**json.loads(cached["result"]), "cached": True}
    try:
        raw, _, _ = fetch_public("https://api.dictionaryapi.dev/api/v2/entries/en/" + quote(word), max_bytes=300000)
        entries = json.loads(raw)
        entry = entries[0]
        meanings = entry.get("meanings", [])
        definitions = [d.get("definition", "") for m in meanings for d in m.get("definitions", [])][:3]
        if not definitions:
            raise ValueError("No definitions")
        phonetic = entry.get("phonetic") or next((p.get("text", "") for p in entry.get("phonetics", []) if p.get("text")), "")
        result = {
            "word": word, "lemma": entry.get("word", word), "phonetic": phonetic,
            "part_of_speech": meanings[0].get("partOfSpeech", "") if meanings else "",
            "meaning": "\n".join(definitions), "example": next((d["example"] for m in meanings for d in m.get("definitions", []) if d.get("example")), ""),
            "source": "Free Dictionary API · 英英释义", "found": True,
        }
        db.execute("INSERT OR REPLACE INTO dictionary_cache VALUES (?, ?, ?)", (word, json.dumps(result, ensure_ascii=False), now()))
        db.commit()
        return result
    except (FetchError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        return {"word": word, "lemma": word, "phonetic": "", "part_of_speech": "", "meaning": "", "example": "",
                "source": "未找到释义或网络不可用", "found": False,
                "message": "内置词库未收录，在线词典暂未返回结果。你可以填写释义并加入生词本。"}
