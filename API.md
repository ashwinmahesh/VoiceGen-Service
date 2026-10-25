# Local Voice Studio API 2.1.0

The startup-selected studio implements one OpenAPI schema at `GET /openapi.json` and interactive documentation at `/docs`. The API is a local job protocol, not an OpenAI-compatible speech endpoint. Read `GET /api/capabilities` before building a request; backend-specific controls are optional in the common schema but rejected when unsupported. Unknown request fields return 422.

## Generation

`POST /api/generate` returns 202 and a durable job. Minimal design request:

```json
{"text":"Welcome home.","description":"A warm, cultured baritone.","emotion":"Quietly amused.","seed":42,"max_tokens":500,"idempotency_key":"scene-1-take-1"}
```

`GET /api/jobs/{id}` retains the established job response and IDs. New jobs add stable `sequence`, `backend`, `model`, `mode`, `generate_immediately`, `attempts`, start/completion times, frozen requested/effective settings, and `paused`. Status is `queued`, `running`, `done`, `error`, or `deleted`. Stage messages describe actual work; no percentage or active cancellation is promised. A disconnected browser or HTTP timeout does not cancel work or release accelerator capacity.

Submissions are transactionally admitted to SQLite under `DATA_DIR/jobs/queue.sqlite3` before a 202 response. A single worker starts after storage ownership/recovery, consumes startup backlog automatically, and runs only the startup backend. Other backends remain visibly paused; the server never loads a different backend to drain them. Within the applicable backend, immediate pending entries run FIFO ahead of ordinary FIFO entries. **Immediate means the next available slot after current inference, with no preemption.** A continuous stream of immediate work can delay ordinary entries. API submissions default `generate_immediately=false`; the webpage sends `true` by default and offers a checkbox to enqueue normally. Busy inference/accelerator contention queues requests rather than returning the former busy 409. Existing `/api/generate` clients still receive 202 Job and poll the same detail route; no synchronous speech route was changed.

The worker holds the configured shared accelerator `GPU_LOCK_FILE` through completion (including after disconnect). It waits when another owner holds the lock. Point image/voice workers at the same lock to serialize accelerator use; an idle resident model still consumes memory. Run one server worker without reloaders. An additional per-data-directory queue owner lock prevents duplicate recovery/workers. Native subprocesses and their time wrapper inherit the actual queue-owner, process data-owner and accelerator descriptors; if the Python server is killed, those reservations remain held until the native child exits. Unloading a running model returns 409; unloading while entries wait releases the model, which loads again for the next admitted take.

### Queue operations

`/api/jobs` and `/api/queue` are aliases for the new collection operations; existing `/api/jobs/{id}` remains valid.

| Method / route | Behavior |
| --- | --- |
| `POST /api/queue` | Same typed Generation body and 202 Job as `/api/generate` (also `POST /api/jobs`) |
| `GET /api/queue?status=pending&backend=voxcpm2&limit=24&offset=0` | List entries; backend optional; limit 1–100, offset >=0. Status: pending (queued+running), queued, running, done, error, deleted, all. Counts reflect all statuses for the backend filter; total reflects the status filter. Includes has_more/next_offset. Running entries precede immediate and normal pending FIFO entries |
| `GET /api/queue/{id}` | Typed detail (alias of `/api/jobs/{id}`); other-backend pending entries have paused=true |
| `DELETE /api/queue/{id}` | Remove only a waiting entry; 409 for running or completed/failed work, 404 unknown. Returns deleted count and active_job. Repeated delete of a tombstone returns zero |
| `DELETE /api/queue?backend=voxcpm2` | Transactionally clear waiting entries, optionally for one backend. Active entries keep running; completed artifacts, History, references and original recordings are retained |
| `POST /api/queue/{id}/retry` | Explicitly retry an error under its original ID/order, incrementing attempts when claimed; 409 unless failed and its backend is selected. Also available under `/api/jobs/{id}/retry` |

Delete/clear and worker claim are serialized in SQLite: an entry is either removed before claim or remains running. Removal retains a durable tombstone and retry-key mapping. Retrying an identical keyed submission returns its original job, including failed/deleted jobs, without silently regenerating. Changed settings (including priority) with the same key return 409. Use the explicit retry route for an intended failed-job retry. Unkeyed requests or new keys are new jobs. No automatic pruning is performed; queue/history retention are separate. Legacy JSON jobs and hashed key records remain readable. Legacy interrupted JSON entries cannot safely replay because they lack durable inputs and remain errors requiring History review.

### Restart and reference guarantees

