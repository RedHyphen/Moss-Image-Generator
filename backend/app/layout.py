"""Validation and compilation for the public MOSS-Image layout draft format.

The public prompt-enhancer format uses absolute top-left-origin ``xyxy``
coordinates. MOSS's model-facing prompt uses centralized ``yxyx``
coordinates, so conversion belongs at the inference boundary rather than in
the browser's editor state.
"""

from __future__ import annotations

import json
import math
from typing import Any


ROOT_FIELDS = {"width", "height", "bbox_coord_space", "bbox_format", "summary", "items"}
SUMMARY_FIELDS = {"caption", "meta", "canvas", "palette"}
ITEM_FIELDS = {"label", "caption", "bbox", "text", "tags", "palette"}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _nonempty_text(value: Any, location: str) -> None:
    _require(isinstance(value, str) and bool(value.strip()), f"{location} must be nonempty text")


def _string_list(value: Any, location: str) -> None:
    _require(isinstance(value, list), f"{location} must be an array")
    for index, item in enumerate(value):
        _nonempty_text(item, f"{location}[{index}]")


def validate_layout_draft(value: Any) -> dict[str, Any]:
    """Validate the current official absolute-pixel layout draft schema."""
    _require(isinstance(value, dict), "Layout draft must be an object")
    unknown = set(value) - ROOT_FIELDS
    _require(not unknown, f"Layout draft contains unsupported fields: {sorted(unknown)}")
    missing = ROOT_FIELDS - set(value)
    _require(not missing, f"Layout draft is missing fields: {sorted(missing)}")

    width, height = value["width"], value["height"]
    _require(type(width) is int and width > 0 and width % 16 == 0, "layout width must be a positive multiple of 16")
    _require(type(height) is int and height > 0 and height % 16 == 0, "layout height must be a positive multiple of 16")
    _require(value["bbox_coord_space"] == "abs", "layout bbox_coord_space must be abs")
    _require(value["bbox_format"] == "xyxy", "layout bbox_format must be xyxy")

    summary = value["summary"]
    _require(isinstance(summary, dict), "layout summary must be an object")
    unknown_summary = set(summary) - SUMMARY_FIELDS
    _require(not unknown_summary, f"layout summary contains unsupported fields: {sorted(unknown_summary)}")
    _require("caption" in summary, "layout summary.caption is required")
    _nonempty_text(summary["caption"], "layout summary.caption")
    if "canvas" in summary:
        _require(summary["canvas"] in {"single", "multi_panel", "collage"}, "layout summary.canvas is invalid")
    for key in ("meta", "palette"):
        if key in summary:
            _string_list(summary[key], f"layout summary.{key}")

    items = value["items"]
    _require(isinstance(items, list) and bool(items), "layout items must be a nonempty array")
    for index, item in enumerate(items):
        location = f"layout items[{index}]"
        _require(isinstance(item, dict), f"{location} must be an object")
        unknown_item = set(item) - ITEM_FIELDS
        _require(not unknown_item, f"{location} contains unsupported fields: {sorted(unknown_item)}")
        for required in ("label", "caption", "bbox"):
            _require(required in item, f"{location}.{required} is required")
        _require(item["label"] in {"object", "character", "text"}, f"{location}.label is invalid")
        _nonempty_text(item["caption"], f"{location}.caption")
        if item["label"] == "text":
            _require("text" in item, f"{location}.text is required for text items")
        if "text" in item:
            _nonempty_text(item["text"], f"{location}.text")
        for key in ("tags", "palette"):
            if key in item:
                _string_list(item[key], f"{location}.{key}")

        bbox = item["bbox"]
        _require(
            isinstance(bbox, list)
            and len(bbox) == 4
            and all(type(number) in (int, float) and math.isfinite(number) for number in bbox),
            f"{location}.bbox must contain four finite numbers",
        )
        x1, y1, x2, y2 = bbox
        _require(x1 < x2 and y1 < y2, f"{location}.bbox must have positive width and height")
        _require(0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height, f"{location}.bbox exceeds the {width}x{height} canvas")
    return value


def compile_layout_prompt(value: Any) -> str:
    """Compile an absolute ``xyxy`` draft to MOSS's centralized ``yxyx`` JSON."""
    draft = validate_layout_draft(value)
    width, height = draft["width"], draft["height"]
    compiled_items: list[dict[str, Any]] = []
    for item in draft["items"]:
        x1, y1, x2, y2 = item["bbox"]
        compiled = dict(item)
        compiled["bbox"] = [
            math.floor(round(y1, 9)) - height // 2,
            math.floor(round(x1, 9)) - width // 2,
            math.ceil(round(y2, 9)) - height // 2,
            math.ceil(round(x2, 9)) - width // 2,
        ]
        compiled_items.append(compiled)
    compiled = {
        "summary": draft["summary"],
        "items": compiled_items,
        "bbox_coord_space": "abs_centralized",
        "bbox_format": "yxyx",
    }
    return json.dumps(compiled, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
