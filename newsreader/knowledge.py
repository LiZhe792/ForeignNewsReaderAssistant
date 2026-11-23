"""Local annotations and personal knowledge, anchored to verified original text."""

import hashlib
import json
import re

from flask import Blueprint, abort, jsonify, request

from .db import get_db, now

knowledge = Blueprint("knowledge", __name__, url_prefix="/api")
KINDS = {"highlight": "高亮", "bookmark": "段落书签", "annotation": "页边批注", "sentence": "句子收藏",
         "grammar": "语法点", "note": "主题笔记", "expression": "表达素材", "card": "知识卡片"}
COLORS = {"yellow": "黄色", "green": "绿色", "blue": "蓝色", "pink": "粉色"}
ANCHORED = ("highlight", "bookmark", "annotation", "sentence")


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def payload():
    value = request.get_json()
    if not isinstance(value, dict):
        raise ValueError("请使用 JSON 对象提交记录。")
    return value


def text(data, key, limit, required=False):
    value = data.get(key, "")
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise ValueError(f"{key} 必须是不超过 {limit} 字符的文本。")
    value = value.strip()
    if required and not value:
        raise ValueError("请填写标题或正文。")
    return value


def token(data):
    value = data.get("request_id")
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9-]{16,80}", value):
        raise ValueError("请求标识无效，请重试。")
    return value


def record(table, item_id):
    row = get_db().execute(f"SELECT * FROM {table} WHERE id=?", (item_id,)).fetchone()
    if row is None:
        abort(404)
    return dict(row)


def paragraphs(content):
    return [part.strip() for part in re.split(r"\n\s*\n|\n", content) if part.strip()]


def verify_anchors(raw, content):
    if not isinstance(raw, list) or not 1 <= len(raw) <= 20:
        raise ValueError("请选择不超过 20 个段落的原文。")
    parts = paragraphs(content)
    result, previous, total = [], -1, 0
    for anchor in raw:
        if not isinstance(anchor, dict) or set(anchor) != {"paragraph", "start", "end", "quote"}:
            raise ValueError("原文位置格式无效。")
        index, start, end, quote = (anchor[key] for key in ("paragraph", "start", "end", "quote"))
        if any(type(value) is not int for value in (index, start, end)) or not 0 <= index < len(parts) or index <= previous:
            raise ValueError("原文段落无效或顺序不正确。")
        encoded = parts[index].encode("utf-16-le")
        if not 0 <= start < end <= len(encoded) // 2 or not isinstance(quote, str) or not quote.strip():
            raise ValueError("请重新选择有效的原文范围。")
        try:
            exact = encoded[start * 2:end * 2].decode("utf-16-le")
        except UnicodeError:
            raise ValueError("原文范围截断了一个字符，请重新选择。") from None
        if exact != quote:
            raise ValueError("摘录与原文位置不一致，请重新选择。")
        previous = index
        total += len(quote)
        result.append({"paragraph": index, "start": start, "end": end, "quote": quote})
    if total > 6000:
        raise ValueError("一次最多摘录 6000 字符，请缩小选区。")
    return result


def tags(data):
    raw = data.get("tags", [])
    if not isinstance(raw, list) or len(raw) > 12:
        raise ValueError("每条记录最多 12 个标签。")
    values = [text({"tag": value}, "tag", 24, True).casefold() for value in raw]
    return list(dict.fromkeys(values))


def fields(data, kind):
    color = data.get("color", "yellow")
    if not isinstance(color, str) or color not in COLORS:
        raise ValueError("请选择支持的高亮颜色。")
    result = {"title": text(data, "title", 200, True), "content": text(data, "content", 10000),
              "example": text(data, "example", 3000), "color": color, "tags": dump(tags(data))}
    if kind in ("annotation", "note", "card") and not result["content"]:
        raise ValueError("请填写批注、笔记正文或卡片答案。")
    return result


