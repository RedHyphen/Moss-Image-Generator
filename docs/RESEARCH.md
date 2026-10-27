# MOSS-Image Research

Research date: 2026-10-21. Official sources and verified runtime behavior take
priority over promotional claims.

## Verified capabilities

MOSS-Image-Base and MOSS-Image-SFT are 5B text-to-image models. The official
runtime supports natural-language prompts, optional negative prompts, seed,
size, sampling steps, guidance, flow shift, and structured layout prompts.
Dimensions are expected to be positive multiples of 16; the documented
workflow uses Euler sampling and approximately 50 or more steps. SFT is the
end-user default because its supervised tuning improves instruction following,
text grouping, and layout organization compared with Base.

The layout editor stores an absolute-pixel draft and compiles it to the
model-facing coordinate format only at the MOSS request boundary. Layout
generation is not image editing.

## Editing boundary

The official MOSS generation API does not document an uploaded-image tensor,
img2img input, inpainting input, or multi-reference conditioning. Official
SGLang notes likewise describe generation, not implemented image editing.
The active application therefore rejects image-conditioned requests before
queueing and never captions an upload to recreate it while claiming visual
conditioning.

Stable Diffusion/Diffusers and Qwen-Image-Edit were evaluated as separate local
editing candidates. The former did not meet the target identity/realism goal;
the latter is too large for the current deployment. Neither is MOSS-Image
conditioning, so neither is installed or silently used. A future editor must
be a separately selected, capability-verified local backend.

## Hardware and acceleration

The recommended local configuration is 8GB VRAM. The MOSS 5B runtime is larger
than a comfortable resident budget, so the local worker uses experimental FP16,
vLLM-Omni layerwise offload, and serialized requests. Two-GPU serving is not
enabled. Exact fit and throughput remain dependent on the selected GPU, driver,
runtime build, resident processes, and workload.

The web app and worker run on the same host and communicate over loopback. The
web app validates returned `b64_json` PNG output locally.

Community quantized/distilled artifacts and ComfyUI integrations remain
experimental or separate deployment choices; they are not enabled by this
studio's default path.

## Sources

- [MOSS repository](https://github.com/OpenMOSS/MOSS-Image)
- [MOSS user guide](https://github.com/OpenMOSS/MOSS-Image/blob/main/USERGUIDE.md)
- [Base model card](https://huggingface.co/OpenMOSS-Team/MOSS-Image-Base)
- [SFT model card](https://huggingface.co/OpenMOSS-Team/MOSS-Image-SFT)
- [Official vLLM-Omni recipe](https://recipes.vllm.ai/OpenMOSS-Team/MOSS-Image-Base)
- [Official ComfyUI integration](https://github.com/OpenMOSS/MOSS-Image/tree/main/comfyui)
