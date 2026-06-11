import html
import re
from typing import Any


_IMAGE_PLACEHOLDER = "（原文含图片或公式，解析未提取到可展示文本）"
_CIRCLED_DIGITS = {
    "0": "⓪",
    "1": "①",
    "2": "②",
    "3": "③",
    "4": "④",
    "5": "⑤",
    "6": "⑥",
    "7": "⑦",
    "8": "⑧",
    "9": "⑨",
    "10": "⑩",
    "11": "⑪",
    "12": "⑫",
    "13": "⑬",
    "14": "⑭",
    "15": "⑮",
    "16": "⑯",
    "17": "⑰",
    "18": "⑱",
    "19": "⑲",
    "20": "⑳",
}
_CIRCLED_LOWER = {
    "a": "ⓐ",
    "b": "ⓑ",
    "c": "ⓒ",
    "d": "ⓓ",
    "e": "ⓔ",
    "f": "ⓕ",
    "g": "ⓖ",
    "h": "ⓗ",
    "i": "ⓘ",
    "j": "ⓙ",
    "k": "ⓚ",
    "l": "ⓛ",
    "m": "ⓜ",
    "n": "ⓝ",
    "o": "ⓞ",
    "p": "ⓟ",
    "q": "ⓠ",
    "r": "ⓡ",
    "s": "ⓢ",
    "t": "ⓣ",
    "u": "ⓤ",
    "v": "ⓥ",
    "w": "ⓦ",
    "x": "ⓧ",
    "y": "ⓨ",
    "z": "ⓩ",
}
_FULLWIDTH_TRANSLATION = str.maketrans(
    {
        "，": ",",
        "。": ".",
        "；": ";",
        "：": ":",
        "（": "(",
        "）": ")",
        "【": "[",
        "】": "]",
        "｛": "{",
        "｝": "}",
        "＜": "<",
        "＞": ">",
        "＝": "=",
        "＋": "+",
        "－": "-",
        "／": "/",
        "＊": "*",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "　": " ",
    }
)


def sanitize_for_display(text: Any) -> str:
    value = _normalize_html_text(text, image_placeholder=_IMAGE_PLACEHOLDER)
    value = _normalize_circled_markers(value)
    value = _normalize_math_text(value)
    value = _normalize_plain_text_noise(value)
    value = re.sub(r"(?<!\*)\*\*(?!\*)", "", value)
    value = re.sub(r"(?<!_)__(?!_)", "", value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = re.sub(r"\s+([,.;:)\]}>])", r"\1", value)
    value = re.sub(r"([({\[])\s+", r"\1", value)
    value = re.sub(r"\s+([，。；：、！？])", r"\1", value)
    value = re.sub(r"([（【《“])\s+", r"\1", value)
    value = re.sub(r"\s+([）】》”])", r"\1", value)
    return value.strip()


def normalize_for_compare(text: Any) -> str:
    value = _normalize_html_text(text, image_placeholder="")
    value = value.translate(_FULLWIDTH_TRANSLATION)
    value = _normalize_circled_markers(value)
    value = _normalize_math_text(value)
    value = _normalize_plain_text_noise(value)
    value = value.replace(_IMAGE_PLACEHOLDER, "")
    value = re.sub(r"(?<!\*)\*\*(?!\*)", "", value)
    value = re.sub(r"(?<!_)__(?!_)", "", value)
    value = re.sub(r"\\left", "", value)
    value = re.sub(r"\\right", "", value)
    value = re.sub(r"\\mathrm\s*\{([^{}]+)\}", r"\1", value)
    value = re.sub(r"(?<!\\)\$", "", value)
    value = re.sub(r"(\d)\s+(?=(?:Pa|kPa|MPa|kg|g|mm|cm|m|s|ms|h)\b)", r"\1", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"[,\.;:，。；：、\-—_（）()\[\]{}<>《》“”\"'!?！？`]", "", value)
    return value.strip()


def _normalize_html_text(text: Any, image_placeholder: str) -> str:
    value = str(text or "").replace("\r\n", "\n")
    value = html.unescape(value)
    value = value.replace("\xa0", " ").replace("　", " ")
    value = re.sub(r"(?is)<img\b[^>]*>", image_placeholder, value)
    value = re.sub(r"(?is)</?u\b[^>]*>", "", value)
    value = re.sub(r"(?is)</?(?:span|div|p|font|strong|em|b|i)\b[^>]*>", "", value)
    value = re.sub(r"(?is)<br\s*/?>", "\n", value)
    value = re.sub(r"(?is)</?[A-Za-z][^>]*>", "", value)
    return value


def _normalize_math_text(text: str) -> str:
    value = str(text or "")
    value = re.sub(r"\$\$(.*?)\$\$", lambda match: f"$${_compact_math_segment(match.group(1))}$$", value, flags=re.DOTALL)
    value = re.sub(
        r"(?<!\$)\$(?!\$)(.*?)(?<!\$)\$(?!\$)",
        lambda match: _normalize_inline_math_segment(match.group(1)),
        value,
        flags=re.DOTALL,
    )
    value = re.sub(r"\\([A-Za-z]+)\s+\{", r"\\\1{", value)
    value = re.sub(r"\\([A-Za-z]+)\s+(?=[A-Za-z])", r"\\\1 ", value)
    value = re.sub(r"\\mathrm\s*\{\s*~?\s*([^{}]+?)\s*\}", r"\\mathrm{\1}", value)
    return value


def _compact_math_segment(segment: str) -> str:
    value = re.sub(r"\s+", " ", str(segment or "")).strip()
    value = re.sub(r"\\(?=[<>|=])", "", value)
    value = re.sub(r"\{\s*([<>]=?|<=|>=|=|\|)\s*\}", r"\1", value)
    value = re.sub(r"<\s*=", "<=", value)
    value = re.sub(r">\s*=", ">=", value)
    value = re.sub(r"\s*([_^{}(),=+\-*/|])\s*", r"\1", value)
    value = re.sub(r"\s*([<>]=?|<=|>=)\s*", r" \1 ", value)
    value = re.sub(r"(?<=\d)\s*\.\s*(?=\d)", ".", value)
    value = re.sub(r"(?<=\d\.\d)\s+(?=\d)", "", value)
    value = re.sub(
        r"(?<![A-Za-z])(?:\d\s+){2,}\d(?![A-Za-z])",
        lambda match: re.sub(r"\s+", "", match.group(0)),
        value,
    )
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"\\([A-Za-z]+)\s+\{", r"\\\1{", value)
    return value


