# Voice models

Description-based voice design is supported by VoxCPM2, Qwen3-TTS, and MOSS. The studio keeps its design tab visible but disabled for other backends, with a tooltip listing these alternatives when none is ready. If a capable model is set up, the tab opens with an empty model selection instead. Generator admission rejects unsupported design requests with 422; standalone queue admission retains its deferred validation behavior. Supported-model hints come from the model registry and backend mode declarations.

All backends share generator-owned [resource history](docs/observability.md). Process-tree sampling includes native speech and LLM children, so CPU/RSS are captured for Higgs and MOSS as well as in-process MLX/MPS models. Device-wide macOS GPU counters are labeled separately. Samples identify the speech backend, actual model/LLM phase, session, and job attempt. This needs no additional runtime package or model download.

Local Voice Studio uses one UI, API v4, catalog, shared SQLite queue and serial inference worker. All ready backends are available without restarting the generator. `make serve` requires at least one model with verified assets and a compatible runtime; otherwise it exits before model/LLM prompts or service startup and explains how to finish setup. Startup (`make serve MODEL=higgs`) chooses the saved default for both APIs. The studio model selector or per-request `model` chooses each new job. `/v1/models` lists ready IDs and their modes; `/api/capabilities?model=higgs` supplies the selected model’s controls and defaults.

Only one speech backend is resident at a time. Consecutive jobs reuse its adapter; switching stops/reaps it before loading another. Candidate batches finish together. Higgs/MOSS still use per-output native children to release speech weights after each take. Missing assets/runtimes reject new work with a setup hint; already admitted work stays blocked until its model returns, while other models proceed. All models use localhost:5743 unless `PORT` is overridden.

```sh
make setup MODEL=chatterbox
make serve MODEL=chatterbox PORT=8017
# Or use the selected environment directly:
DATA_DIR=/absolute/data .venv-chatterbox/bin/python server.py --model chatterbox --port 8017
```

Omitting MODEL selects **Higgs TTS 3** from `service_defaults.json`; it uses `.venv-native`. The same default applies to queue submissions that omit `model`. The legacy `service.json` continues to identify the original Vox storage/environment layout and is not changed by choosing a different default. Explicit MODEL uses `backends/MODEL/{service,model_specs,extension_specs}.json`. Selecting the original service.json backend preserves its existing DATA_DIR/weights and .venv. Other backends use DATA_DIR/weights/MODEL and the environment below. `WEIGHTS_DIR=/absolute/existing/weights` overrides only model files, allowing reuse without copying/downloading them. Otherwise ignored storage.json may provide backend_weights keyed by backend ID. Extension files similarly use EXTENSIONS_DIR, then backend_extensions, then DATA_DIR/extensions. Environment overrides take priority over saved mappings; setup retries preserve mappings. The selected mapped paths receive the same missing-volume checks as other data paths.

| MODEL | Environment / bootstrap Python | Implemented voice modes and controls |
| --- | --- | --- |
| voxcpm2 | .venv when original, otherwise .venv-mlx / 3.12–3.14 | Existing MLX BF16 design/reference/continuation/combined, steps, CFG, supported prompt directions |
| qwen3 | .venv when original, otherwise .venv-mlx / 3.12–3.14 | Existing MLX 8-bit VoiceDesign/Base/CustomVoice; preset directions/optional steering; Base reference transcript |
| chatterbox | .venv-chatterbox / 3.11–3.12 | Original English 500M reference cloning, exaggeration 0–1 (default .3, provisional character setting), cfg_weight 0–1 (default .5), temperature .05–2 (default .8) |
| indextts | .venv-indextts / 3.11 | IndexTTS 2.5 reference cloning, separate emotion recording or eight-value vector, duration_factor .7–1.5, segment interval 0–2000 ms, temperature |
| moss | .venv-native / 3.11–3.12 | MOSS VoiceGenerator description-based design only, English/Chinese, temperature, seed, audio-frame cap |
| fish | .venv-mlx / 3.12–3.14 | Fish S2 Pro 8-bit reference cloning with exact transcript, inline `[free-form delivery tags]`, temperature, audio-token cap |
| higgs | .venv-native / 3.11–3.12 | Higgs TTS 3 Q8_0 reference cloning with exact transcript, inline control tokens, temperature, audio-token cap |

