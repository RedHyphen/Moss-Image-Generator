# Model Research

Research date: 2026-10-21. Official documentation and source behavior take
priority over promotional feature claims.

## MOSS-Image decision

MOSS-Image-Base and MOSS-Image-SFT are the primary local text-to-image models
served through vLLM-Omni. Base is the base checkpoint; SFT is the supervised
fine-tuned variant with better instruction following, text grouping, and
structured layouts. The application defaults to SFT while allowing Base when
the authorized worker serves it.

The verified generation interface supports prompts, optional negative prompts,
size, seed, sampling steps, guidance, flow shift, and structured layout JSON.
The official API does not document an uploaded-image tensor, img2img input,
inpainting input, or multi-reference conditioning. Therefore MOSS-only mode
does not expose image editing and never converts an uploaded image into a
caption while claiming that the pixels were conditioned.

The layout editor is generation-only. It compiles the user-facing absolute
pixel layout into the model's documented coordinate format at the request
boundary.

## Hardware findings

The recommended local configuration is 8GB VRAM. The official MOSS/vLLM recipe
uses BF16 for the DiT and text encoder, so this project selects an explicitly
experimental FP16 profile for constrained or older GPU configurations. Resident
5B serving is not expected to fit in 8GB; local vLLM-Omni layerwise diffusion
offload and a serialized queue are required to reduce GPU residency. The 8GB
baseline output ceiling is 589,824 pixels. The automatic ceiling now reserves
20% of visible VRAM and rounds down to a standard pixel budget, so higher-memory
hosts may receive a larger validated ceiling.

At the time of this research record, the 8GB path was configuration-tested but
not live-inference-validated on the target card. Current runtime evidence is
maintained in `docs/TEST_RESULTS.md`: the host's RTX 4060 Ti 16GB has passed a
real `/v1/images/generations` request, while an 8GB target-host
`check_hardware.sh` run and smoke test are still required before treating that
profile as validated.

The web app and worker run on the same host. The web app validates returned PNG
data locally and never falls back to a public or remote image service.

## Editing research outcome

Stable Diffusion img2img and Qwen-Image-Edit were investigated as possible
secondary backends. They are separate model families, carry additional
memory/licensing/integration costs, and are not MOSS-Image conditioning. The
previous Diffusers path produced poor identity preservation for the target
workflow, so it has been removed from the active product rather than kept as a
misleading fallback. A future editor must be explicitly selected, verified on
the available hardware, and integrated behind a separate capability adapter.

## Licensing and references

MOSS-Image Base/SFT model cards list Apache-2.0. Review the current model card
before redistributing weights or a derivative service. Useful references:

- [MOSS repository](https://github.com/OpenMOSS/MOSS-Image)
- [MOSS user guide](https://github.com/OpenMOSS/MOSS-Image/blob/main/USERGUIDE.md)
- [MOSS SFT model card](https://huggingface.co/OpenMOSS-Team/MOSS-Image-SFT)
- [Official vLLM-Omni recipe](https://recipes.vllm.ai/OpenMOSS-Team/MOSS-Image-Base)
