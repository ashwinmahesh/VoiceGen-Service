---
name: local-voice-studio
description: Use VoiceGen Service to design or clone character voices, generate expressive dialogue, compare 1–5 takes, and manage the shared durable queue across ready voice models.
---

# Local Voice Studio

## Locate and inspect

Locate the checkout from the user’s configured projects; this installation is `~/Documents/models/VoiceGen-Service`. Read its README, QUICKSTART.md and API.md, then inspect the live `/health`, `/v1/models`, `/api/capabilities?model=<id>` and `/openapi.json`. Do not assume a backend supports another model’s controls. The browser app is named **Voice Studios** across all pages, regardless of the selected speech model. Studio, Collection and Observability share the generator header with Queue, service status and memory release. The normal UI/generator origin is localhost:5743; the independent queue API is localhost:55205. Both bind locally.

Generator health has stable identity `service:voicegen-service`, `mode:generator`, `api_version:4.0.0`, `queue_scope:shared`, `runtime_model_switching:true`, plus project and database paths. `default_model` is the saved admission default; `active_model` is the backend loading/holding speech weights, or null. A model change never requires a new generator process. Do not identify a service or kill a process by its model.

Queue health identifies `service:voicegen-queue`, `role:queue-crud`, `api_version:4.0.0`, `queue_scope:shared`, plus `project` and `queue_database`. Both APIs use `api_version`, not `version`; generator identity is `voicegen-service`, not an inferred family name. Match each actual contract when integrating external launchers.

`GET /v1/models` on either API returns ready-only `data` descriptors (`id`, `name`, `modes`, `is_default`), `default_model`, `active_model` and `runtime_model_switching`. The queue has no backend process and reports active null. Registry order is Higgs, VoxCPM2, Qwen, Chatterbox, IndexTTS, MOSS, Fish. Missing models are set up with `make setup MODEL=<id>`; never silently download during serving.

## Start and stop

`make setup` prompts for a model and remembered storage location. Blank storage uses the project for a fresh installation; preserve this user’s saved SSD configuration. `[SETUP COMPLETE]` reflects read-only file/runtime readiness; safe reruns reuse verified downloads. `q` or Ctrl+C cancels setup gracefully, retaining partial and existing data. Explicit `MODEL=<id>`/`NONINTERACTIVE=1` skip menus. Setup manages a compatible internal Python environment rather than requiring the user’s default Python to match every model.

`make serve` requires at least one model with verified assets and a compatible runtime. Without one, it exits nonzero with `make setup` instructions before asking questions or starting either service. An environment or partial download alone does not count. If setup previously completed, reconnect its model storage rather than starting an empty service. `make serve` chooses/saves the default from ready models and serves all ready backends. Enter uses the saved default, initially Higgs, or the first ready model. `MODEL=<id>` explicitly saves that default. `make start` starts/reuses a verified managed instance and can change its default without restarting it. Foreground Ctrl+C finishes active work; `make stop` stops a verified idle managed generator. Queue lifecycle is separate: `make queue`, `make queue-status`, `make stop-queue`. Keep pending work and private media.

The local Qwen3.5-2B 4-bit prompt LLM is enabled by default. Interactive serving asks [Y/n] with an explanatory sentence; Enter enables it. `NO_LLM=1` / `--no-llm` disables it for that run. Missing required LLM assets fail clearly; do not silently switch it off. Inspect startup-mode compatibility before reusing an instance. Setup includes the shared LLM; `make setup-llm` adds it to an existing installation.

Restricted models print license notices at startup and on actual backend switches. Read the selected model’s `commercial_use_notice`, full notice and license URL. Preserve the documented Fish, Higgs Creator Use Grant and IndexTTS threshold conditions; do not impose a universal warning on permissive models or uploaded recordings. Notices do not require approval.

After maintenance/tests, finish active test work and stop every agent-started generator, queue and preview. Preserve pre-existing user processes and never restore a different model automatically. Do not install login daemons or auto-start services.

## Choose a voice source

Higgs is the initial default and requires a saved/uploaded recording plus its exact transcript. VoxCPM2, Qwen VoiceDesign and MOSS support description-based design. Capabilities expose `modes.design.supported_models` (static alternatives), `ready_models` and `unavailable_reason`.