Admission freezes validated requested/effective controls, backend/model/mode, destination, reference path/identity/metadata, exact transcript and reference audio SHA256. Later catalog renames/transcript edits cannot change an admitted take. If reference bytes change or disappear, execution fails visibly; restore the admitted bytes before retrying. The same guard covers IndexTTS speaker and separate emotion recordings (including legacy flat aliases). No audio snapshots are duplicated or removed by queue clearing.

Interrupted SQLite running entries return to their original ordering on restart. Recovery first reconciles a matching complete WAV plus JSON sidecar and repairs the artifact index instead of synthesizing again. The same check runs before executing a queued entry. Invalid/mismatched completion markers fail for review. Backend model-mapping changes also fail before inference. Worker failures stay errors until explicit retry.

This is durable admission and practical retry/restart recovery, **not exactly-once synthesis across arbitrary crashes**. A crash before a complete sidecar exists can repeat synthesis under the same job ID; a complete matching sidecar plus valid WAV prevents the normal bookkeeping-crash duplicate. Hardware/filesystem failures, external edits and loss of storage are outside that guarantee. SQLite uses rollback journaling and FULL synchronous writes; keep the database, journal and owner lock on the configured local data volume and back up jobs together with outputs/indexes. Do not run a second server or manually alter its queue while an owner is active.

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
| `generate_immediately` | Boolean, API default false; true joins priority FIFO for the next slot, never interrupts active inference |
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

422: validation/unsupported control/input/storage error; 409: conflicting idempotency key, unsupported active deletion/retry, or occupied unload slot; 413: body too large; 503: incomplete assets (run `make download`); 404: unavailable ID/media. Worker failures appear in job status and health. Unload clears a previous inference error and allows another attempt. Status remains responsive because inference runs off the HTTP event loop.

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

## Reviewed longer-pause versions

`POST /api/artifacts/{id}/pauses` creates a separate artifact from an existing local output. It performs CPU-only silence insertion; it does not run the speech model. The source WAV and all its original sample values/order are preserved. The same route works across backends for supported stored audio. No edits are automatic: listen and select **the entire quiet gap** between phrases yourself. Quietness validation does not identify sentence boundaries or prove a span has no quiet speech.

```json
{"reviewed":true,"spans":[{"start_seconds":1.20,"end_seconds":1.55}],"minimum_gap_ms":750,"idempotency_key":"reviewed-gap-take-1"}
```

The example offsets are illustrative; measure them in your recording. `reviewed: true` is required. Supply 1–30 finite, increasing, ordered, nonoverlapping spans strictly inside the source. Each selected span must be at least 20ms, and every PCM16 sample in it must have absolute value <=32 (about -60dBFS). This fixed conservative check rejects audible selections; no user-adjustable quietness bypass exists. Times round to the nearest source sample. The target is the **minimum total selected gap**, default 750ms, integer 100–5000ms. The editor inserts only the missing zero samples at the span midpoint; it preserves longer spans, including no-op copies. It never removes, fades, stretches, trims or resynthesizes original samples. There is no automatic sentence alignment, nor any guarantee about unselected sentence gaps.

Inputs must be mono/stereo PCM16 WAV, 8–96kHz, <=600 seconds and <=128MiB. Outputs are <=900 seconds/128MiB; decoded audio is capped at 48 million scalar samples. Metadata is <=1MiB before editing; derivative chains are limited to 20 prior transformations. Optional `output_dir` follows generation's absolute local path/mount validation. The editor serializes its own operations (409 if occupied) independently of the GPU worker. Unique WAV/JSON/index writes use existing storage paths; status 201 returns an ordinary artifact accessible through History, playback and downloads. Missing source:404; invalid/nonquiet/out-of-bounds/unsupported/unmounted storage:422. Completed WAV+sidecar may remain discoverable if the final index/retry write fails; inspect History before resubmitting.

Generation `requested`/`effective` settings remain unchanged. `transformation` separately records the reviewed request, source artifact ID/path/SHA256, original source lineage, source frames, exact start/end/insertion offsets, original and inserted gap samples, output frames and CPU processing time. Previous transformations remain in `transformation_history`. No models or GPU locks are used. Pause retry keys live in a separate namespace and do not change legacy generation fingerprints. Identical keyed retries return the existing derivative; changed request or source bytes with the same key returns409. Keep the same key after a client timeout; HTTP timeout does not prove the CPU edit stopped.

