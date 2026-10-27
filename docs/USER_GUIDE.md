# User Guide

## Sign in and conversations

Open `http://127.0.0.1:8002`. Sign in with your account and create a
conversation.
Conversations, images, jobs, and uploaded references are private to the
authenticated account. The sidebar supports search and reopening previous
conversations after a restart.

To remove your own history, use **Clear history** in the top-right toolbar and
confirm the prompt. This removes your conversations, messages, uploaded
references, generated outputs, and completed/failed job records for the signed-in
account only. It does not remove your account, model files, or another account's
data. The action is refused while your account has
queued or running generation work.

## Generate an image

Type a natural-language prompt and press **Enter** to send. Use
**Shift+Enter** for a new line. The default model is MOSS-Image-SFT on the
private vLLM-Omni worker. Advanced controls can set a supported model, size,
seed, steps, guidance, negative prompt, and output count. Layout JSON is
generation-only and can be built with the visual layout editor.

The application does not apply an artistic NSFW or subject-category filter;
the negative-prompt field starts with conservative quality and subject-count
terms (including duplicate-person and multiple-subject safeguards) and remains
entirely user-controlled. File validation, authentication, and LAN isolation
remain enabled because they protect the host and private data rather than
limiting artistic content. The selected model may still have behavior learned
during its own training.

The queue shows the actual job stage. When the worker supplies a numeric
progress value, the interface displays 1–100%; otherwise it displays a live
indeterminate state rather than inventing a percentage. Generated images keep
their prompt, model, seed, resolution, and settings in local metadata.

In the advanced controls, enable **Generate randomized variations** to
request at least two independently seeded outputs at once. Set the variation
count from 2 to 4; the fixed seed control is disabled while this mode is
active. The same batch setting is used by the conversational send action.

### Additive follow-ups

After MOSS generates an image, you can send a follow-up such as “add a red
cape” or “make the lighting warmer.” The application carries the previous
generation prompt forward, appends the new instruction, and records the new
output as a child of the previous version. This is prompt-context continuity,
not pixel-level image editing: the verified MOSS endpoint does not accept the
previous raster as an input, so the assistant reports that visual continuity
is not guaranteed. The current product has no uploaded-image editing workflow.

## MOSS-only limitation

The verified MOSS-Image API is text/layout generation only. This deployment
does not provide image-to-image editing, inpainting, outpainting, or
multi-reference conditioning. Upload controls are intentionally hidden in the
MOSS-only interface. If an old client sends an image-conditioned request, the
API rejects it clearly before queueing; it never discards the pixels and
pretends to have edited them.

## Gallery and privacy

Use the gallery to download images, favorite them, load a saved prompt and its
generation settings into the controls, inspect metadata, and delete outputs.
Original files are retained
until explicit deletion. Account ownership is checked for every conversation,
image, reference, and job route; another account receives a not-found response
for private records.

## Offline use

After the worker and dependencies are installed, set `MOSS_OFFLINE_MODE=true`
to disable optional downloads. Existing local conversations and generated
outputs remain available, but generation still requires the configured private
vLLM worker unless it is also running locally.
