# Local Voice Studio API 2.1.0

Both projects implement the same OpenAPI schema at `GET /openapi.json` and interactive documentation at `/docs`. The API is a local job protocol, not an OpenAI-compatible speech endpoint. Read `GET /api/capabilities` before building a request; backend-specific controls are optional in the common schema but rejected when unsupported. Unknown request fields return 422.

## Generation

`POST /api/generate` returns 202 and a durable job. Minimal design request:

```json
{"text":"Welcome home.","description":"A warm, cultured baritone.","emotion":"Quietly amused.","seed":42,"max_tokens":500,"idempotency_key":"scene-1-take-1"}
```

`GET /api/jobs/{id}` returns `id`, `status` (`queued`, `running`, `done`, `error`), `stage`, timestamps, `error`, and `result`. Stage messages describe actual work; no estimated percentage or cancellation is provided. A disconnected browser or HTTP timeout does not cancel synthesis or release capacity. Recover by job ID, or resend the identical request with the same idempotency key. Reusing a key with different settings returns 409. Keys/jobs remain until explicitly removed from local storage. After a server restart unfinished jobs become errors; completed outputs remain available. Never retry an interrupted operation automatically with a new key before checking History.

A single worker accepts one job; another request returns 409 immediately. Both voice projects also share a nonblocking file lock for inference/unloading. That lock does not coordinate unrelated image/model servers. Qwen unloads its previous variant before loading another. Models load lazily from verified local assets and remain resident until `POST /api/model/unload` or shutdown. Run one server worker, with no development reloader.

| Field | Shared validation / meaning |
| --- | --- |
| `text` | Required, nonblank, up to 3000 characters; no silent truncation |
| `description` | Up to 1500 characters; required for design; reference identity comes from the recording |
| `emotion` | Up to 1000 characters; design on both models, reference mode on VoxCPM2 only |
| `voice_id`, `reference_id` | Optional UUID hex IDs; choose one reference source or preset_voice |
| `preset_voice` | Qwen CustomVoice preset ID from capabilities; mutually exclusive with voice_id/reference_id; description is optional additional direction |
| `accent` | Optional accent instruction, <=500 characters; design/preset/Vox standard reference mode only; this is prompt guidance |
| `clone_mode` | `reference` default; `continuation` and `combined` on VoxCPM2 references require an exact saved/override transcript |
| `steering_emotion` | Optional installed experimental direction; Qwen CustomVoice presets only; see capabilities |
| `steering_strength` | Optional 0–12, default 8 when a direction is selected; cannot be supplied without steering_emotion |
| `ref_text` | Optional exact reference transcript, up to 3000 characters; Qwen reference mode or VoxCPM2 continuation/combined; overrides saved transcript |
| `language` | `auto` default; Qwen also English, Chinese, Japanese, Korean, German, French, Russian, Portuguese, Spanish, Italian. VoxCPM2 auto-detects from text |
| `seed` | Default 42, unsigned 32-bit integer; reproducibility input, not a voice identity guarantee |
| `steps` | VoxCPM2 only, default 10, 1–30, maps to inference_timesteps |
| `guidance` | VoxCPM2 only, default 2, 1–5, maps to cfg_value |
| `temperature` | Qwen only, default 0.9, 0–2 |
| `max_tokens` | Default 1000, 50–2000; output-token cap, not requested duration |
| `output_dir` | Optional absolute directory on server's Mac; defaults to OUTPUT_DIR. Volume and writability checked before inference |
| `idempotency_key` | Optional 1–128 characters; stable for retries of the same request |

Qwen VoiceDesign handles description+emotion. CustomVoice handles built-in preset speakers with emotion/style/accent instructions and the optional experimental steering pack. Base handles saved/uploaded references. Base accepts no direct emotion instruction: 422 if nonempty. An exact transcript enables in-context reference cloning; without a transcript it uses speaker embedding conditioning. Voice descriptions and accent/emotion are probabilistic; identity is approximate. There is no effective speed control, so the API rejects `speed`.

Conservative combined tokenizer limits: VoxCPM2 4096, Qwen 2048 input tokens for text/instructions/reference transcript used by the adapter. Tokenizer limits are checked after local load, before synthesis. Outputs have a separate maximum of 2000 audio tokens and references are bounded to 30 seconds. The adapter sends one complete text passage (Qwen Base segmentation disabled). Backend token limits may cut audio short; metadata records a warning based on Qwen output token count or VoxCPM2 duration heuristic, not proof that the final words were spoken. Start with short passages.

Results include an absolute WAV `path` and `metadata_path`, local `audio_url`, actual sample rate/channels/format/duration, separate requested/effective settings, model revision, runtime and quantization, input token count, waveform peaks, cold-load time, synthesis/total time, peak MLX allocation, and `real_time_factor` (synthesis seconds / audio seconds). These paths are on the Mac, not a phone. Audio+sidecar use unique IDs and atomic file replacement; an ignored artifact index resolves custom destinations after restart. Metadata retains reference path/transcript and original/effective upload properties through reference/voice records. Transcripts and audio remain local unless explicitly exported.

## Artifacts, references and voice catalog

