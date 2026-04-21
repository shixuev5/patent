import html
import re
from typing import Any


_IMAGE_PLACEHOLDER = "（原文含图片或公式，解析未提取到可展示文本）"
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
    value = _normalize_math_text(value)
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
    value = _normalize_math_text(value)
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
    value = re.sub(r"(?is)<[^>]+>", "", value)
    return value


def _normalize_math_text(text: str) -> str:
    value = str(text or "")
    value = re.sub(r"\$\$(.*?)\$\$", lambda match: f"$${_compact_math_segment(match.group(1))}$$", value, flags=re.DOTALL)
    value = re.sub(r"(?<!\$)\$(?!\$)(.*?)(?<!\$)\$(?!\$)", lambda match: f"${_compact_math_segment(match.group(1))}$", value, flags=re.DOTALL)
    value = re.sub(r"\\([A-Za-z]+)\s+\{", r"\\\1{", value)
    value = re.sub(r"\\([A-Za-z]+)\s+(?=[A-Za-z])", r"\\\1 ", value)
    value = re.sub(r"\\mathrm\s*\{\s*~?\s*([^{}]+?)\s*\}", r"\\mathrm{\1}", value)
    return value


def _compact_math_segment(segment: str) -> str:
    value = re.sub(r"\s+", " ", str(segment or "")).strip()
    value = re.sub(r"\s*([_^{}(),=+\-*/|])\s*", r"\1", value)
    value = re.sub(r"\s*([<>]=?|<=|>=)\s*", r" \1 ", value)
    value = re.sub(
        r"(?<![A-Za-z])(?:\d\s+){2,}\d(?![A-Za-z])",
        lambda match: re.sub(r"\s+", "", match.group(0)),
        value,
    )
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"\\([A-Za-z]+)\s+\{", r"\\\1{", value)
    return value