Chatterbox Original is intentionally selected over Turbo: Turbo's vocal-event tags and control surface differ. Original does not accept emotion/accent instructions or reference transcripts. Its upstream public `generate()` fixes the output ceiling at 1000 tokens; this API rejects other max_tokens values and hides the ineffective selector. It conditions the speaker from up to the first six seconds and decoder from ten seconds; uploads retain the full original. No exact accent/emotion guarantee follows from exaggeration.

IndexTTS 2.5 uses FP32 MPS, no BF16, CUDA extensions, DeepSpeed, accelerated GPT, or torch.compile. Its optional Qwen text-to-emotion model is not installed: natural-language emotion/accent fields are rejected. Choose an uploaded `emotion_reference_id` or `emotion_vector` in this order: happy, angry, sad, afraid, disgusted, melancholic, surprised, calm. Each value must be finite and 0–1, total at most 1. `emotion_alpha` 0–1 blends the selected emotion. Speaker identity comes from voice_id/reference_id. References are consumed for up to 15 seconds. `duration_factor > 1` is slower; interval_silence defaults to 400 ms **between upstream segments**, not automatically between every sentence. With punctuation pacing disabled the wrapper does not split sentences; the configurable pacing mode below explicitly generates phrases with the same speaker/emotion references. `max_tokens` maps to max_mel_tokens per segment, capped at 1500. `language` must be English, Chinese, Japanese, Spanish, or Arabic. Explicit upstream phoneme annotations, e.g. `<going|G OW1 . IH0 NG>`, are passed through; optional NeMo text normalization is disabled rather than silently changing supplied speech.

MOSS uses the inspected audio.cpp `moss_voicegen` family, not an assumed generic MLX implementation. Its self-contained GGUF includes the required v1 decoder codec. It designs a speaker from the combined description/emotion/accent instruction; it cannot clone the supplied Strahd recording or lock identity across lines. Input is limited to 500 characters and sent as one native chunk to avoid changing designed speakers at chunk boundaries. `max_tokens` maps to max_frames; upstream can raise that ceiling to its text-derived minimum. It is a safety ceiling, not requested duration. Audio sampling temperature defaults to 1.5; text-stream temperature/top-p/top-k retain the model-specific upstream defaults.

The pinned Fish 8-bit conversion quantizes embeddings and fast-decoder layers excluded by MLX Audio 0.6.1's default predicate. fish_loader.py loads the verified tensor layout using the shared quantizer's checkpoint-scale selection, then validates weights strictly. It never drops scale tensors or edits installed runtime/model files.

Fish and Higgs do not expose natural-language description design through this wrapper. Select a reference and exact transcript. Keep inline tags explicitly in text: Fish `[whisper]`, `[pause]`, `[with strong accent]`; Higgs `<|emotion:amusement|>`, `<|prosody:expressive_high|>`, `<|prosody:pause|>`, `<|prosody:long_pause|>`. Higgs sound events also require matching written onomatopoeia. Text is not automatically rewritten or embellished. The wrapper offers one reference per request, even though upstream Higgs supports more. No backend here exposes the upstream streaming transport; all return durable WAV jobs.

Higgs's UI/recommended audio budget is 2,000 tokens per native text chunk; API omission retains 1,000. `higgs.text_chunk_size` controls the native `--text-chunk-size` argument (80–1,024 characters, default 1,024). A missing EOC at the cap returns an actionable failure read from a bounded native-log tail, removes temporary output, and preserves successful batch siblings. The wrapper does not enable `allow_incomplete_generation` or automatically retry with a larger budget.

Higgs's Emotion, Prosody and Style inputs search its official catalog. Custom wording can be previewed through the existing local LLM, restricted to the selected category, then applied after review. Other native controls remain intact. The speech model still receives recognized tags only; this does not add arbitrary natural-language style or accent support. No model/runtime download is needed for this UI change; custom suggestions require the separately set-up prompt LLM.

## Installation and storage

Prerequisites: Apple Silicon macOS and Git/Make; Higgs and MOSS also need Xcode command-line tools for the native C++ build. Setup chooses Python automatically. Use `VENV` for another internal speech environment location. Setup installs a complete pinned runtime lock with `--no-deps`, then checks dependencies; this prevents an upstream unpinned Git dependency from overriding the explicit Perth pin. The locks target macOS arm64 and are not CUDA/Linux recipes.

