import json

import pytest

from app.layout import compile_layout_prompt, validate_layout_draft


def draft(width=1024, height=1024):
    return {
        "width": width,
        "height": height,
        "bbox_coord_space": "abs",
        "bbox_format": "xyxy",
        "summary": {"caption": "A clean product poster", "canvas": "single", "meta": ["poster"]},
        "items": [
            {
                "label": "object",
                "caption": "A ceramic cup centered on the table",
                "bbox": [176, 224, 800, 864],
            },
            {
                "label": "text",
                "caption": "A large readable title",
                "text": "MOSS",
                "bbox": [928, 272, 1408, 448],
            },
        ],
    }


def test_layout_compiler_matches_official_coordinate_conversion():
    compiled = json.loads(compile_layout_prompt(draft(1536, 1024)))

    assert compiled["bbox_coord_space"] == "abs_centralized"
    assert compiled["bbox_format"] == "yxyx"
    assert compiled["items"][0]["bbox"] == [-288, -592, 352, 32]
    assert compiled["items"][1]["bbox"] == [-240, 160, -64, 640]


def test_layout_validation_rejects_model_facing_schema_and_unknown_fields():
    legacy = draft()
    legacy.pop("width")
    legacy.pop("height")
    legacy["bbox_coord_space"] = "abs_centralized"
    legacy["bbox_format"] = "yxyx"
    with pytest.raises(ValueError, match="missing fields"):
        validate_layout_draft(legacy)

    invalid = draft()
    invalid["items"][0]["unexpected"] = "discard me"
    with pytest.raises(ValueError, match="unsupported fields"):
        validate_layout_draft(invalid)


def test_layout_validation_requires_nonempty_items_and_text():
    empty = draft()
    empty["items"] = []
    with pytest.raises(ValueError, match="nonempty"):
        validate_layout_draft(empty)

    missing_text = draft()
    missing_text["items"][1].pop("text")
    with pytest.raises(ValueError, match="text is required"):
        validate_layout_draft(missing_text)
