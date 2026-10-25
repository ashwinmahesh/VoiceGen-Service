# Expressiveness and accent additions

These projects keep one shared API/UI and model-specific capability checks. All checkpoints, vectors, caches and outputs stay under the configured SSD data root. Serving remains offline. No voices, recordings or catalog annotations were replaced.

## Qwen CustomVoice

Installed `mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit` at revision `41d3337e8b7f2843a75841595fc14e4b9a7a4b96` (~3.08 GB including its decoder). Apache-2.0. Required files and published SHA256/Git blob hashes are in model_specs.json.

The **Expressive presets** tab uses the runtime's native `generate_custom_voice`: Ryan, Aiden, Vivian, Serena, Uncle Fu, Dylan, Eric, Ono Anna and Sohee. Emotion/style instructions work while retaining the selected preset identity. It automatically switches checkpoints, unloading the old one before loading another. These are built-in speakers, not extra voices added to VoiceDesign. A generated sample can still be saved to the reusable voice catalog. Reusing that recording uses Base, with Base's existing limits.

## Experimental emotion steering

Downloaded 13 small `ryan_*.qlsteer` vectors (~3.1 MB total), with their MIT license, from [gabriele-mastrapasqua/qwen3-tts](https://github.com/gabriele-mastrapasqua/qwen3-tts/tree/ef339be58a778b062e1c14382347964552eae007/presets/steer/emotion). The pinned revision and Git blob hashes are in extension_specs.json. The project does not run that repository's scripts or install its separate C engine.

A reviewed MLX port follows the upstream decode-only, norm-preserving residual addition at Talker layers 21–25, with strength decaying by 0.985 per decoded frame. Text prefill is unmodified. Wrappers are restored in `finally`, including failures. Checkpoint weights are never changed. Only Qwen **1.7B CustomVoice preset** mode is exposed; transferring these directions to Base/VoiceDesign or cloning through CustomVoice has not been validated and is rejected.

Available directions: sadness (`sad`), joy, anger, fear, disgust, surprise, contempt, awe, nostalgia, disapproval, remorse, outrage and despair. Default strength 8, range 0–12; 0 is an explicit unsteered baseline. When the API temperature is omitted, steering uses 1.1. The UI selects 1.1 when enabling steering from its default temperature. Prompt direction remains an optional additional input. Selecting steering while the UI still has its untouched Natural preset clears that generic instruction to start closer to the upstream pure-steering recipe.

**Experimental:** the upstream recipe and older vector manifest recommend different strengths. The conservative local default is 8; the range includes the current recipe's 12. Higher values may change timbre, articulation or stability. The 8-bit MLX port is a local adaptation of the C/BF16 work, not an official MLX Audio feature. Structural checks and decoded-audio A/B tests establish that steering changes the result and does not leak into the next request; they do not establish a perceptual emotion-quality improvement. Audition the output for each voice/language.

Sources: [current upstream recipe](https://github.com/gabriele-mastrapasqua/qwen3-tts/blob/ef339be58a778b062e1c14382347964552eae007/docs/emotion-THE-recipe.md), [vector manifest](https://github.com/gabriele-mastrapasqua/qwen3-tts/blob/ef339be58a778b062e1c14382347964552eae007/presets/steer/emotion/MANIFEST.md), [steering implementation](https://github.com/gabriele-mastrapasqua/qwen3-tts/blob/ef339be58a778b062e1c14382347964552eae007/qwen_tts_talker.c). See THIRD_PARTY_NOTICES.md.

## Accents and VoxCPM2 continuation

Both UIs now have a separate **Accent direction** field. This appends a literal natural-language accent instruction to supported backend instructions: design on both models, Qwen presets, and VoxCPM2 standard reference mode. It is prompt guidance, not a trained accent adapter. Qwen Base rejects this field when nonempty because Base does not accept direct style/accent instructions. A recording containing the desired accent is usually the stronger conditioning input. Romanian-accented **English** should use English in Qwen, rather than pretending Romanian is an installed target language.

VoxCPM2 additionally exposes its already-installed **continuation** mode. Select Saved voice or Reference audio, choose **Continue performance**, and supply the exact words in that recording (or save the transcript in voice details). The native adapter passes `prompt_audio` + `prompt_text`, carrying the recorded performance context into the new line. Continuation follows the recorded delivery and rejects new emotion/accent instructions in this service. Default reference mode still uses `ref_audio` and permits flexible delivery instructions. Continuation can carry room noise or mannerisms too; it does not guarantee identity/accent preservation. Generation metadata records the actual mode, transcript and instructions.

No compatible, published Romanian-accent or general expressiveness LoRA pack was identified for the installed VoxCPM2 MLX runtime. [Upstream VoxCPM2 LoRA support](https://github.com/OpenBMB/VoxCPM/tree/main/conf/voxcpm_v2) is a training/inference mechanism in its PyTorch implementation; it is not a downloadable accent pack, and the pinned MLX implementation has no corresponding ready-to-use loader. Irrelevant language fine-tunes and incompatible C-format expression packs were not installed. A custom accent fine-tune would need a suitable recording dataset and a compatible conversion/training workflow.

## Commands and verification

`make download` installs/verifies the selected checkpoints and extension assets. `make download-extensions` fetches only the pinned small vector pack where selected. `make verify` hashes all model and extension files. The pack has no effect unless a supported request selects it. `make test` includes fake-backend API/adapter tests plus pure-NumPy vector validation, norm preservation, decode scheduling and state-restoration tests; no model inference or downloads occur in tests.

`logs/extension-smoke-qwen.json` contains the real CustomVoice strength-0 / strength-8 / strength-0 results and paths. The first and last baselines match decoded samples; the steered take differs and is finite, nonsilent and below its token cap. `logs/extension-smoke-vox.json` records a real VoxCPM2 continuation test with the saved fictional aristocrat recording. Generated tests stay local; this work does not automatically upload new clips to Drive.

VoxCPM2 continuation follows the recorded delivery and accepts no new emotion/accent directions in this service. Use its standard reference mode to apply prompt directions.