| Route | Behavior |
| --- | --- |
| `GET /health` | Service identity, project/data root, verified assets, loaded variant, real unloaded/loading/ready/generating/unloading/error state, busy/current job, last error |
| `GET /api/capabilities` | Backend controls, models/revisions/files, modes, languages, limits and storage defaults |
| `GET /api/history?q=&limit=24&offset=0` | Search transcript/description/emotion, newest first; matching total and navigation; limit 1–100 |
| `GET /api/artifacts/{id}` | Result with verified current paths; missing media omitted from catalog |
| `GET /api/metadata/{id}` | Download JSON generation sidecar |
| `GET /api/audio/{outputs\|voices\|references}/{id}?download=true` | Scoped opaque-ID WAV playback/download; no arbitrary file access |
| `POST /api/references` | Multipart `file`, optional `transcript`; preserves original plus normalized mono 16 kHz PCM16 working copy |
| `GET /api/references/{id}/original` | Original upload bytes with original filename |
| `POST /api/voices` | Save a sample using `name`, optional `description`, `tags`, `transcript`, and exactly one `output_id` or `reference_id` |
| `GET /api/voices?q=&tag=&sort=newest&limit=24&offset=0` | All query words match across name, description, original design and tags; accent/case insensitive; exact tag filter; sort newest/updated/name |
| `GET /api/voices/{id}` | Saved voice details and preview URL |
| `PATCH /api/voices/{id}` | Change name/description/tags/transcript; audio and original design remain unchanged |
| `GET /api/voice-tags` | Existing normalized tags/counts |
| `POST /api/model/unload` | Release this server's model when idle |

Upload limits: file <=25 MiB, complete multipart <=26 MiB, decoded 1–30 seconds, 8–96 kHz, mono/stereo, <=5.76M scalar samples. Headers are checked before decoding. WAV/FLAC/MP3 supported by installed libsndfile; unreadable formats return 422. JSON bodies <=128 KiB. Originals remain in `references/originals`; legacy references created before v2 have no preserved original. Nothing is pruned automatically.

Voice names: 1–100 trimmed characters. Catalog description: <=2000. Tags: <=20, each 1–40 characters, whitespace collapsed, lowercased and deduplicated. PATCH replaces supplied fields; empty description/tags/transcript clears them, null is rejected. Saved samples are mono, up to the first 30 seconds. A transcript is inherited only when the entire generated recording fits within 30 seconds. For longer takes supply the exact excerpt transcript or leave it empty. Catalog notes describe the character; editing them does not alter the recording that conditions reuse. Library has search, exact-tag filter, sort, pagination/page size and explicit reuse.

## Errors and local access

422: validation/unsupported control/input/storage error; 409: occupied inference slot or conflicting idempotency key; 413: body too large; 503: incomplete assets (run `make download`); 404: unavailable ID/media. Worker failures appear in job status and health. Unload clears a previous inference error and allows another attempt. Status remains responsive because inference runs off the HTTP event loop.

Loopback only, local Host allowlist, same-origin writes, no permissive CORS, no remote URL fetching, no external UI resources. This API accepts trusted local output paths and is not authenticated for LAN/internet exposure. Use an explicitly designed authentication/access layer before changing that scope.

## Expressiveness additions in 2.1

Read [EXTENSIONS.md](EXTENSIONS.md) for provenance, compatibility and limitations. API/UI sources and OpenAPI remain identical across projects; capability metadata identifies which additions are installed. The Qwen capability response includes nine `presets` and an `extensions.emotion_steering` record (13 directions, strength bounds, source revision and experimental label). VoxCPM2 reports an empty extension pack/preset list and supports native reference continuation. Both accept a prompt-based accent direction where instructions work. No unsupported field is silently forwarded to Base.

```json
{"text":"You came. Sit, please.","preset_voice":"ryan","language":"English","emotion":"","steering_emotion":"contempt","steering_strength":8,"temperature":1.1,"max_tokens":200}
```

A preset requires no description. When temperature is omitted with steering, effective temperature is 1.1. Choose `clone_mode: "continuation"` plus a VoxCPM2 voice/reference ID and exact `ref_text` to continue the recorded performance; an empty override with no usable saved transcript returns 422 before inference. New metadata includes `applied_instructions`, extension revision/settings and requested/effective preset, accent and continuation controls. Old 2.0 records remain readable.

VoxCPM2 continuation and combined cloning follow the recorded delivery and accept no new emotion/accent directions in this service. Use its standard reference mode to apply prompt directions.

## Combined cloning and performance references

VoxCPM2 accepts `clone_mode: "combined"` with a saved `voice_id` or uploaded `reference_id` and an exact saved/override `ref_text`. This sends the same recording as both `ref_audio` (identity) and `prompt_audio` plus `prompt_text` (recorded performance). Continuation and combined modes reject new emotion/accent directions. A reference description is catalog context, not an applied instruction. Default reference behavior is unchanged. Check `modes.reference.clone_modes`; Qwen and other unsupported backends reject combined mode. The full text is synthesized in one call, and its transcript counts toward the input budget.

For expressive variants, upload clean recordings of the **same desired speaker**, save each with a distinct performance name, mood tags and its exact spoken transcript, then select the variant for combined cloning. Preview the reference first: tags are organizational notes, not proof of accent quality. The selected recording supplies both identity and performance; a separate identity anchor is not exposed. Private recordings, transcripts and presets remain outside Git. Accent retention and delivery require listening; no accent guarantee is made.

Longer sentence pauses are not automatically aligned or inferred. Preserve raw full-line takes; do not synthesize tiny sentence fragments or treat energy minima as verified sentence boundaries. Reviewed boundary edits can add silence to reach a desired total gap while preserving voiced samples and longer existing gaps. No speed, temperature or accent-strength control is added for VoxCPM2.