The Design tab is enabled whenever any capable model is ready. Entering a source unsupported by the current model clears the model selection and offers only capable ready models, preserving the draft. Select a model before generation or LLM preview. If none can design, the tab remains disabled with the supported-model tooltip on hover, keyboard focus and tap. API requests still need an explicitly capable model or a capable saved default; Higgs design returns 422 before creating work. Queue acceptance of opaque speech does not establish compatibility; the generator validates it at consumption.

Upload reference audio with `POST /api/references` (multipart file and optional transcript) or save directly to Collection with `POST /api/voices/upload` (file, name, description, transcript, repeated tags). Limits: 25 MiB, 1–30 seconds, 8–96 kHz, mono/stereo; preserve originals. Saved voice metadata supports names, descriptions, tags and exact transcripts. Search/list with `/api/voices`, get/edit `/api/voices/{id}`, tags `/api/voice-tags`. Save a generated take with `/api/voices` and its output_id. Read the live schema for precise fields.

The studio preserves transcript edits through refreshes and offers Save transcript to voice; Generate saves a saved voice's edited transcript before admission. API ref_text is only an override: PATCH the voice transcript to persist it. Saving a complete generated take up to 30 actual seconds defaults its transcript to the spoken line without Fish/Higgs tags. Longer or potentially cut-off takes need an exact excerpt transcript rather than the full line. The listening room shows elapsed admission-to-completion time, including waiting, across batches and reloads.

Vox Auto leaves accent/emotion directions editable and resolves to flexible conditioning when they are used; Combined/Continue performance disable them with an explanation. Higgs shows native Emotion/Prosody/Style instead of unsupported free-text direction fields. Its reference supplies the accent cue, with no accent-fidelity guarantee. Disabled fields have a lighter grey fill; never bypass capability checks merely to enable a control.

## Generate and compare

`POST /api/generate` accepts one flat Generation request. Include `model` to choose a ready backend; omitted/null uses the shared saved default at admission. That choice is immutable for the job. Shared controls are top-level; model-specific settings belong under the exact model ID (`higgs`, `voxcpm2`, `qwen3`, etc.). Use the request model’s capabilities/defaults, asset requirements and prompt profile, never the last active backend’s.

Example description-based request:

```json
{"model":"voxcpm2","text":"Welcome home.","description":"A smooth, cultured baritone.","idempotency_key":"scene-1-voice"}
```

Example recorded voice:

```json
{"model":"higgs","voice_id":"VOICE_ID","text":"Welcome home.","idempotency_key":"scene-1-take"}
```

HTTP 202 means persisted, not completed. Poll `GET /api/jobs/{id}` for done/error and use `result.audio_url`, verified absolute `path`, and metadata. Keep a stable key and original body after timeouts; changed requests with a key return409. Do not resubmit an error automatically; explicitly retry `/api/jobs/{id}/retry` if intended. If its model is unavailable, retry returns422 with setup guidance and leaves the failed job unchanged.

`POST /api/candidates` adds `candidate_count` 1–5 (default2), `candidate_variation` auto/distinct/seed_only, and `candidate_use_llm` (defaulttrue). Poll `/api/candidates/{id}` for ordered child jobs and full/partial/error state. Description-based Vox/Qwen/MOSS batches can explore contrasting resonance/articulation within explicit character constraints. Original is option1. The served/enabled LLM can refine variants together; otherwise built-in directions apply. Recordings/presets, including Higgs, produce seed-only alternate takes of the same speaker; explicit distinct is rejected. Prompts freeze before speech, retries reuse them, and successful children remain usable after another child fails. Save the preferred take to Collection.

Keep spoken words and transcripts exact. Delivery controls and comma/sentence pacing vary by model; inspect API.md, VOICE_QUALITY.md, EXTENSIONS.md and model-specific capabilities. More expression can change accent/identity; never promise fidelity based on a seed or settings alone. Preview prompt expansion via `/api/prompts/expand`, review its original/expanded guidance, and apply its receipt. The LLM sees voice/delivery guidance rather than dialogue, audio or reference transcripts. UI expansion initially enables itself when served and supports opt-out. Per-model profiles are versioned in `backends/<id>/prompt_expansion.json` and published in capabilities. Do not turn an unsupported native mode into a supported one by editing prose rules.