### Python selection

Setup announces safe reruns before the model menu: verified model files are reused without downloading them again, while dependencies and support files may be checked or updated in place. The download step reports each reused model. Pinned revisions remain pinned; rerunning setup does not select newer model weights. Choose **q** in the setup menu or press **Ctrl+C** at any setup stage to cancel. The setup supervisor stops only its own installer/build process group, preserves existing downloads and saved data, and lets a later setup resume or repair incomplete downloads. Genuine installation failures still report failure.

Setup first reuses the target environment when compatible, then checks installed interpreters, including Homebrew, framework installations, and project-local Python bases. Only when none matches does it download a base into `<project>/.python`, using a pinned, checksum-verified official [uv](https://docs.astral.sh/uv/guides/install-python/) binary under `.bootstrap`. Downloads and tool caches stay inside the serving project; no global packages, shell-profile edits, or system Python changes are needed. An entirely Python-free machine can bootstrap setup this way too. Serving only uses installed interpreters and environments and never downloads.

| Environment | Compatible standard CPython on Apple Silicon | Download fallback |
| --- | --- | --- |
| VoxCPM2, Qwen, Fish | 3.12–3.14 | 3.14 |
| Prompt-expansion LLM | 3.12–3.14 | 3.14 |
| Higgs, MOSS, Chatterbox | 3.11–3.12 | 3.11 |
| IndexTTS | 3.11 | 3.11 |
| Independent queue | 3.11–3.14 | 3.11 |

These bounds follow the pinned dependencies: MLX environments include NumPy/SciPy requiring 3.12+, the native lock includes NumPy 1.26 wheels through 3.12 and SciPy requiring 3.11+, and IndexTTS requires Python below 3.12. PyPy, Intel/Rosetta interpreters, free-threaded Python, and newer unvalidated Python minors are not selected. Existing 3.11/native and 3.14/MLX environments remain the inference-tested defaults; accepting another compatible minor does not establish identical speech or performance.

Optional interpreter paths: `BOOTSTRAP_PYTHON` for speech, `LLM_PYTHON` for the enhancer, and `QUEUE_BOOTSTRAP_PYTHON` for the queue. An explicit incompatible path fails without falling back. An incompatible or broken existing environment is preserved, including one whose Python base was removed. Select a new speech `VENV`, or move the affected environment aside and rerun setup. Setup never replaces it automatically. The small terminal chooser can run with any available CPython 3.9+; actual speech/LLM/queue environments must meet the table above.

The first terminal `make setup MODEL=...` asks for a data folder. Enter means this project; an absolute alternative creates model/data links and persists ignored storage.json. Retries reuse it. `DATA_DIR=...` or `NONINTERACTIVE=1` skips the prompt. Missing external volumes/dangling links fail before downloads. Source, environment and native build stay internal; weights/codecs, model caches, uploads, catalogs, jobs, logs and outputs belong in the configured data root. Build-only temporary scratch uses a bounded `/private/tmp/voice-build-*` directory and is removed on completion. Runtime caches/temp stay in DATA_DIR.

```sh
make setup MODEL=indextts BOOTSTRAP_PYTHON=/path/to/python3.11 DATA_DIR=/absolute/ssd/studio NONINTERACTIVE=1
make download MODEL=indextts       # resume exact required assets
make verify MODEL=indextts         # full upstream hashes
make serve MODEL=indextts PORT=8017
make health MODEL=indextts PORT=8017
make test MODEL=indextts
```

`make setup-env` installs runtime only. Native setup fetches immutable audio.cpp commit `082177ecac45f1965929ded33cd81a85c13542f1`, builds only MOSS/Higgs families with Metal and no OpenMP, and refuses a different/modified native checkout. Runtime Python versions and model hashes are in each backend directory. Normal serve loads only local verified files; no implicit model download occurs. An `AUDIOCPP_BIN` override must refer to the same reviewed pinned build; it is an operator override, not an automatic binary download.

Do not run two servers with the same DATA_DIR. A process-lifetime data lock prevents a second server from marking the first one's jobs interrupted; startup also checks the port before recovery. The global GPU file lock covers load, generation and unload. It does not free another service's resident allocations. Use separate validation data directories and explicitly set the same GPU_LOCK_FILE. Never stop an unrelated listener or disable MPS safeguards to make a test fit.

Native models run in one subprocess per job, with no second HTTP server/UI. Each subprocess loads locally, generates, exits and frees memory before the reservation is released. Health correctly remains model_loaded=false between jobs; every native job is cold. Native generation_seconds includes process startup/model load/synthesis and is labeled accordingly; load_seconds is null because it is not separately measured. `/usr/bin/time -l` records the child peak RSS, which is not total system/GPU RAM.

## Provenance and licenses

All downloaded conversion revisions, required files, sizes, SHA256/Git blob hashes are in `backends/*/model_specs.json`; `verified.json` is written externally only after verification. MLX Vox/Qwen use their existing pinned conversions. New model downloads are approximately Chatterbox 3.19 GB, IndexTTS including auxiliaries 7.08 GB, MOSS/codec 6.02 GB, Fish/codec 6.73 GB, and Higgs/codec 5.10 GB. The Index emotion-text model and unrelated runtime examples are not model downloads.

| Backend/assets | License and provenance |
| --- | --- |
| Chatterbox | MIT code/weights; pinned official runtime 5de7a54; Perth ff1c8ac watermark retained |
| IndexTTS 2.5 | Custom Bilibili Index model use license, including outputs and downstream conditions; **not MIT/Apache**; official runtime d9e41aa |
| Index auxiliaries | Pinned facebook/w2v-bert-2.0, funasr/campplus, NVIDIA BigVGAN; consult their pinned cards/licenses alongside the model license |
| MOSS and v1 codec | Apache-2.0 upstream cards, packaged together in immutable audio-cpp GGUF |
| Fish S2 Pro | Fish Audio Research License; research/noncommercial use, separate commercial license |
| Higgs TTS 3 | Boson research/noncommercial license with a conditional creator-use grant; retain attribution and full terms |
| Native runtime | audio.cpp MIT; vendored dependencies retain their upstream notices |

Copies of the exact available primary license texts are in `third_party/`. Documentation pointers in upstream-provenance.json are explicitly distinct from the downloaded conversions: the converter's exact original source-weight revision is not asserted when unpublished. The immutable downloaded conversion bytes are verified. User-provided references/outputs stay private; no paid generation, upload, or cloud publishing is part of setup/smoke.

## Real smoke and interpretation

```sh
# Reference backends require an authorized recording; exact transcript required by Fish/Higgs:
.venv-mlx/bin/python -m tests.smoke_test --port 8017 --reference /absolute/reference.wav --transcript 'Exact recorded words.' --text 'Welcome home.'
# Design-only MOSS:
.venv-native/bin/python -m tests.smoke_test --port 8017 --description 'A calm, cultured baritone.' --text 'Welcome home.'
```

Smoke checks decode WAV, finite/nonsilent data, duration/rate and saved metadata; they do not establish accent, emotion, completeness, or identity by listening. Timings depend on hardware and first-use compilation. Chatterbox and Index completed MPS smoke on this Apple M6/24 GB machine but showed high end-of-job MPS driver allocations around 27 GB and slow synthesis; treat them as experimental. Driver allocation is an allocator figure, **not measured peak physical/system RAM**. Do not disable high-watermark safeguards. Fish/native measurements and exact validation paths are reported in the integration handoff. Retain original and effective settings plus model/runtime provenance for every output.

## Integrating an isolated worktree

Merge source changes before deployment; preserve the originating Vox combined/performance-reference and reviewed pause-derivative changes when resolving shared files. Do not copy an entire worktree over the authoritative checkout or blindly sync into another installed service.

Weights are already on SSD: reuse the exact validation `weights/{chatterbox,indextts,moss,fish,higgs}` directories through WEIGHTS_DIR, or persist ignored storage.json backend_weights/backend_extensions mappings. Do not create symlinks on filesystems such as exFAT that do not support them; existing project-to-SSD links originate on internal APFS. Keep validation runs and logs in their original directories. Do not point a new JobManager at their data roots while a validation server is running.

Virtual environments are not relocatable: pyvenv.cfg and executable shebangs reference their Python base and project paths. Keep the project in its final location before setup. Downloaded Python bases live in project/.python, the uv tool/cache in .bootstrap, and native source/build in .runtime-source/audio.cpp. If a project must move, preserve its data and rebuild environments and native binaries at the final path with make setup-env. Existing weights can be reused without downloading or copying them. Verify readiness and a small inference before removing the old installation.


Health includes runtime_errors separately from asset_errors/downloaded: missing or mismatched pinned runtime packages, or a missing/wrong native Metal binary, cannot advertise readiness even when model files are complete. These checks do not load model weights. Validation errors are JSON-safe, including NaN/Infinity inputs.

VoxCPM2 combined cloning requires an exact transcript and uses the same recording for identity and performance, without new direction fields. The shared reviewed-pause editor preserves raw samples and adds only enough silence in explicit reviewed quiet spans to reach a minimum total; no automatic sentence alignment is implied. See API.md.

## Interactive startup and model-scoped controls

Interactive `make setup` and `make serve` show a small model menu ordered as Higgs TTS 3, VoxCPM2, Qwen, Chatterbox, IndexTTS, MOSS, and Fish. Setup therefore numbers Higgs first and Vox second; serving retains that order while numbering only ready models. Setup lists every model and appends **[SETUP COMPLETE]** when its current required assets and compatible runtime pass the same lightweight checks used for the serving list; missing volumes, files, or broken environments do not receive the label. Completed models remain selectable for setup retries. The label concerns that speech model, independently of the shared optional LLM and queue dependencies. No weights are loaded or downloaded to build the menu. Enter chooses the configured **Higgs TTS 3** default when available (otherwise serving marks the first ready model as the menu default). An explicit `MODEL=...` skips it; `NONINTERACTIVE=1` or nonterminal input uses the configured default without prompting and skips the setup-status scan. Storage selection remains separate and remembered. Explicit VENV/PORT/DATA_DIR overrides survive model selection; automatic environment/port defaults are recomputed for the chosen model. Managed `make start` retains its existing noninteractive lifecycle behavior. Setup downloads only the chosen model if required; `make setup-env` is runtime-only.

New controls are namespaced: `chatterbox.exaggeration/cfg_weight` and `indextts.emotion_reference_id/emotion_vector/emotion_alpha/duration_factor/interval_silence`. The API accepts deprecated flat aliases for the earlier local validation runs, but rejects duplicate forms, inactive namespaces and unknown members. Requested/effective metadata uses the model IDs. See API.md. Restricted-model startup/UI/API notices summarize the bundled exact license texts without blocking authorized personal use; Higgs explicitly retains its conditional Creator Use Grant.

## Performance workflow

Use each backend's own controls: the shared UI now previews Fish bracket-prefix tags and Higgs official emotion/prosody/style prefixes, and provides optional Chatterbox delivery candidates plus Index's Follow recording/emotion-audio/named-vector selection. All new fields live under their exact model ID. Model-specific defaults and explicit overrides are documented in VOICE_QUALITY.md; no universal quality slider is provided. Fish/Higgs samplers use real consumed top_p/top_k controls; ineffective repetition penalties stay hidden. Qwen mode guidance distinguishes Base ICL from newly designed/preset speakers, and MOSS explicitly remains design-only. See API.md for conflict rules, exact enums, sampling metadata, reference durations and audition limits. Whole submitted text is preserved, with raw generated output retained; precise pauses still require reviewed quiet-span edits.

Recommended quality defaults and research evidence: [VOICE_QUALITY.md](VOICE_QUALITY.md). Vox auto resolves once at admission; other backends retain their own conditioning and untagged/follow-recording defaults.

## Longer punctuation pauses

New reference/preset requests for Fish, IndexTTS, Chatterbox and Qwen default to 500 ms extra silence after sentences and 200 ms after commas, implemented as separate phrase calls with the same conditioning. Set `<model-id>.punctuation_pauses:false` for the former whole-line behavior, or configure `sentence_pause_ms` / `comma_pause_ms` in that namespace or the UI's Advanced settings. This is additional silence, not a measured minimum total gap; full token/input validation still applies. Short phrase calls can alter flow and take longer. Manual Fish tags and Qwen VoiceDesign retain whole-line synthesis by default. Index's existing 400 ms internal segment interval is a separate native control.

MOSS uses the same fields as approximate native instruction targets in one pass, preserving designed speaker continuity without claiming millisecond enforcement. Vox/Higgs are unchanged. See API.md for bounds, exclusions, frozen queue behavior and sample-level provenance.
