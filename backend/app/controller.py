"""Small, capability-aware router for the MOSS-only application.

MOSS-Image is used for text-to-image generation only. Follow-up messages may
carry forward the previous prompt as text, but no previous raster or upload is
ever presented as model conditioning.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

from .identity import IdentityEntity, append_identity_instruction, extract_named_identities, identity_instruction


EDIT_TERMS = {
    "edit", "modify", "transform", "restyle", "recolor", "keep", "preserve", "remove",
    "more realistic", "more cinematic", "different outfit", "same subject", "same composition",
    "variation", "variant", "new version", "regenerate", "revert", "restore", "earlier version",
    "original version",
}
NEW_TERMS = {"generate", "create", "draw", "design", "make a new", "another image", "new image"}


def _requests_fresh_generation(text: str) -> bool:
    lowered = text.lower().strip()
    # An explicit fresh-generation verb wins over edit-like words inside the
    # description, such as "keep the character" or "remove the background".
    # Without this precedence, a new prompt can be rejected as editing when
    # it follows an earlier image in the same conversation.
    if lowered.startswith(("generate ", "create ", "draw ", "design ")):
        return True
    return any(term in lowered for term in (
        "from scratch", "completely new image", "new image", "another image", "new scene",
        "unrelated image", "start over", "without using this image", "without using the previous image",
    ))


def _has_edit_intent(text: str) -> bool:
    lowered = text.lower()
    contextual = (
        "make it", "make this", "make the ", "make a ", "change this", "change the ", "change ",
        "turn this", "turn the ", "add this to", "add a ", "add the ", "remove this from",
        "remove the ", "replace the ", "restyle the ", "recolor the ", "preserve the ",
        "keep the same", "keep the ", "restore the ", "go back to", "use the original",
        "use this image", "use that image", "use the previous image", "new version", "new variant",
        "alternative version", "different version",
    )
    return any(term in lowered for term in EDIT_TERMS) or any(term in lowered for term in contextual)


def _is_visual_question(text: str) -> bool:
    return text.lower().strip().startswith(("what is", "what's", "describe", "analyze", "analyse", "identify"))


def _prompt_continuation(previous_prompt: str, instruction: str) -> str:
    return (
        "Continue the previous image concept as a new coherent MOSS-Image generation.\n"
        f"Previous generation prompt:\n{previous_prompt.strip()[:10_000]}\n\n"
        f"Additional user instruction:\n{instruction.strip()[:6_000]}\n\n"
        "Keep relevant subject, setting, style, and composition details from the previous prompt "
        "unless the additional instruction changes them."
    )


def _preservation(text: str) -> str:
    lowered = text.lower()
    if any(term in lowered for term in (
        "preserve", "keep ", "retain", "unchanged", "same subject", "same composition", "closely",
        "same character", "same person",
    )):
        return "close"
    if any(term in lowered for term in ("loosely", "creative", "dramatically", "completely restyle")):
        return "creative"
    return "balanced"


def _preservation_constraints(text: str) -> tuple[str, ...]:
    lowered = text.lower()
    constraints: list[str] = []
    if any(term in lowered for term in ("subject", "person", "character", "face", "identity")):
        constraints.append("subject_identity")
    if any(term in lowered for term in ("pose", "position", "posture", "gesture")):
        constraints.append("pose")
    if any(term in lowered for term in ("composition", "framing", "layout", "camera angle", "perspective")):
        constraints.append("composition_and_camera")
    if any(term in lowered for term in ("scene structure", "background", "environment", "setting")):
        constraints.append("scene_structure")
    if any(term in lowered for term in ("lighting", "light", "color", "colour", "shadows")):
        constraints.append("lighting_and_color")
    return tuple(constraints)


@dataclass(frozen=True)
class ExecutionPlan:
    intent: str
    kind: str
    operation: str
    model: str
    backend: str
    prompt: str
    original_prompt: str
    warnings: tuple[str, ...] = ()
    preservation: str = "balanced"
    preservation_constraints: tuple[str, ...] = ()
    identity_strength: float = 0.5
    identity_entities: tuple[IdentityEntity, ...] = ()
    identity_mode: str = "none"
    identity_instruction: str = ""
    context_mode: str = "none"
    confidence: str = "medium"

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["warnings"] = list(self.warnings)
        value["preservation_constraints"] = list(self.preservation_constraints)
        return value


def _lowered_terms(text: str) -> set[str]:
    lowered = text.lower()
    return {term for term in EDIT_TERMS | NEW_TERMS if term in lowered}


def interpret_request(
    content: str,
    *,
    reference_ids: Iterable[str] = (),
    image_ids: Iterable[str] = (),
    parent_image_id: str | None = None,
    latest_image_id: str | None = None,
    moss_model: str = "OpenMOSS-Team/MOSS-Image-SFT",
    intent_hint: str | None = None,
    preservation_hint: str | None = None,
    previous_prompt: str | None = None,
) -> ExecutionPlan:
    """Build a MOSS text-to-image plan without accepting visual conditioning."""
    content = content.strip()
    refs = tuple(reference_ids)
    images = tuple(image_ids)
    if not content:
        raise ValueError("A message must contain an instruction")
    if intent_hint == "question" or _is_visual_question(content):
        raise ValueError("No local vision-language model is configured for image questions yet")
    if refs or images or parent_image_id:
        raise ValueError(
            "MOSS-Image is text-to-image only in this installation; uploaded images are not accepted as model input. "
            "Remove the attachment and submit a text prompt."
        )
    if intent_hint not in {None, "generate", "edit", "question"}:
        raise ValueError("The request interpreter returned an unsupported intent")

    edit_requested = intent_hint == "edit" or _has_edit_intent(content)
    fresh_requested = _requests_fresh_generation(content)
    prompt_continuation = bool(latest_image_id and previous_prompt and edit_requested and not fresh_requested)
    if edit_requested and not prompt_continuation and not fresh_requested:
        raise ValueError(
            "Image editing has been removed. Continue a conversation with a previous generated prompt, "
            "or start a new MOSS-Image text-to-image request."
        )

    identity_entities = extract_named_identities(content)
    identity_text, identity_mode, identity_warnings = identity_instruction(identity_entities)
    model_prompt = append_identity_instruction(content, identity_text)
    if prompt_continuation:
        model_prompt = _prompt_continuation(previous_prompt or "", model_prompt)
        identity_warnings = tuple(identity_warnings) + (
            "This follow-up reuses prompt context only; MOSS-Image does not receive the previous raster.",
        )
    preservation = preservation_hint if preservation_hint in {"close", "balanced", "creative"} else _preservation(content)
    return ExecutionPlan(
        intent="generate",
        kind="generate",
        operation="text-to-image",
        model=moss_model,
        backend="vllm-omni-moss-image",
        prompt=model_prompt,
        original_prompt=content,
        warnings=identity_warnings,
        preservation=preservation,
        preservation_constraints=_preservation_constraints(content),
        identity_strength=0.8 if preservation == "close" else 0.5,
        identity_entities=identity_entities,
        identity_mode=identity_mode,
        identity_instruction=identity_text,
        context_mode="prompt-continuation" if prompt_continuation else "none",
        confidence="high" if not _lowered_terms(content) else "medium",
    )
