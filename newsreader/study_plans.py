"""Study goals and plans based on the local reading and review records."""

from datetime import date, datetime, timedelta
import json
import re

from flask import Blueprint, abort, jsonify, request

from .db import CHINA_TZ, current_timezone, get_db, now, today
from .personalization import CATEGORIES

planning = Blueprint("planning", __name__, url_prefix="/api/planning")
PLAN_TABLES = ("study_plans", "study_makeups", "study_reminder_days")
MAKEUP_DAYS = 7
DEFAULTS = {
    "reading_minutes": 15, "new_words": 5,
    "reading_reminder": "", "review_reminder": "",
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS study_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK(kind IN ('week','topic')),
    title TEXT NOT NULL, category TEXT NOT NULL,
    start_day TEXT NOT NULL, end_day TEXT NOT NULL,
    reading_minutes INTEGER NOT NULL CHECK(reading_minutes BETWEEN 0 AND 14400),
    articles INTEGER NOT NULL CHECK(articles BETWEEN 0 AND 500),
    new_words INTEGER NOT NULL CHECK(new_words BETWEEN 0 AND 5000),
    steps_json TEXT NOT NULL DEFAULT '[]',
    reflection TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS study_makeups (
    day TEXT PRIMARY KEY, note TEXT NOT NULL DEFAULT '', recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS study_reminder_days (
    day TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('reading','review')),
    PRIMARY KEY(day, kind)
);
"""


def payload():
    data = request.get_json()
    if not isinstance(data, dict):
        raise ValueError("请提交 JSON 对象。")
    return data


def integer(value, minimum, maximum, message):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(message)
    return value


def clean_text(value, limit, label):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"{label}最多 {limit} 个字符。")
    return value.strip()


def parse_day(value):
    if isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        try:
            result = date.fromisoformat(value)
            if 1901 <= result.year <= 9998:
                return result
        except ValueError:
            pass
    raise ValueError("请填写有效日期（YYYY-MM-DD，1901–9998 年）。")


def read_goals(db):
    row = db.execute("SELECT value FROM settings WHERE key='planning.goals'").fetchone()
    try:
        preferences = json.loads(row[0]) if row else {}
        return {"version": 0, **DEFAULTS, **validate_goals(preferences, stored=True)}
    except (ValueError, TypeError):
        return {"version": 0, **DEFAULTS}


def validate_goals(data, stored=False):
    if not isinstance(data, dict) or set(data) - {*DEFAULTS, "version"}:
        raise ValueError("包含未知的目标设置。")
    valid = {}
    for name, maximum in (("reading_minutes", 720), ("new_words", 500)):
        if name in data:
            valid[name] = integer(data[name], 0, maximum, f"目标应为 0–{maximum} 的整数。")
    for name in ("reading_reminder", "review_reminder"):
        if name in data:
            value = data[name]
            if not isinstance(value, str) or (value and not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value)):
                raise ValueError("提醒时间应为 HH:MM，留空可关闭。")
            valid[name] = value
    revision = integer(data.get("version"), 0, 2**53 - 1, "目标设置版本无效。")
    return {**valid, "version": revision} if stored else valid


def activity(db, start, end, category=""):
    """Period totals retain the existing meaning of reading and review events."""
    bounds = (start.isoformat(), end.isoformat())
    if category:
        reading = db.execute(
            """SELECT day,SUM(seconds),SUM(completed) FROM learning_reading_events
               WHERE day BETWEEN ? AND ? AND category=? GROUP BY day""",
            (*bounds, category),
        )
    else:
        reading = db.execute(
            "SELECT date,reading_seconds,articles_completed FROM checkins WHERE date BETWEEN ? AND ?", bounds
        )
    days = {
        row[0]: {"seconds": row[1], "articles": row[2], "words": 0, "reviews": 0}
        for row in reading
    }
    if not category:
        for day, count in db.execute(
            "SELECT day,COUNT(*) FROM learning_word_events WHERE kind='added' AND day BETWEEN ? AND ? GROUP BY day",
            bounds,
        ):
            days.setdefault(day, dict.fromkeys(("seconds", "articles", "words", "reviews"), 0))["words"] = count
        for day, count in db.execute(
            "SELECT day,COUNT(*) FROM srs_events WHERE day BETWEEN ? AND ? GROUP BY day", bounds
        ):
            days.setdefault(day, dict.fromkeys(("seconds", "articles", "words", "reviews"), 0))["reviews"] = count
    return days


def today_progress(db, current):
    return activity(db, current, current).get(
        current.isoformat(), dict.fromkeys(("seconds", "articles", "words", "reviews"), 0)
    )


def continuity(db, current, include_makeups=True):
    real = {row[0] for row in db.execute("SELECT date FROM checkins WHERE date<=?", (current.isoformat(),))}
    added = {row[0] for row in db.execute("SELECT day FROM study_makeups WHERE day<=?", (current.isoformat(),))}
    dates = real | added if include_makeups else real
    streak = longest = run = 0
    previous = None
    for value in sorted(dates):
        day = date.fromisoformat(value)
        run = run + 1 if previous and (day - previous).days == 1 else 1
        longest = max(longest, run)
        previous = day
    cursor = current if current.isoformat() in dates else current - timedelta(days=1)
    while cursor.isoformat() in dates:
        streak += 1
        cursor -= timedelta(days=1)
    return {"current": streak, "longest": longest, "study_days": len(real), "makeups": len(added)}


def achievements(db, actual_streak):
    totals = db.execute("SELECT COALESCE(SUM(articles_completed),0),COALESCE(SUM(reading_seconds),0) FROM checkins").fetchone()
    vocabulary = db.execute("SELECT COUNT(*) FROM learning_word_events WHERE kind='added'").fetchone()[0]
    reviewed = db.execute("SELECT COUNT(*) FROM srs_events WHERE state_before IN ('review','relearning') AND grade>=3").fetchone()[0]
    milestones = (
        ("读完第一篇", totals[0], 1, "篇文章"),
        ("读完十篇", totals[0], 10, "篇文章"),
        ("累计阅读一小时", totals[1] // 60, 60, "分钟"),
        ("收集二十条词汇", vocabulary, 20, "条词汇"),
        ("复习记住十次", reviewed, 10, "次复习"),
        ("连续学习七天", actual_streak, 7, "天（实际打卡）"),
    )
    return [
        {"title": title, "actual": actual, "target": target, "unit": unit, "earned": actual >= target}
        for title, actual, target, unit in milestones
    ]


def plan_record(db, identity):
    row = db.execute("SELECT * FROM study_plans WHERE id=?", (identity,)).fetchone()
    if row is None:
        abort(404)
    return row


def validate_plan(data):
    allowed = {"kind", "title", "category", "start_day", "end_day", "reading_minutes", "articles",
               "new_words", "steps", "reflection", "version"}
    if set(data) - allowed:
        raise ValueError("包含未知的计划字段。")
    kind = data.get("kind")
    if kind not in ("week", "topic"):
        raise ValueError("请选择周计划或专题计划。")
    start = parse_day(data.get("start_day"))
    end = parse_day(data.get("end_day"))
    if kind == "week" and (start.weekday() != 0 or (end - start).days != 6):
        raise ValueError("周计划应从星期一开始，到星期日结束。")
    if not 0 <= (end - start).days < 366:
        raise ValueError("计划日期范围应为 1–366 天。")
    category = data.get("category", "")
    if (kind == "topic" and category not in CATEGORIES) or (kind == "week" and category != ""):
        raise ValueError("专题计划请选择一个文章分类。")
    title = clean_text(data.get("title"), 80, "计划名称")
    if not title:
        raise ValueError("请输入计划名称。")
    steps = data.get("steps", [])
    if not isinstance(steps, list) or len(steps) > 12:
        raise ValueError("每个计划最多 12 个步骤。")
    cleaned = []
    for step in steps:
        if not isinstance(step, dict) or set(step) != {"text", "done"} or type(step["done"]) is not bool:
            raise ValueError("计划步骤格式无效。")
        text = clean_text(step["text"], 160, "步骤")
        if not text or any(other["text"] == text for other in cleaned):
            raise ValueError("步骤不能为空或重复。")
        cleaned.append({"text": text, "done": step["done"]})
    limits = (("reading_minutes", 14400), ("articles", 500), ("new_words", 5000))
    targets = {name: integer(data.get(name, 0), 0, limit, f"计划目标应为 0–{limit} 的整数。") for name, limit in limits}
    if kind == "topic" and targets["new_words"]:
        raise ValueError("专题计划自动统计对应分类的阅读，词汇任务可写入步骤。")
    if not any(targets.values()) and not cleaned:
        raise ValueError("请设置至少一个目标或添加步骤。")
    return {
        "kind": kind, "title": title, "category": category,
        "start_day": start.isoformat(), "end_day": end.isoformat(), **targets,
        "steps_json": json.dumps(cleaned, ensure_ascii=False),
        "reflection": clean_text(data.get("reflection", ""), 2000, "阶段笔记"),
    }


def plan_result(db, row, current):
    plan = dict(row)
    plan["steps"] = json.loads(plan.pop("steps_json"))
    start, end = parse_day(plan["start_day"]), parse_day(plan["end_day"])
    observed = activity(db, start, min(end, current), plan["category"])
    totals = {key: sum(value[key] for value in observed.values()) for key in ("seconds", "articles", "words", "reviews")}
    targets = {"seconds": plan["reading_minutes"] * 60, "articles": plan["articles"], "words": plan["new_words"]}
    duration = (end - start).days + 1
    days = []
    for offset in range(duration):
        day = (start + timedelta(days=offset)).isoformat()
        expected = {
            key: amount * (offset + 1) // duration - amount * offset // duration
            for key, amount in targets.items()
        }
        days.append({"day": day, "target": expected, "actual": observed.get(day)})
    steps_done = sum(step["done"] for step in plan["steps"])
    plan.update(
        totals=totals, targets=targets, days=days, steps_done=steps_done,
        status="upcoming" if current < start else "ended" if current > end else "active",
        completed=all(totals[key] >= value for key, value in targets.items()) and steps_done == len(plan["steps"]),
    )
    return plan


@planning.get("")
def overview():
    db = get_db()
    current = today()
    streak = continuity(db, current)
    real_streak = continuity(db, current, include_makeups=False)
    first = current - timedelta(days=MAKEUP_DAYS)
    recent = {
        row[0] for row in db.execute("SELECT date FROM checkins WHERE date BETWEEN ? AND ?", (first.isoformat(), current.isoformat()))
    }
    makeups = {
        row["day"]: dict(row) for row in db.execute(
            "SELECT * FROM study_makeups WHERE day BETWEEN ? AND ? ORDER BY day DESC", (first.isoformat(), current.isoformat())
        )
    }
    candidates = [(current - timedelta(days=offset)).isoformat() for offset in range(1, MAKEUP_DAYS + 1)]
    return jsonify(
        today=current.isoformat(), goals=read_goals(db), progress=today_progress(db, current),
        continuity=streak, makeup_candidates=[day for day in candidates if day not in recent and day not in makeups],
        makeups=list(makeups.values()), achievements=achievements(db, real_streak["longest"]),
        plans=[dict(row) for row in db.execute(
            """SELECT id,kind,title,category,start_day,end_day FROM study_plans
               ORDER BY CASE WHEN ? BETWEEN start_day AND end_day THEN 0 WHEN start_day>? THEN 1 ELSE 2 END,
               start_day DESC,id DESC""", (current.isoformat(), current.isoformat())
        )],
    )


@planning.patch("/goals")
def save_goals():
    data = payload()
    changes = validate_goals(data)
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    current = read_goals(db)
    if data["version"] != current["version"]:
        db.rollback()
        return jsonify(error="目标已在其他页面更新，请重新载入。"), 409
    goals = {**current, **changes, "version": current["version"] + 1}
    db.execute("INSERT OR REPLACE INTO settings VALUES('planning.goals',?)", (json.dumps(goals),))
    db.commit()
    return jsonify(goals=goals)


@planning.post("/plans")
def create_plan():
    record = validate_plan(payload())
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    if db.execute("SELECT COUNT(*) FROM study_plans").fetchone()[0] >= 500:
        raise ValueError("最多保存 500 份计划，请删除不再需要的计划后再添加。")
    cursor = db.execute(
        """INSERT INTO study_plans(kind,title,category,start_day,end_day,reading_minutes,articles,new_words,steps_json,reflection)
           VALUES(:kind,:title,:category,:start_day,:end_day,:reading_minutes,:articles,:new_words,:steps_json,:reflection)""", record
    )
    db.commit()
    return jsonify(plan=plan_result(db, plan_record(db, cursor.lastrowid), today())), 201


@planning.route("/plans/<int:identity>", methods=["GET", "PATCH", "DELETE"])
def change_plan(identity):
    db = get_db()
    if request.method == "GET":
        return jsonify(plan=plan_result(db, plan_record(db, identity), today()))
    data = payload()
    requested_version = integer(data.get("version"), 1, 2**53 - 1, "计划版本无效。")
    db.execute("BEGIN IMMEDIATE")
    row = plan_record(db, identity)
    if requested_version != row["version"]:
        db.rollback()
        return jsonify(error="计划已在其他页面更新，请重新打开后再操作。"), 409
    if request.method == "DELETE":
        db.execute("DELETE FROM study_plans WHERE id=?", (identity,))
        db.commit()
        return jsonify(ok=True)
    current = {key: row[key] for key in (
        "kind", "title", "category", "start_day", "end_day", "reading_minutes", "articles", "new_words", "reflection"
    )}
    record = validate_plan({**current, "steps": json.loads(row["steps_json"]), **data})
    db.execute(
        """UPDATE study_plans SET kind=:kind,title=:title,category=:category,start_day=:start_day,end_day=:end_day,
           reading_minutes=:reading_minutes,articles=:articles,new_words=:new_words,steps_json=:steps_json,
           reflection=:reflection,version=version+1 WHERE id=:id""", {**record, "id": identity}
    )
    db.commit()
    return jsonify(plan=plan_result(db, plan_record(db, identity), today()))


@planning.post("/makeups")
def add_makeup():
    data = payload()
    day = parse_day(data.get("day")).isoformat()
    note = clean_text(data.get("note", ""), 200, "补签备注")
    current = today()
    if not (current - timedelta(days=MAKEUP_DAYS)).isoformat() <= day < current.isoformat():
        raise ValueError("只能补签过去 7 天内尚未打卡的日期。")
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    existing = db.execute("SELECT * FROM study_makeups WHERE day=?", (day,)).fetchone()
    if existing:
        db.rollback()
        return jsonify(makeup=dict(existing), duplicate=True)
    if db.execute("SELECT 1 FROM checkins WHERE date=?", (day,)).fetchone():
        db.rollback()
        return jsonify(error="当天已有打卡记录。"), 409
    recorded = now()
    db.execute("INSERT INTO study_makeups VALUES(?,?,?)", (day, note, recorded))
    db.commit()
    return jsonify(makeup={"day": day, "note": note, "recorded_at": recorded}, duplicate=False), 201


def clock():
    return datetime.now(current_timezone())


@planning.post("/reminders/claim")
def claim_reminders():
    if payload():
        raise ValueError("提醒请求不需要参数。")
    db = get_db()
    current = clock()
    day = current.date().isoformat()
    local_time = current.strftime("%H:%M")
    goals = read_goals(db)
    if not any(goals[key] and goals[key] <= local_time for key in ("reading_reminder", "review_reminder")):
        return jsonify(day=day, reminders=[])
    db.execute("BEGIN IMMEDIATE")
    goals = read_goals(db)
    progress = today_progress(db, current.date())
    needs_reading = (progress["seconds"] < goals["reading_minutes"] * 60 or progress["words"] < goals["new_words"])
    due = db.execute(
        """SELECT COUNT(*) FROM srs_cards
           WHERE suspended=0 AND state IN ('review','relearning') AND due_at<=?""", (current.isoformat(),)
    ).fetchone()[0]
    reminders = []
    for kind, needed in (("reading", needs_reading), ("review", due > 0)):
        scheduled = goals[kind + "_reminder"]
        if needed and scheduled and scheduled <= local_time:
            inserted = db.execute("INSERT OR IGNORE INTO study_reminder_days VALUES(?,?)", (day, kind))
            if inserted.rowcount:
                reminders.append({"kind": kind, "due": due if kind == "review" else None})
    db.commit()
    return jsonify(day=day, reminders=reminders)
