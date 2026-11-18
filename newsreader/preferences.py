"""Validated reader preferences, stored in the existing settings table."""

import json
import math

READER_DEFAULTS = {
    "font_size": 20,
    "line_height": 1.9,
    "font_family": "serif",
    "content_width": 800,
    "paragraph_spacing": 1.45,
    "theme": "light",
    "layout": "single",
    "paragraph_numbers": False,
    "focus_mode": False,
    "shortcuts_enabled": True,
    "gestures_enabled": True,
    "reading_mode": "fluent",
    "definition_collapsed": True,
    "hint_density": "balanced",
    "suppress_mastered": True,
    "reminder_minutes": 10,
    "dictionary_provider": "auto",
}

ENUMS = {
    "font_family": ("serif", "classic", "sans", "mono"),
    "theme": ("light", "dark", "sepia"),
    "layout": ("single", "double"),
    "reading_mode": ("fluent", "intensive"),
    "hint_density": ("quiet", "balanced", "detailed"),
    "dictionary_provider": ("auto", "offline", "free"),
}
RANGES = {
    "font_size": (16, 28, True, "字号"),
    "line_height": (1.4, 2.4, False, "行距"),
    "content_width": (560, 1200, True, "正文宽度"),
    "paragraph_spacing": (0.5, 3, False, "段落间距"),
    "reminder_minutes": (0, 60, True, "提醒间隔（分钟，0 为关闭）"),
}


def validate_preferences(data):
    # Validate the entire patch before writing any of it.
    for key, value in data.items():
        if key not in READER_DEFAULTS:
            raise ValueError("不支持的阅读设置。")
        if key in ENUMS:
            if not isinstance(value, str) or value not in ENUMS[key]:
                raise ValueError("请选择有效的阅读设置选项。")
        elif key in RANGES:
            minimum, maximum, integer, label = RANGES[key]
            if (type(value) not in (int, float) or
                    (integer and type(value) is not int) or
                    not minimum <= value <= maximum or not math.isfinite(value)):
                raise ValueError(f"{label}范围为 {minimum}–{maximum}。")
        elif type(value) is not bool:
            raise ValueError("阅读开关必须为 true 或 false。")


def read_preferences(db):
    settings = READER_DEFAULTS.copy()
    for row in db.execute("SELECT key, value FROM settings"):
        key = row["key"]
        if key not in settings:
            continue
        try:
            value = json.loads(row["value"])
            validate_preferences({key: value})
        except (ValueError, TypeError, OverflowError):
            continue
        settings[key] = value
    return settings