def _normalize_plain_text_noise(text: str) -> str:
    return _apply_to_non_math_segments(text, _normalize_plain_text_segment)


def _apply_to_non_math_segments(text: str, transform: Any) -> str:
    parts: list[str] = []
    index = 0
    value = str(text or "")
    length = len(value)
    while index < length:
        if value.startswith("$$", index):
            end = value.find("$$", index + 2)
            if end != -1:
                parts.append(value[index:end + 2])
                index = end + 2
                continue
        if value[index] == "$":
            end = index + 1
            while end < length:
                if value[end] == "$" and value[end - 1] != "\\":
                    break
                end += 1
            if end < length and value[end] == "$":
                parts.append(value[index:end + 1])
                index = end + 1
                continue
        next_math = value.find("$", index)
        if next_math == -1:
            parts.append(transform(value[index:]))
            break
        if next_math == index:
            parts.append(transform(value[index:index + 1]))
            index += 1
            continue
        parts.append(transform(value[index:next_math]))
        index = next_math
    return "".join(parts)


def _normalize_plain_text_segment(text: str) -> str:
    value = str(text or "")
    value = re.sub(r"\\(?=[<>|=])", "", value)
    value = re.sub(r"(以及)(?:[\s\u3000]+\1)+", r"\1", value)
    value = re.sub(r"Δ\s+([A-Za-z])", r"Δ\1", value)
    value = re.sub(r"([A-Za-z])\s+(?=\d)", r"\1", value)
    value = re.sub(r"(?<=\d)\s+(?=[A-Za-z])", "", value)
    value = re.sub(r"(?<=[<>]=)\s+(?=\d)", "", value)
    return value


def _normalize_circled_markers(text: str) -> str:
    value = str(text or "")
    value = re.sub(
        r"(?<!\\)\$\s*\\textcircle(?:d)?\s*(?:\{\s*([^{}]+?)\s*\}|\(\s*([^)]+?)\s*\)|([A-Za-z0-9]))\s*\$",
        lambda match: _to_circled_symbol(match.group(1) or match.group(2) or match.group(3)),
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"\\textcircle(?:d)?\s*(?:\{\s*([^{}]+?)\s*\}|\(\s*([^)]+?)\s*\)|([A-Za-z0-9]))",
        lambda match: _to_circled_symbol(match.group(1) or match.group(2) or match.group(3)),
        value,
        flags=re.IGNORECASE,
    )
    return value


def _to_circled_symbol(raw: str) -> str:
    token = str(raw or "").strip()
    if not token:
        return ""
    compact = re.sub(r"\s+", "", token)
    if compact in _CIRCLED_DIGITS:
        return _CIRCLED_DIGITS[compact]
    lowered = compact.lower()
    if lowered in _CIRCLED_LOWER:
        return _CIRCLED_LOWER[lowered]
    return compact


def _normalize_inline_math_segment(segment: str) -> str:
    compact = _compact_math_segment(segment)
    if _should_downgrade_inline_math(compact):
        return _normalize_inline_math_plain_text(compact)
    return f"${compact}$"


def _should_downgrade_inline_math(segment: str) -> bool:
    value = str(segment or "")
    if not value:
        return False
    if "\\frac" in value or "\\sum" in value or "\\prod" in value or "\\sqrt" in value:
        return False
    markers = ("\\Delta", "\\amalg", "\\mathrm", "<=", ">=", "\\|", "dB", "IL", "CR")
    return any(marker in value for marker in markers)


def _normalize_inline_math_plain_text(segment: str) -> str:
    value = str(segment or "")
    value = re.sub(r"\\amalg\s*(\d+)", r"IL\1", value)
    value = value.replace("\\Delta", "Δ")
    value = value.replace("\\sim", "~")
    value = value.replace("\\times", "×")
    value = re.sub(r"\\mathrm\{([^{}]+)\}", lambda match: re.sub(r"\s+", "", match.group(1)), value)
    value = value.replace("{", "").replace("}", "")
    value = value.replace("\\|", "|")
    value = value.replace("\\", "")
    value = re.sub(r"(?<![A-Za-z])mathrm", "", value)
    value = re.sub(r"<\s*=", "<=", value)
    value = re.sub(r">\s*=", ">=", value)
    value = re.sub(r"Δ\s+([A-Za-z])", r"Δ\1", value)
    value = re.sub(r"([A-Za-z])\s+(?=\d)", r"\1", value)
    value = re.sub(r"(?<=[A-Za-z0-9])\s+(?=\d)", "", value)
    value = re.sub(r"(?<=\d)\s+(?=[A-Za-z])", "", value)
    value = re.sub(r"\s*([=<>+\-*/|])\s*", r"\1", value)
    value = re.sub(r"(?<=[<>]=)\s+(?=\d)", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value