def item_json(row, article=None):
    item = dict(row)
    item.pop("request_id", None)
    item.pop("source_key", None)
    item["tags"] = json.loads(item["tags"])
    item["anchors"] = json.loads(item["anchors"])
    item["label"] = KINDS[item["kind"]]
    item["quote"] = "\n\n".join(anchor["quote"] for anchor in item["anchors"])
    item["source_state"] = "none"
    if item["anchors"]:
        if article is None and item["article_id"] is not None:
            article = get_db().execute("SELECT * FROM articles WHERE id=?", (item["article_id"],)).fetchone()
        item["source_state"] = "deleted" if article is None else "available"
        if article is not None:
            try:
                verify_anchors(item["anchors"], article["content"])
            except ValueError:
                item["source_state"] = "changed"
    return item


def pagination():
    value = request.args.get("before", str(2**62))
    if not value.isascii() or not value.isdigit() or len(value) > 19:
        raise ValueError("分页参数无效。")
    return min(int(value), 2**62)


def version_matches(data, row):
    version = data.get("version")
    if type(version) is not int or version < 0:
        raise ValueError("记录版本无效，请重新打开。")
    return version == row["version"]


@knowledge.get("/knowledge/options")
def options():
    rows = get_db().execute("SELECT tags FROM knowledge_items").fetchall()
    all_tags = sorted({tag for row in rows for tag in json.loads(row["tags"])})
    return jsonify(kinds=KINDS, colors=COLORS, tags=all_tags)


@knowledge.get("/articles/<int:article_id>/knowledge")
def article_items(article_id):
    article = record("articles", article_id)
    rows = get_db().execute("SELECT * FROM knowledge_items WHERE article_id=? ORDER BY id", (article_id,)).fetchall()
    return jsonify(items=[item_json(row, article) for row in rows])