## Shared queue

Both services access the same ignored project-local `state/queue.sqlite3`. Their OpenAPI uses shared model-discovery, move and pause response schemas; pause/resume returns `{ "paused": true }` or false. One generator holds the consumer lease and processes all ready models in global order. Whole candidate batches finish before switching. It reuses the current adapter where possible and releases/reaps it before another starts. Backend children retain the global worker lease while warm; actual native/LLM descendants inherit per-job GPU/data/consumer descriptors. Unverifiable cleanup stops consumption, never starts overlapping models.

Generator queue operations: GET `/api/queue` (status/model/limit/offset), DELETE one `/api/queue/{id}` or clear `/api/queue`, POST `/{id}/move` (one of before_id/after_id/direction), POST `/{id}/run-now`, and POST `/api/queue/pause` or `/resume`. Job status/retry use `/api/jobs/{id}`. Discovery/OpenAPI are canonical; no old aliases or migration routing are required.

The standalone queue uses the same scheduling/list/delete routes, plus GET/PATCH `/api/queue/{id}` and this admission envelope:

```json
{"model":"voxcpm2","payload":{"text":"Welcome home.","description":"A cultured baritone."},"generate_immediately":false,"idempotency_key":"scene-1-queued"}
```

Omitted/null envelope model uses the same saved default as the generator. Keep key and priority outside payload; any optional payload.model must match the immutable envelope model. Admission checks model readiness only, deferring speech/media validation until consumption. An unavailable model rejects new work422 with setup guidance. Admitted unavailable entries stay queued/blocked, keep their model/position, and resume when it returns; other ready work can proceed. Existing CRUD works with model storage offline.

One global order spans models; filtered lists retain global `position`. Immediate jobs precede normal work FIFO unless moved. Run Now moves first without interrupting the active batch. Pause holds normal waiting work; immediate work can still run. Removal/clear affects waiting entries only and retains tombstones/keys and all completed media. Active jobs cannot be moved/removed. Moves serialize with claims; refresh after an uncertain relative move. Keyed/prepared payloads are immutable; the queue may edit priority or an unkeyed unprepared opaque payload.

The studio talks directly to generator APIs/SQLite. Queue HTTP outage leaves generator jobs/CRUD working and shows a recoverable notice. Keep queue_service diagnostics separate from queue_storage. SQLite failures return503, code queue_storage_unavailable, Retry-After:2; preserve IDs/keys and retry after storage recovers. Never create a fallback database or replay work from health checks. A complete matching WAV/sidecar reconciles after restart; crashes before completion may repeat synthesis, so do not promise exactly-once effects.

## Resources and storage

Generator Observability and `/api/observability`, `/api/observability/jobs`, `/api/observability/jobs/{id}` provide 5m/30m/1h/6h/24h/3d/7d histories and per-attempt charts. CPU/RSS include the generator tree; GPU counters are device-wide shared memory, not exclusive job use or discrete VRAM. Samples retain model/session/phase and gaps, keep seven days in the existing project DB, and never upload telemetry. Read docs/observability.md for metric scopes and query bounds.

Keep source/Git, Python environments, SQLite/WAL/backups, PID files and leases under the project. Keep actual weights, caches, references, voices and outputs on configured storage; preserve storage.json mappings and SSD symlinks. Missing volumes must fail clearly without internal fallback downloads. Private state, audio, indexes, credentials and weights stay out of Git. Back up database state together with referenced media. Use isolated state and benign fixtures for tests.

For generation diagnostics, use generator `GET /api/jobs/<id>/logs?attempt=1` (omit attempt for latest), or Observability → By job → Charts & logs. Native Higgs/MOSS output is bounded to the latest 64 KiB; `available:false`/`notice` is normal for other models, missing storage or unretained older attempts. Never substitute shared backend logs or treat sampled stage history as a full trace. No model execution or queue replay is needed to inspect logs.
