---
name: voxcpm2
description: Operate the local VoxCPM2 Voice Studio to design voices, direct supported emotion, synthesize speech, and manage saved named/tagged reference voices. Use for this existing server rather than rebuilding it.
---

# VoxCPM2 Voice Studio

Locate the checkout from the user's location or service.json with the expected name; do not assume an alias or this Mac's paths. Read README.md and API.md. Read GET /health and /api/capabilities on the configured port; fetch /openapi.json for the authoritative schema. Preserve model mode, recordings, catalogs and active work.

First terminal setup asks for a storage folder; Enter selects the project, and an external absolute folder creates links and is remembered. AI/scripts should use make setup NONINTERACTIVE=1 or an explicit DATA_DIR to avoid a prompt. Use make setup only if needed; it installs pinned dependencies and verifies pinned model assets. Honor VENV, BOOTSTRAP_PYTHON, DATA_DIR, OUTPUT_DIR and PORT overrides consistently. Confirm external volumes are mounted before creating storage. On this Mac the serving project and venv are under ~/Documents/models/VoxCPM2; ignored storage.json keeps DATA_DIR and the shared GPU lock on the SSD. If present, read that file before setup and preserve all linked data directories. A fresh checkout needs no local settings file. storage.json.example is portable; never copy another machine’s local settings blindly. Serving is offline and must not download missing weights implicitly.

Use make serve for a retained foreground process, or make start for explicitly requested background operation. Reuse an existing matching instance. make stop verifies a managed idle process; do not kill an unrelated listener or interrupt a job. No automatic login service. Do not stop other model services to free memory without authorization.

VoxCPM2 supports steps/guidance and emotion with designed or reference voices. Fetch capabilities before choosing controls. A common schema does not mean every backend accepts every setting. Unknown/unsupported settings fail. No speed control or streaming API is exposed. Reference identity/accent/emotion remain approximate; a seed is not a voice lock.

POST /api/generate with text plus description or a saved voice_id/reference_id. Keep passages short and comply with live token/media limits. Optional absolute output_dir refers to this server's filesystem. Include a stable idempotency_key for recoverable retries, poll GET /api/jobs/id, and keep capacity busy until the worker completes even if the client disconnects. No cancellation or progress percentage is promised. On timeout recover the job; do not automatically create duplicates. On restart unfinished jobs become errors; inspect History before retrying.

Return the verified WAV and JSON paths and local playback URL after success. Check actual output rate/duration, cold loading versus synthesis time, and truncation warnings. Do not claim perceptual quality was verified unless audio was actually auditioned. Local links are not phone-accessible; upload/export only when explicitly authorized for that file.

Save reusable recordings using POST /api/voices, with name, catalog description and normalized tags. Search/filter/page through GET /api/voices. PATCH updates metadata without changing audio/original design. Save exact excerpt transcripts where appropriate; long samples save only their first 30 seconds. Uploads retain original bytes plus bounded mono working copies. Never delete user recordings as cleanup.

Run make test for no-GPU validation and make smoke for one small real inference when requested/appropriate. Shared source/UI/API can be checked with make sync-check PEER=...; synchronization only copies allowlisted source and never profiles, weights or user data. Keep private audio, transcripts, metadata, indexes, jobs, caches and environments outside Git. Do not upload or publish anything without user authorization.

## Expressiveness additions

Read EXTENSIONS.md and the live API 2.1 capabilities. Qwen may offer preset_voice and the experimental steering_emotion/steering_strength fields only when its CustomVoice checkpoint/pack is installed. Do not apply these vectors to Base or VoiceDesign. Accent is a literal prompt direction in supported modes, not a trained accent adapter. VoxCPM2 continuation requires a reference plus its exact transcript and uses prompt_audio/prompt_text; default reference mode remains available. Do not infer that a language LoRA or unrelated runtime extension can be loaded by MLX. Inspect applied settings and decode small real outputs after changes; do not claim perceptual improvement without listening.

## Combined cloning and performance references

VoxCPM2 accepts `clone_mode: "combined"` with a saved `voice_id` or uploaded `reference_id` and an exact saved/override `ref_text`. This sends the same recording as both `ref_audio` (identity) and `prompt_audio` plus `prompt_text` (recorded performance). Continuation and combined modes reject new emotion/accent directions. A reference description is catalog context, not an applied instruction. Default reference behavior is unchanged. Check `modes.reference.clone_modes`; Qwen and other unsupported backends reject combined mode. The full text is synthesized in one call, and its transcript counts toward the input budget.

For expressive variants, upload clean recordings of the **same desired speaker**, save each with a distinct performance name, mood tags and its exact spoken transcript, then select the variant for combined cloning. Preview the reference first: tags are organizational notes, not proof of accent quality. The selected recording supplies both identity and performance; a separate identity anchor is not exposed. Private recordings, transcripts and presets remain outside Git. Accent retention and delivery require listening; no accent guarantee is made.

Longer sentence pauses are not automatically aligned or inferred. Preserve raw full-line takes; do not synthesize tiny sentence fragments or treat energy minima as verified sentence boundaries. Reviewed boundary edits can add silence to reach a desired total gap while preserving voiced samples and longer existing gaps. No speed, temperature or accent-strength control is added for VoxCPM2.