@knowledge.route("/knowledge/items", methods=["GET", "POST"])
def items():
    db = get_db()
    if request.method == "GET":
        conditions, values = ["id<?"], [pagination()]
        kind, tag = request.args.get("kind", ""), request.args.get("tag", "").strip().casefold()
        if kind:
            if kind not in KINDS:
                raise ValueError("记录类型无效。")
            conditions.append("kind=?")
            values.append(kind)
        if tag:
            if len(tag) > 24:
                raise ValueError("标签过长。")
            conditions.append("EXISTS (SELECT 1 FROM json_each(knowledge_items.tags) WHERE value=?)")
            values.append(tag)
        query = request.args.get("q", "").strip()
        if len(query) > 200:
            raise ValueError("搜索内容过长。")
        if query:
            pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            conditions.append("""(title LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\' OR example LIKE ? ESCAPE '\\'
                OR EXISTS (SELECT 1 FROM json_each(knowledge_items.anchors) WHERE json_extract(value,'$.quote') LIKE ? ESCAPE '\\')
                OR EXISTS (SELECT 1 FROM json_each(knowledge_items.tags) WHERE value LIKE ? ESCAPE '\\'))""")
            values.extend([pattern] * 5)
        article_id = request.args.get("article_id", "")
        if article_id:
            if not article_id.isascii() or not article_id.isdigit() or len(article_id) > 15:
                raise ValueError("文章筛选无效。")
            conditions.append("article_id=?")
            values.append(int(article_id))
        rows = db.execute("SELECT * FROM knowledge_items WHERE " + " AND ".join(conditions) + " ORDER BY id DESC LIMIT 51", values).fetchall()
        return jsonify(items=[item_json(row) for row in rows[:50]], next_cursor=rows[49]["id"] if len(rows) > 50 else None)
    data = payload()
    request_key, kind = token(data), data.get("kind")
    if not isinstance(kind, str) or kind not in KINDS:
        raise ValueError("请选择有效的记录类型。")
    values = fields(data, kind)
    article_id = data.get("article_id")
    anchors, article_title, source_key = [], "", None
    db.execute("BEGIN IMMEDIATE")
    if article_id is not None:
        if type(article_id) is not int or article_id <= 0:
            raise ValueError("文章编号无效。")
        article = record("articles", article_id)
        anchors = verify_anchors(data.get("anchors"), article["content"])
        article_title = article["title"]
    elif data.get("anchors"):
        raise ValueError("摘录必须关联有效的原文。")
    if kind in ANCHORED and not anchors:
        raise ValueError("这类记录需要先选择原文或段落。")
    if kind == "bookmark" and len(anchors) != 1:
        raise ValueError("每个书签对应一个段落。")
    if kind in ("highlight", "bookmark", "sentence"):
        identity = anchors[0]["paragraph"] if kind == "bookmark" else anchors
        source_key = hashlib.sha256(dump([kind, article_id, identity]).encode()).hexdigest()
    original = db.execute("SELECT * FROM knowledge_items WHERE request_id=?", (request_key,)).fetchone()
    if original:
        same = (original["kind"] == kind and original["article_id"] == article_id
                and original["anchors"] == dump(anchors)
                and all(original[key] == value for key, value in values.items()))
        if not same:
            db.rollback()
            return jsonify(error="请求标识已用于另一条记录，请重新保存。"), 409
        db.commit()
        return jsonify(item=item_json(original), duplicate=True), 200
    if source_key:
        original = db.execute("SELECT * FROM knowledge_items WHERE source_key=?", (source_key,)).fetchone()
        if original:
            db.commit()
            return jsonify(item=item_json(original), duplicate=True), 200
    stamp = now()
    cursor = db.execute("""INSERT INTO knowledge_items (request_id,source_key,kind,article_id,article_title,anchors,
        title,content,example,color,tags,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (request_key, source_key, kind, article_id, article_title, dump(anchors), values["title"], values["content"],
         values["example"], values["color"], values["tags"], stamp, stamp))
    db.commit()
    return jsonify(item=item_json(record("knowledge_items", cursor.lastrowid)), duplicate=False), 201


@knowledge.route("/knowledge/items/<int:item_id>", methods=["GET", "PATCH", "DELETE"])
def item_detail(item_id):
    db = get_db()
    if request.method != "GET":
        data = payload()
        db.execute("BEGIN IMMEDIATE")
    item = record("knowledge_items", item_id)
    if request.method == "GET":
        return jsonify(item=item_json(item))
    if not version_matches(data, item):
        db.rollback()
        return jsonify(error="这条记录已在其他窗口修改，请重新打开后比较。", item=item_json(item)), 409
    if request.method == "DELETE":
        linked = db.execute("""SELECT DISTINCT m.* FROM knowledge_maps m, json_each(m.nodes) n
            WHERE json_extract(n.value,'$.item_id')=?""", (item_id,)).fetchall()
        for row in linked:
            nodes = json.loads(row["nodes"])
            for node in nodes:
                if node["item_id"] == item_id:
                    node["item_id"] = None
            db.execute("UPDATE knowledge_maps SET nodes=?,version=version+1,updated_at=? WHERE id=?", (dump(nodes), now(), row["id"]))
        db.execute("DELETE FROM knowledge_items WHERE id=?", (item_id,))
        db.commit()
        return jsonify(ok=True)
    if set(data) - {"version", "title", "content", "example", "color", "tags"}:
        raise ValueError("类型与原文位置不能修改，请新建记录。")
    values = fields({**item, "tags": json.loads(item["tags"]), **data}, item["kind"])
    db.execute("UPDATE knowledge_items SET title=?,content=?,example=?,color=?,tags=?,version=version+1,updated_at=? WHERE id=?",
               (values["title"], values["content"], values["example"], values["color"], values["tags"], now(), item_id))
    db.commit()
    return jsonify(item=item_json(record("knowledge_items", item_id)))


def validate_nodes(nodes):
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 100:
        raise ValueError("导图需要 1–100 个节点。")
    result, ids = [], set()
    for node in nodes:
        if not isinstance(node, dict) or set(node) != {"id", "parent", "text", "item_id"}:
            raise ValueError("导图节点格式无效。")
        node_id, parent, item_id = node["id"], node["parent"], node["item_id"]
        if not isinstance(node_id, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,64}", node_id) or node_id in ids:
            raise ValueError("导图节点编号无效或重复。")
        if parent is not None and (not isinstance(parent, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,64}", parent)):
            raise ValueError("父节点无效。")
        if item_id is not None:
            if type(item_id) is not int or item_id <= 0 or not get_db().execute("SELECT 1 FROM knowledge_items WHERE id=?", (item_id,)).fetchone():
                raise ValueError("关联记录不存在，请取消关联或重新选择。")
        result.append({"id": node_id, "parent": parent, "text": text(node, "text", 200, True), "item_id": item_id})
        ids.add(node_id)
    by_id = {node["id"]: node for node in result}
    if "root" not in by_id or by_id["root"]["parent"] is not None:
        raise ValueError("导图需要唯一的 root 中心节点。")
    for node in result:
        visited, current = set(), node
        while current["id"] != "root":
            if current["id"] in visited or current["parent"] not in by_id:
                raise ValueError("节点不能形成循环，所有节点都必须连接到中心。")
            visited.add(current["id"])
            current = by_id[current["parent"]]
    return result


def map_json(row):
    value = dict(row)
    value.pop("request_id", None)
    value["nodes"] = json.loads(value["nodes"])
    return value


@knowledge.route("/knowledge/maps", methods=["GET", "POST"])
def maps():
    db = get_db()
    if request.method == "GET":
        rows = db.execute("SELECT id,title,version,created_at,updated_at FROM knowledge_maps WHERE id<? ORDER BY id DESC LIMIT 51", (pagination(),)).fetchall()
        return jsonify(maps=[dict(row) for row in rows[:50]], next_cursor=rows[49]["id"] if len(rows) > 50 else None)
    data = payload()
    request_key, title = token(data), text(data, "title", 200, True)
    db.execute("BEGIN IMMEDIATE")
    nodes = validate_nodes(data.get("nodes"))
    existing = db.execute("SELECT * FROM knowledge_maps WHERE request_id=?", (request_key,)).fetchone()
    if existing:
        if existing["title"] != title or existing["nodes"] != dump(nodes):
            db.rollback()
            return jsonify(error="请求标识已用于另一张导图，请重新打开已保存的导图。"), 409
        db.commit()
        return jsonify(map=map_json(existing)), 200
    stamp = now()
    row = db.execute("INSERT INTO knowledge_maps (request_id,title,nodes,created_at,updated_at) VALUES (?,?,?,?,?)",
                     (request_key, title, dump(nodes), stamp, stamp))
    db.commit()
    return jsonify(map=map_json(record("knowledge_maps", row.lastrowid))), 201


@knowledge.route("/knowledge/maps/<int:map_id>", methods=["GET", "PATCH", "DELETE"])
def map_detail(map_id):
    db = get_db()
    if request.method != "GET":
        data = payload()
        db.execute("BEGIN IMMEDIATE")
    row = record("knowledge_maps", map_id)
    if request.method == "GET":
        return jsonify(map=map_json(row))
    if not version_matches(data, row):
        db.rollback()
        return jsonify(error="导图已在其他窗口修改，请重新打开后比较。", map=map_json(row)), 409
    if request.method == "DELETE":
        db.execute("DELETE FROM knowledge_maps WHERE id=?", (map_id,))
        db.commit()
        return jsonify(ok=True)
    nodes, title = validate_nodes(data.get("nodes")), text(data, "title", 200, True)
    db.execute("UPDATE knowledge_maps SET title=?,nodes=?,version=version+1,updated_at=? WHERE id=?", (title, dump(nodes), now(), map_id))
    db.commit()
    return jsonify(map=map_json(record("knowledge_maps", map_id)))