In the webpage, open a completed/history take and choose **Adjust pauses**. Listen or seek, copy playback time into the start/end fields (or enter measured times), listen to the selection, and add/remove reviewed gaps. Set the desired total and confirm every selection was reviewed, then create a separate longer-pause version. Raw audio remains available in History. This workflow does not automatically enforce 750ms sentence pauses.
## Additional startup backends

See BACKENDS.md for exact capabilities, storage/locks, licenses and validation. Start with `make serve MODEL=chatterbox|indextts|moss|fish|higgs PORT=...`; selection is not a per-generation request field. Existing Vox/Qwen profiles are also available. Health/capabilities identify the active backend. Unsupported controls fail with 422 before reserving the GPU.

| Additional field | Accepted backend and behavior |
| --- | --- |
| chatterbox.exaggeration | Chatterbox Original only, 0–1, default .5 |
| chatterbox.cfg_weight | Chatterbox Original only, 0–1, default .5 |
| indextts.emotion_reference_id | IndexTTS only: uploaded reference UUID for emotion; separate from speaker reference |
| indextts.emotion_vector | IndexTTS only: 8 finite 0–1 numbers in happy/angry/sad/afraid/disgusted/melancholic/surprised/calm order, sum <=1; mutually exclusive with emotion_reference_id |
| indextts.emotion_alpha | IndexTTS only, 0–1, default 1 when an emotion source is selected; requires a source |
| indextts.duration_factor | IndexTTS only, .7–1.5, default 1; higher is slower |
| indextts.interval_silence | IndexTTS only, 0–2000 ms, default 400; between upstream segments, without automatic sentence splitting |

Chatterbox/Index require a voice/reference; neither accepts prompt-based emotion/accent, presets, reference transcripts or Vox clone modes. Chatterbox fixes max_tokens at 1000 (other values rejected). Index max_tokens is 50–1500 per segment, and requires an explicit supported language. MOSS requires a description and English/Chinese language, supports voice directions as design instructions, rejects references, and limits text to 500 characters. MOSS max_tokens maps to audio frames, with upstream text-derived minimum behavior documented in capabilities.

Fish/Higgs require a reference plus exact saved/override transcript. Their emotion/prosody/pause controls are explicit inline text tags, not the shared emotion/accent fields. The UI shows the appropriate syntax; the service preserves submitted text. The wrapper exposes one reference and offline WAV jobs. Index phoneme annotations also remain explicit in text; optional normalization is disabled.

Native MOSS/Higgs processes release their models after every job, so model_loaded=false while idle is correct. Their load_seconds=null and generation_seconds include startup/load/synthesis; timing_scope explains this. peak_process_rss_gb measures native child peak RSS. Torch metadata distinguishes current MPS active/driver allocations from process-lifetime peak RSS; none is a claimed peak system-RAM measurement. Older validation records retain current_device_memory_gb, meaning end-of-job MPS driver allocation. Requested and effective settings remain distinct. Idempotency records include backend identity; a key from another backend returns 409 rather than replaying the wrong voice job.

A second process cannot own the same DATA_DIR: its lifetime lock fails before restart recovery. A port collision also fails before recovery. Keep old active installations and isolated validation jobs on distinct data roots. WEIGHTS_DIR can point at shared immutable model files without sharing JobManagers or copying weights.

### Model-scoped options and compatibility

New backend-specific options use typed objects named by the exact API model ID: `"chatterbox":{"exaggeration":0.7,"cfg_weight":0.5}` or `"indextts":{"duration_factor":1.2,"interval_silence":750}`. Temperature, text, seed, token cap and published Vox/Qwen fields remain common. Capabilities list these settings under `controls.chatterbox` / `controls.indextts`. Unknown members and inactive-model objects return422. Deprecated flat aliases remain accepted for local pre-integration requests; supplying a namespace and any of its flat aliases together returns422. Use the same request form/key for retries. Prior published generation hashes remain recoverable. Newly written requested/effective metadata stores these options under the same model keys; legacy sidecars stay unchanged. Model-only telemetry (MPS allocation, native log path, watermark/normalization details) is similarly scoped under its model ID; ordinary output properties/timings remain common.

Restricted-model notices appear at every server startup, visibly in the webpage, and under `models.<model-id>` in capabilities, with authoritative license links and bundled source texts. They do not add an acknowledgment gate. Fish requires a separate license for commercial model use. Higgs includes a conditional grant for monetized creator content with prominent acknowledgment, while hosting/product integration and other uses outside that grant require a separate license. Index uses custom terms with a separate-license threshold for very large organizations, not a blanket noncommercial restriction. Read the full model-specific terms; reference uploads are not reclassified as generated outputs.
