from app.controller import interpret_request
from app.identity import extract_named_identities


def test_extracts_explicit_character_and_source_pair():
    entities = extract_named_identities("Generate Furina from Genshin Impact in a snowy forest")
    assert [(item.name, item.source) for item in entities] == [("Furina", "Genshin Impact")]


def test_deduplicates_repeated_identity_mentions_and_counts_them():
    entities = extract_named_identities(
        "Two views of Furina from Genshin Impact and Furina from Genshin Impact"
    )
    assert len(entities) == 1
    assert entities[0].mention_count == 2


def test_rejects_generic_from_phrase_as_named_identity():
    assert extract_named_identities("Generate a warrior from a snowy forest") == ()


def test_text_only_identity_is_anchored_and_warned():
    plan = interpret_request("Generate Furina from Genshin Impact in a snowy forest")
    assert plan.identity_mode == "text-only"
    assert plan.identity_entities[0].name == "Furina"
    assert "Furina" in plan.prompt
    assert any("text-only" in warning for warning in plan.warnings)
