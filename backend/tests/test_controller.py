import pytest

from app.controller import interpret_request


def test_plain_prompt_routes_to_moss_generation():
    plan = interpret_request("Generate a photorealistic snowy forest")
    assert plan.kind == "generate"
    assert plan.operation == "text-to-image"
    assert plan.backend == "vllm-omni-moss-image"
    assert plan.context_mode == "none"


def test_plain_description_after_output_starts_new_moss_generation():
    plan = interpret_request(
        "A red cape flowing in a snowy forest",
        latest_image_id="version-1",
        previous_prompt="A knight standing in a snowy forest",
    )
    assert plan.kind == "generate"
    assert plan.context_mode == "none"


def test_follow_up_carries_prompt_context_without_raster_conditioning():
    plan = interpret_request(
        "Make the armour black and add a red cape",
        latest_image_id="version-1",
        previous_prompt="A knight standing in a snowy forest",
    )
    assert plan.kind == "generate"
    assert plan.context_mode == "prompt-continuation"
    assert "A knight standing in a snowy forest" in plan.prompt
    assert "Make the armour black" in plan.prompt
    assert any("does not receive the previous raster" in warning for warning in plan.warnings)


def test_explicit_fresh_language_does_not_continue_previous_prompt():
    plan = interpret_request(
        "Create a completely new image from scratch",
        latest_image_id="version-1",
        previous_prompt="A knight standing in a snowy forest",
    )
    assert plan.kind == "generate"
    assert plan.context_mode == "none"


def test_explicit_generate_prompt_wins_over_edit_words_without_context():
    plan = interpret_request(
        "Generate a new character image; keep the iconic hair and remove the outer clothing",
    )
    assert plan.kind == "generate"
    assert plan.context_mode == "none"


def test_edit_language_without_context_is_rejected_instead_of_faking_editing():
    with pytest.raises(ValueError, match="Image editing has been removed"):
        interpret_request("Change the background to a snowy forest")


def test_uploaded_assets_are_rejected_before_model_routing():
    with pytest.raises(ValueError, match="uploaded images are not accepted"):
        interpret_request("Use the subject", reference_ids=["upload-1"])
    with pytest.raises(ValueError, match="uploaded images are not accepted"):
        interpret_request("Use this", image_ids=["image-1"])


def test_parent_image_is_not_silently_discarded():
    with pytest.raises(ValueError, match="uploaded images are not accepted"):
        interpret_request("Make this more cinematic", parent_image_id="image-1")


def test_structured_edit_hint_can_continue_text_context_only():
    plan = interpret_request(
        "Please transform this",
        latest_image_id="version-1",
        previous_prompt="A portrait in soft morning light",
        intent_hint="edit",
        preservation_hint="close",
    )
    assert plan.kind == "generate"
    assert plan.context_mode == "prompt-continuation"
    assert plan.preservation == "close"


def test_visual_questions_are_not_routed_to_text_generation():
    with pytest.raises(ValueError, match="vision-language"):
        interpret_request("Describe this image")


def test_named_identity_is_explicitly_text_only():
    plan = interpret_request("Generate Furina from Genshin Impact in a snowy forest")
    assert plan.identity_mode == "text-only"
    assert plan.identity_entities[0].name == "Furina"
    assert "Furina" in plan.prompt
    assert any("text-only" in warning for warning in plan.warnings)
