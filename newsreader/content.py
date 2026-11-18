"""Readable article extraction and a dictionary with a bundled offline base."""

import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from .network import FetchError, fetch_public


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


def lookup_word(word, provider="auto"):
    from .dictionary import lookup
    return lookup(word, provider, fetch_public)
