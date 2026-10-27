# Third-party notices

Moss-Image-Generator is an independent portfolio project. This file records the
major upstream components used by the project; it does not replace the license
files distributed with those components or grant rights to their trademarks.

## MOSS-Image

The application invokes MOSS-Image Base/SFT by model identifier and does not
include the model weights in this repository. The official MOSS-Image project
and MOSS-Image-SFT model card identify their released code/weights as Apache
License 2.0. Users must obtain and use model weights under the applicable
upstream terms.

- Project: <https://github.com/OpenMOSS/MOSS-Image>
- Model card: <https://huggingface.co/OpenMOSS-Team/MOSS-Image-SFT>
- License: <https://huggingface.co/OpenMOSS-Team/MOSS-Image-SFT/blob/main/LICENSE>

## vLLM-Omni

The local inference worker uses the vLLM-Omni project and its published
container/package distribution. vLLM-Omni is licensed under Apache License 2.0.

- Project: <https://github.com/vllm-project/vllm-omni>
- License: <https://github.com/vllm-project/vllm-omni/blob/main/LICENSE>

## Application dependencies

Python and frontend dependencies are declared in
`backend/requirements.txt`, `backend/requirements-dev.txt`,
`frontend/package.json`, and `frontend/package-lock.json`. Their individual
license and notice files remain authoritative. Any redistributed Docker image,
frontend bundle, or other artifact must be reviewed against the complete
resolved dependency inventory before distribution.
