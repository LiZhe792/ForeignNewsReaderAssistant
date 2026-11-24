"""Extract local files into an editable preview; uploads are never saved to disk."""

import io
import posixpath
import re
import zipfile
from pathlib import PurePosixPath
from threading import Lock
from xml.etree import ElementTree

from bs4 import BeautifulSoup, NavigableString
from charset_normalizer import from_bytes

MAX_TEXT = 100_000
MAX_PAGES = 30
MAX_PIXELS = 25_000_000
MAX_ARCHIVE = 60 * 1024 * 1024
_ocr = None
_ocr_lock = Lock()


def decode(data):
    encodings = ("utf-16", "utf-8-sig") if data.startswith((b"\xff\xfe", b"\xfe\xff")) else ("utf-8-sig",)
    for encoding in encodings:
        try:
            return data.decode(encoding)
        except UnicodeError:
            pass
    result = from_bytes(data).best()
    if result is None:
        raise ValueError("无法识别文件编码，请另存为 UTF-8 后重试。")
    return str(result)


def html_text(raw):
    soup = BeautifulSoup(raw, "html.parser")
    for element in soup.select("script, style, nav, footer, header, aside, form, noscript, iframe, [hidden]"):
        element.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    blocks = {"p", "div", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre", "tr"}
    parts = []
    def visit(node):
        if isinstance(node, NavigableString):
            parts.append(str(node))
            return
        if node.name in blocks:
            parts.append("\n\n")
        if node.name == "br":
            parts.append("\n")
        for child in node.children:
            visit(child)
        if node.name in blocks:
            parts.append("\n\n")
    visit(root)
    return "\n".join(re.sub(r"[ \t\f\v]+", " ", line).strip() for line in "".join(parts).splitlines())


def archive(data):
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        infos = z.infolist()
        if len(infos) > 3000 or sum(i.file_size for i in infos) > MAX_ARCHIVE:
            raise ValueError("文件解压后过大，无法导入。")
        if any(i.flag_bits & 1 for i in infos):
            raise ValueError("不支持加密文件，请先解除加密。")
        return z
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError("文件格式无效或已损坏。") from exc


def xml(data):
    # Office/EPUB XML does not need DTDs or entity declarations.
    if re.search(br"<!\s*(DOCTYPE|ENTITY)", data, re.I):
        raise ValueError("文件包含不受支持的 XML 声明。")
    try:
        return ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        raise ValueError("文档结构无效或已损坏。") from exc


def docx_text(data):
    with archive(data) as z:
        try:
            root = xml(z.read("word/document.xml"))
        except KeyError as exc:
            raise ValueError("DOCX 缺少正文。") from exc
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        paragraphs = []
        for p in root.iter(ns + "p"):
            parts = []
            for node in p.iter():
                if node.tag == ns + "t":
                    parts.append(node.text or "")
                elif node.tag == ns + "tab":
                    parts.append("\t")
                elif node.tag in (ns + "br", ns + "cr"):
                    parts.append("\n")
            if "".join(parts).strip():
                paragraphs.append("".join(parts))
        return "\n\n".join(paragraphs)


def epub_text(data):
    with archive(data) as z:
        try:
            container = xml(z.read("META-INF/container.xml"))
            rootfile = next(e for e in container.iter() if e.tag.endswith("}rootfile"))
            opf_path = rootfile.attrib["full-path"]
            package = xml(z.read(opf_path))
            manifest = {e.attrib.get("id"): e.attrib for e in package.iter() if e.tag.endswith("}item")}
            spine = [e.attrib.get("idref") for e in package.iter() if e.tag.endswith("}itemref")]
            chapters = []
            for item_id in spine:
                item = manifest.get(item_id, {})
                if item.get("media-type") not in ("application/xhtml+xml", "text/html"):
                    continue
                from urllib.parse import unquote, urlsplit
                href = urlsplit(item.get("href", ""))
                if href.scheme or href.netloc:
                    continue
                path = posixpath.normpath(posixpath.join(posixpath.dirname(opf_path), unquote(href.path)))
                if path.startswith("../") or path.startswith("/"):
                    raise ValueError("EPUB 包含无效路径。")
                chapters.append(html_text(z.read(path)))
            return "\n\n".join(chapters)
        except (KeyError, StopIteration, zipfile.BadZipFile) as exc:
            raise ValueError("EPUB 结构无效或缺少正文。") from exc


def recognize(image):
    global _ocr
    try:
        import numpy as np
        from PIL import Image, ImageOps
        from rapidocr_onnxruntime import RapidOCR
        Image.MAX_IMAGE_PIXELS = MAX_PIXELS
        with Image.open(io.BytesIO(image)) as opened:
            if opened.width * opened.height > MAX_PIXELS:
                raise ValueError("图片尺寸过大，请缩小后重试。")
            img = ImageOps.exif_transpose(opened).convert("RGB")
            with _ocr_lock:
                if _ocr is None:
                    _ocr = RapidOCR()
                result, _ = _ocr(np.asarray(img)[:, :, ::-1].copy())
        return "\n".join(line[1] for line in (result or []))
    except ValueError:
        raise
    except ImportError as exc:
        raise ValueError("OCR 组件未安装，请重新安装项目依赖。") from exc
    except Exception as exc:
        raise ValueError("图片无法识别或格式损坏，请尝试清晰的图片。") from exc


def pdf_text(data):
    import pymupdf
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
        with doc:
            if doc.needs_pass:
                raise ValueError("PDF 已加密，请先解除密码保护。")
            if len(doc) > MAX_PAGES:
                raise ValueError(f"PDF 最多支持 {MAX_PAGES} 页，请拆分后导入。")
            pages, scanned = [], 0
            for page in doc:
                text = page.get_text(sort=True).strip()
                if len(re.findall(r"[A-Za-z]", text)) < 20:
                    if page.rect.width <= 0 or page.rect.height <= 0:
                        raise ValueError("PDF 页面尺寸无效。")
                    scale = min(2, (MAX_PIXELS / (page.rect.width * page.rect.height)) ** .5)
                    image = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes("png")
                    text = recognize(image)
                    scanned += 1
                pages.append(text)
            return "\n\n".join(pages), scanned
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("PDF 无法读取，请检查文件是否损坏。") from exc


def extract_file(filename, data):
    name = PurePosixPath(filename.replace("\\", "/")).name
    suffix = PurePosixPath(name).suffix.lower()
    title = PurePosixPath(name).stem[:250] or "导入文章"
    if not data:
        raise ValueError("文件为空。")
    scanned = 0
    if suffix == ".txt":
        content = decode(data)
    elif suffix in (".md", ".markdown"):
        import markdown
        content = html_text(markdown.markdown(decode(data), extensions=["extra"]))
    elif suffix in (".html", ".htm"):
        content = html_text(decode(data))
    elif suffix == ".docx":
        content = docx_text(data)
    elif suffix == ".epub":
        content = epub_text(data)
    elif suffix == ".pdf":
        content, scanned = pdf_text(data)
    elif suffix in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"):
        content = recognize(data)
        scanned = 1
    else:
        raise ValueError("不支持该格式，请选择 TXT、Markdown、HTML、PDF、DOCX、EPUB 或图片。")
    content = re.sub(r"\n{3,}", "\n\n", content.replace("\r\n", "\n").replace("\r", "\n")).strip()
    if not content:
        raise ValueError("未提取到文字，请检查文件或尝试更清晰的扫描图片。")
    if len(content) > MAX_TEXT:
        raise ValueError("提取的正文超过 10 万字符，请拆分文件后导入。")
    return {"title": title, "content": content, "source": "文件导入", "ocr_pages": scanned}
