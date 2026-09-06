---
author: Neil Mitchell
last_modified_by: Neil Mitchell
---

# Third-Party Notices

## World of Warcraft

World of Warcraft and related names and marks are trademarks of Blizzard
Entertainment, Inc. This project is an independent fan-made tool and is not
affiliated with, endorsed by, or sponsored by Blizzard Entertainment.

## Pizza Warriors artwork

Files under `assets/` contain user-supplied Pizza Warriors branding. They are
included for this project's configured presentation workflow and are not
licensed for reuse under the MIT License.

## Repository social preview

`docs/assets/social-preview.jpg` combines the user-supplied Pizza Warriors
branding with an original AI-generated background and deterministic typography.
It is repository presentation artwork, not part of the Python package.

## Dependencies

Python dependencies retain their respective upstream licenses. Consult
`uv.lock` and each package's distribution metadata for exact versions and terms.

The optional speech extra pins Vosk 0.3.45, distributed under the Apache License
2.0. The local `vosk-model-small-en-us-0.15` model is also listed by Alpha
Cephei under Apache 2.0. The model is downloaded separately into the ignored
`.models\` directory and is not redistributed in this repository.

## Optional local highlight intelligence

The project source remains under its own MIT License. Dependencies, downloaded
runtime binaries, and model weights retain their upstream terms. Downloading
them for local use does not relicense them under this repository's MIT License.
The `.tools/`, `.models/`, and `.venv/` contents are local installation materials,
not redistribution payloads included in the project package.

| Material | Upstream terms and source |
| --- | --- |
| faster-whisper | [SYSTRAN MIT License](https://github.com/SYSTRAN/faster-whisper/blob/master/LICENSE) |
| CTranslate2 | [OpenNMT MIT License](https://github.com/OpenNMT/CTranslate2/blob/master/LICENSE) |
| Whisper medium.en weights converted for faster-whisper | [SYSTRAN model card](https://huggingface.co/Systran/faster-whisper-medium.en) and [OpenAI Whisper MIT License](https://github.com/openai/whisper/blob/main/LICENSE) |
| Portable Ollama | [Ollama MIT License](https://github.com/ollama/ollama/blob/main/LICENSE); preserve additional notices supplied with the downloaded distribution |
| Qwen3.5-9B model weights, default `qwen3.5:9b` | Apache License 2.0, as listed by the [official Qwen model card](https://huggingface.co/Qwen/Qwen3.5-9B); [Ollama distribution entry](https://ollama.com/library/qwen3.5:9b) |
| Gemma 3 model weights (`gemma3:4b`), optional evaluated model rather than the default | [Google Gemma Terms of Use](https://ai.google.dev/gemma/terms) and [Gemma Prohibited Use Policy](https://ai.google.dev/gemma/prohibited_use_policy) |
| Optional NVIDIA cuBLAS/CUDA libraries | [NVIDIA CUDA software license and supplement](https://docs.nvidia.com/cuda/eula/index.html), plus notices accompanying the installed wheels |
| Optional NVIDIA cuDNN libraries | [NVIDIA cuDNN software license](https://docs.nvidia.com/deeplearning/cudnn/backend/latest/reference/eula.html), plus notices accompanying the installed wheels |

The explicit setup script verifies portable Ollama 0.33.3 against its pinned
archive hash, selects a specific faster-whisper medium.en revision, and records
the installed editorial-model digest. The default Qwen weights retain their
Apache 2.0 license; separately evaluated optional Gemma weights retain Google's
model terms. Neither is bundled or relicensed under this project's MIT License.
Model terms remain separate from Ollama's runtime license. Any future
redistribution of weights or NVIDIA/runtime materials needs its own review of
the applicable upstream conditions and notices. See the
[local intelligence setup guide](docs/highlight-intelligence.md) for locations
and the recorded provenance file.
