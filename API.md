# VoiceGen API

Use the API to generate dialogue, compare takes, save voices, and queue work for later. For installation and a first-use walkthrough, see [QUICKSTART.md](QUICKSTART.md).

## Choose the right service

| Service | Default address | Use it for |
| --- | --- | --- |
| **Generator** | `http://localhost:5743` | Generate audio, expand prompts, manage voices, and use the studio. |
| **Queue** | `http://localhost:55205` | Add and manage waiting jobs, even when the generator is off. |

Both services use one SQLite database in the project: `state/queue.sqlite3`. The studio calls the generator's APIs, which access that database directly. A separate queue API outage does not stop the studio from generating.

Each service has interactive documentation at **`/docs`** and its complete request/response schema at **`/openapi.json`**. The APIs accept local jobs and return WAV recordings.

### In this guide

- [Generate speech](#generate-speech)
- [Estimated progress](#estimated-progress)
- [Generate multiple options](#generate-multiple-options)
- [Generation fields](#generation-fields)
- [Voices and reference recordings](#voices-and-reference-recordings)
- [Manage jobs through the generator](#manage-jobs-through-the-generator)
- [Reorder or run next](#reorder-or-run-next)
- [Use the standalone queue](#standalone-queue-api-port-55205)
- [Model settings](#model-settings)
- [Sentence and comma pauses](#sentence-and-comma-pauses)
- [Expand voice and acting directions](#voice-guidance-preview-and-application)
- [Edit pauses in finished audio](#edit-pauses-in-finished-audio)
- [History and downloads](#history-and-downloads)
- [Health and errors](#health-and-errors)
- [Resource history and job charts](#resource-history-and-job-charts)

Examples use `VOICE_ID_HERE`, `JOB_ID_HERE`, and similar placeholders. Replace them with IDs returned by the service. IDs are 32 lowercase hexadecimal characters; they are not file paths.

## Resource history and job charts

The generator records memory, CPU, GPU, model, and job-attempt history while running. Read it through these endpoints, or open **Observability** in the UI. Recording keeps seven days in the same project SQLite database as the queue.

```sh
curl --fail-with-body 'http://localhost:5743/api/observability?window=30m&model=higgs'
curl --fail-with-body 'http://localhost:5743/api/observability/models?window=30d&model=higgs'
curl --fail-with-body 'http://localhost:5743/api/observability/jobs?limit=25&offset=0'
curl --fail-with-body 'http://localhost:5743/api/observability/jobs/JOB_ID_HERE'
```

`window` accepts `5m` (default), `30m`, `1h`, `6h`, `24h`, `3d`, and `7d`. The optional `model` filters by speech backend ID; omit it to include all recorded models. Job listings contain separate attempts and peaks. A job detail request selects its latest attempt; add `?run_id=RUN_ID_HERE` to choose another recorded attempt. No retained recording returns 404.

Timeline/detail responses include `points`, a `summary` of original sample counts and peaks, timestamps in Unix seconds, and metric scopes. Values include `cpu_percent`, `rss_bytes`, `gpu_percent`, `gpu_allocated_bytes`, and `gpu_in_use_bytes`; each bucket also has `peak_*` values. CPU at 100% means one core, and memory values are bytes. CPU/RSS cover VoiceGen's process tree. **GPU counters cover the entire Mac**, including other apps; they are not exclusive per-job consumption. Missing readings are null. Longer windows aggregate samples, preserving their peaks. Collector status reports recording errors and dropped samples.

### Generation logs

`GET /api/jobs/JOB_ID_HERE/logs` reads the latest available job-specific runtime output. Add `?attempt=1` to request a particular attempt. It works through the generator, including while the separate queue API is off.

```sh
curl --fail-with-body 'http://localhost:5743/api/jobs/JOB_ID_HERE/logs'
```

The response includes `job_id`, `model`, `attempt`, current queue `status`, `available`, `text`, `truncated`, `max_bytes`, `source`, `updated_at`, `notice`, and `error`. Times are Unix seconds. Reads return at most **64 KiB** from the file's tail; `truncated: true` means earlier output was omitted. The text has terminal control sequences removed and is intended to be displayed as plain text.

Higgs and MOSS retain native runtime logs in the configured media store. Other models currently have sampled stages and generation errors, rather than reliable job-specific console output. Missing/disconnected logs and older attempts that were not retained separately return **200**, `available: false`, empty `text`, and an explanatory `notice`. The endpoint never substitutes a shared backend log or another attempt's output. An unknown job/attempt returns 404; invalid attempt numbers return 422; SQLite failures return 503.

The Observability job view refreshes this output every five seconds alongside the charts. Its **Recorded stages** list comes from sampled resource history; it is separate from the raw runtime output and may miss brief stages.

Model load/unload events have their own **30-day default view and 90-day retention**, with individual durations rather than an interpolated resource curve. `GET /api/observability/models` supports model, operation, boundary, and triggering job/attempt filters, pagination, status counts, and successful mean/max summaries. Python/native weight operations and backend process startup/shutdown are separate, potentially overlapping scopes; interrupted durations remain unknown.

See [resource history](docs/observability.md) for response fields, sampling intervals, retention, model/phase labels, and measurement limits. These endpoints live on the generator, not the standalone queue API.

## Generate speech

These examples use the default **Higgs TTS 3** model. Start it with `make serve MODEL=higgs`. Startup saves the default for both APIs. Every request can choose any set-up `model`; when omitted or null, the saved default is used and frozen on admission. Check `GET /v1/models` for ready IDs and `GET /api/capabilities?model=higgs` for that model’s controls.

**Designing a new voice:** use VoxCPM2, Qwen3-TTS, or MOSS. Higgs requires a saved voice or reference recording. A description-only request to the generator's `/api/generate` or `/api/candidates` returns **422** with an explanation and the supported-model names, before creating a job. Capabilities expose this list at `modes.design.supported_models`, plus `available` and an `unavailable_reason` when disabled. The studio allows entering Design whenever a capable model is ready, leaving the model selection empty when the previous one cannot design. If none is ready, the disabled tab retains the same supported-model tooltip. `supported_models` lists alternatives and `ready_models` lists the ready subset. The independent queue API still accepts a ready model’s unvalidated speech payload with 202; the generator validates its speech payload later and marks an unsupported request `error` with the same explanation, without running inference.

### 1. Upload a voice

Higgs needs a recording and the exact words spoken in it. Replace the path and transcript below with your own:

```sh
curl --fail-with-body http://localhost:5743/api/voices/upload \
  -F 'file=@/absolute/path/voice.wav' \
  -F 'name=My character' \
  -F 'transcript=Exact words spoken in this recording.'
```

**Returns:** HTTP **201** with a saved voice. Copy its `id` for the next request. The voice also appears in the studio's Collection.

### 2. Submit the new dialogue

**`POST /api/generate`**

```sh
curl --fail-with-body http://localhost:5743/api/generate \
  -H 'Content-Type: application/json' \
  -d '{
    "voice_id": "VOICE_ID_HERE",
    "text": "Welcome home. Sit, please.",
    "seed": 42,
    "generate_immediately": true,
    "idempotency_key": "scene-1-take-1"
  }'
```

**Returns:** HTTP **202** with a job. This means the request was saved, not that audio is ready. Relevant response fields look like this:

```json
{
  "id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "status": "queued",
  "generate_immediately": true,
  "result": null,
  "error": null
}
```

`generate_immediately: true` puts the job ahead of ordinary waiting work, after the current take finishes. It does not interrupt active work. API requests default to `false`; the UI defaults to `true`.

### 3. Wait for the result

**`GET /api/jobs/{id}`**

```sh
curl --fail-with-body http://localhost:5743/api/jobs/JOB_ID_HERE
```

| Job status | Meaning |
| --- | --- |
| `queued` | Waiting to run. `paused: true` can mean its model is not currently selected. |
| `running` | In progress; `stage` describes the current step. |
| `done` | Finished. Audio details are in `result`. |
| `error` | Failed. Read `error` for the reason. |
| `deleted` | Removed from the waiting queue. |

A finished job includes `result.id`, `result.audio_url`, `result.duration`, and the actual settings used. See [results and downloads](#history-and-downloads).

#### Estimated progress

Running jobs include an estimated `progress` object. Speech models stop at their own end marker and report no step count, and `max_tokens` is only a ceiling, so the percentage is a **time estimate**, not a measurement:

```json
"progress": {"percent": 62, "phase": "generating", "estimated": true, "eta_seconds": 40, "overdue": false, "basis": "history"}
```

| Field | Meaning |
| --- | --- |
| `percent` | `0` while preparing. While generating, elapsed time divided by the expected time, held at **95** until the audio is saved. `100` when done. |
| `phase` | `preparing` (starting, switching or loading a model, or planning distinct designs), `generating`, `waiting` (batches only, between takes), or `done`. |
| `estimated` | `true` until the take is saved. |
| `eta_seconds` | Estimated seconds left. `null` while preparing or when overdue. |
| `overdue` | `true` once the take runs past its estimate; `percent` stays at 95. |
| `basis` | `history` or `default` (see below). `null` while preparing or when done. |

`progress` is `null` for queued, failed and removed jobs.

When speech generation starts, the generator saves an expected generation time for that take. It learns from the newest 20 successful takes of the same model variant (or the whole model when the variant has fewer than 3), using each take's `result.generation_seconds` and dialogue length. Three or more takes of different lengths fit a robust line (per-take overhead plus seconds per character), so short and long lines both estimate sensibly. Fewer takes use their median seconds per character. A model with no finished takes uses a built-in starting rate (`basis: "default"`), replaced after its first successful take. Model loading and LLM planning are not part of the estimate. The percentage only grows while a take runs. Both the generator and the standalone queue API compute it from the same saved estimate, so they agree.

### 4. Download the audio

Use the finished job's **`result.id`**, replacing `OUTPUT_ID_HERE`:

```sh
curl --fail-with-body \
  'http://localhost:5743/api/audio/outputs/OUTPUT_ID_HERE?download=true' \
  -o "$HOME/Downloads/voicegen-line.wav"
```

You can also play the relative `result.audio_url` by adding the generator's address in front of it.

### Retry safely after a timeout

Use the **same request and `idempotency_key`** to recover the original job after a network error or restart. A new key creates new work. Reusing a key with changed dialogue, settings, model, or priority returns **409**.

A repeated submission does not restart a failed or deleted job. To rerun a failed job, use [the explicit retry endpoint](#manage-jobs-through-the-generator).

## Generate multiple options

**`POST /api/candidates`** accepts the same fields as `/api/generate`, plus:

| Field | Default | Meaning |
| --- | --- | --- |
| `candidate_count` | `2` | Integer from 1–5. |
| `candidate_variation` | `"auto"` | How options differ; see below. |
| `candidate_use_llm` | `true` | Use the enabled local LLM to refine different voice designs. |

```sh
curl --fail-with-body http://localhost:5743/api/candidates \
  -H 'Content-Type: application/json' \
  -d '{
    "voice_id": "VOICE_ID_HERE",
    "text": "Welcome home. Sit, please.",
    "candidate_count": 2,
    "generate_immediately": true,
    "idempotency_key": "scene-1-options-1"
  }'
```

**Returns:** HTTP **202** with a batch `id` and ordered `jobs`. Poll **`GET /api/candidates/{id}`**. Each job has its own audio result; takes run one at a time.

| Variation | Behavior |
| --- | --- |
| `auto` | Contrasting descriptions for Vox/Qwen VoiceDesign/MOSS; alternative takes for recorded or preset voices. |
| `distinct` | Require a supported voice-design mode. Other modes return 422. |
| `seed_only` | Keep one description and vary the random seed. |

For distinct designs, option 1 keeps the original description. Later options explore brighter, darker, focused, or open resonance while retaining the stated constraints. The enabled LLM can refine these directions; otherwise built-in directions are used. The actual description is visible in each job's `effective.description` and `candidate_variant`.

Reference and preset batches, including Higgs, retain the selected speaker recording or preset. Neither repeated seeds nor different descriptions guarantee a particular audible identity.

Batch status is `queued`, `running`, `done`, `partial`, `error`, or `deleted`. `done_count` counts successful takes. The batch's own [`progress`](#estimated-progress) counts each finished take (including failed or removed ones) fully, plus the running take's share; `eta_seconds` adds the remaining takes at the running take's expected time. It is `null` before any take starts and once every take has failed or been removed. `partial` means some finished successfully and others did not. Remove or retry individual jobs using their IDs; successful takes remain available.

Keep the same batch key after a timeout to recover its original jobs. Batch keys are separate from single-take keys. If an unplanned LLM batch waits after restarting with the LLM disabled, restart with it enabled to continue planning.

## Generation fields

Send JSON with `Content-Type: application/json`. Unknown fields and unsupported controls return **422**. Check **`GET /api/capabilities?model=MODEL_ID`** before using a different model.

### Dialogue and voice source

| Field | Meaning and limits |
| --- | --- |
| `model` | Optional ready model ID; omitted/null uses the shared saved default. Frozen at admission. |
| `text` | Required new dialogue. Nonblank, up to 3,000 characters; Chatterbox allows 1,000 and MOSS 500. |
| `voice_id` | Saved voice from Collection. |
| `reference_id` | Uploaded recording from `/api/references`. |
| `preset_voice` | Qwen built-in speaker ID from capabilities. |
| `description` | Voice-design description, up to 1,500 characters. Required for design without a recording or preset. |
| `ref_text` | Exact reference transcript override, up to 3,000 characters. Supported by Higgs, Fish, Qwen reference cloning, and Vox auto/continuation/combined modes. |
| `language` | Defaults to `"auto"`. IndexTTS and MOSS require an explicit supported language. |

Choose at most one of `voice_id`, `reference_id`, or `preset_voice`. Available sources depend on the model. A catalog description does not replace the recording used for cloning.

### Job and output settings

| Field | Default | Meaning and limits |
| --- | --- | --- |
| `seed` | `42` | Integer from 0–4,294,967,295. Selects random variation. |
| `max_tokens` | `1000` | API output limit, generally 50–2,000. Higgs's UI/recommended setting is 2,000. Chatterbox is fixed at 1,000; IndexTTS is capped at 1,500. This is not a requested audio duration. |
| `temperature` | Model-dependent | Sampling variation where supported. See [model settings](#model-settings). Vox does not accept it. |
| `generate_immediately` | `false` | Run ahead of normal waiting work, without interrupting the current job. |
| `idempotency_key` | Omitted | Retry key, 1–128 characters. Reuse only for an identical request. |
| `output_dir` | Configured output folder | Optional absolute folder on the server's Mac. The drive must be mounted and writable. |
| `prompt_expansion_id` | Omitted | ID of a reviewed [prompt expansion](#voice-guidance-preview-and-application) applied to this request. |

The **site** chooses a fresh random 32-bit base seed for each new Generate click using the browser's secure random generator. Batch options retain the API's consecutive-seed rule. Advanced settings offer **Random seed each generation**, on by default; turn it off to enter a fixed seed. Old browser drafts with seeds such as 42 or 43 do not disable this new default. The selected seed is saved with every output. Retrying an uncertain submission or retrying an admitted job keeps its original seed. API requests still default to `42`; send your own `seed` for repeatable requests or client-side randomization. The browser preference is not an API field.

### Acting and model-specific settings

Some established controls are top-level fields: `emotion` (up to 1,000 characters), `accent` (up to 500), Vox's `clone_mode`/`steps`/`guidance`, and Qwen's `steering_emotion`/`steering_strength`.

Other model-specific settings go under the **exact model ID**, for example:

```json
{
  "text": "Welcome home.",
  "voice_id": "VOICE_ID_HERE",
  "higgs": {
    "emotion": "amusement"
  }
}
```

Use only the running model's options. For example, Higgs uses `higgs.emotion`; the top-level prose `emotion` field is unsupported there. Deprecated flat Chatterbox/Index fields remain accepted, but cannot be mixed with their model object in one request.

## Voices and reference recordings

### Upload directly to Collection

**`POST /api/voices/upload`** — multipart form; returns **201** with a saved voice.

| Form field | Required? | Meaning |
| --- | --- | --- |
| `file` | Yes | Audio recording. |
| `name` | Yes | Voice name, 1–100 characters. |
| `description` | No | Catalog notes, up to 2,000 characters. |
| `transcript` | No | Exact words, up to 3,000 characters. A nonempty value is kept and skips recognition. Otherwise automatic transcription supplies it. Review the returned words before cloning with Higgs or Fish. |
| `transcribe` | No | Defaults to `true`. Set `false` to upload without recognition or save an empty transcript. |
| `transcription_language` | No | Defaults to `auto`. Set a supported language code such as `en` to guide recognition; this does not translate speech. |
| `tags` | No | Repeat the field for each tag; up to 20 tags, 1–40 characters each. |

Add metadata to the [upload example](#1-upload-a-voice) with form fields such as `-F 'description=A cultured baritone.' -F 'tags=romanian' -F 'tags=aristocrat'`.

When recognition is needed, the request waits for local CPU transcription before returning 201. No voice is saved if recognition fails; supply exact words, retry, or set `transcribe=false`. Allow up to 150 seconds in the client. `GET /api/transcription` reports availability and the supported language codes without loading a model. It never downloads while serving. A successful automatic result includes `transcription` with `engine`, `model`, detected `language`, `seconds` and `device: "CPU"`. Editing the saved words clears that recognition provenance when the words change.

Use the returned `id` as `voice_id`. Uploading saves a recording without generating or training a voice model. Original upload bytes are retained. Repeating an upload can create another library entry.

### Upload a temporary reference

**`POST /api/references`** takes multipart `file` and optional `transcript`, returning **201**. It also accepts `transcribe` (default `false`) and `transcription_language` (default `auto`). The UI opts in when preparing a voice-upload preview: review its returned `transcript`, then save with `POST /api/voices` using the reference ID and corrected words. This avoids uploading the recording twice. A canceled completed preview remains a reference recording; it does not create a voice in Collection. Use its `id` as `reference_id` without saving it to Collection first.

Download the original with **`GET /api/references/{id}/original`**. For a voice imported directly into Collection, its `source_id` identifies that reference.

### Save a generated take

**`POST /api/voices`** — save an existing output or reference to Collection:

```json
{
  "name": "Romanian aristocrat",
  "description": "Calm, cultured, quietly mocking.",
  "tags": ["romanian", "aristocrat"],
  "output_id": "OUTPUT_ID_HERE"
}
```

Supply exactly one `output_id` or `reference_id`. Optional `transcript` overrides the saved sample's transcript. When omitted, a complete generated take of at most 30 seconds saves its spoken line automatically, with Fish/Higgs performance tags removed. Uploaded references reuse their stored transcript. Saved samples use up to the first 30 seconds; longer or potentially cut-off takes do not automatically reuse the full line. Supply the exact words in that excerpt or leave its transcript empty. Clipping uses the actual audio duration.

The studio preserves transcript drafts when its catalog refreshes. **Save transcript to voice** uses `PATCH /api/voices/{id}`; generating with a saved voice saves any transcript edit before submitting the job. API `ref_text` remains a per-request override and does not modify the saved voice; use PATCH to persist it.

### Search and edit voices

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/voices?q=aristocrat&tag=romanian&sort=newest&limit=24&offset=0` | Search and filter the library. |
| GET | `/api/voices/{id}` | Get voice details and its preview URL. |
| PATCH | `/api/voices/{id}` | Edit `name`, `description`, `tags`, or `transcript`. |
| DELETE | `/api/voices/{id}` | Move the voice to deleted items. |
| POST | `/api/voices/{id}/restore` | Restore the voice. |
| GET | `/api/voice-tags` | List tags and their counts. |

Search matches all query words across names, notes, original designs, and tags, ignoring case and accents. `tag` is an exact tag filter. For multiple selections, repeat `tags`, for example `/api/voices?tags=romanian&tags=narrator`; a voice matches **any** selected tag. Each tag accepts 1–40 characters. If `tag` and `tags` are both supplied, their selections are combined. Search, sort, pagination, and `deleted` still apply. Sort choices are `newest`, `updated`, and `name`. Lists include `data`, `total`, `limit`, `offset`, `has_more`, and `next_offset`.

PATCH replaces the supplied fields. Use `"description": ""`, `"transcript": ""`, or `"tags": []` to clear them; `null` is rejected. Tags are normalized and duplicates removed. Editing catalog notes does not alter its audio.

### Delete and restore Collection entries

Voice library is the UI's first/default Collection tab. Both voice and History cards open a confirmation popup before deletion. Cancellation sends no delete request. The **Show deleted** switch lists retained deleted entries and provides Restore for 24 hours.

Deletion starts a **24-hour retention window**, measured from the first DELETE; repeated DELETE does not extend it. The generator checks once a minute while running and on startup, then removes expired audio, its metadata and output index, plus an uploaded voice’s unshared original/normalized reference. Separate saved voices and other takes are independent copies and stay available. Queued/running jobs defer cleanup of files they still need. Queue records/results stay as history; their expired audio URLs return 404. See [retention details](docs/collection-retention.md).

| Operation | Voice | Take |
| --- | --- | --- |
| Delete | `DELETE /api/voices/{id}` | `DELETE /api/artifacts/{id}` |
| Restore | `POST /api/voices/{id}/restore` | `POST /api/artifacts/{id}/restore` |
| List deleted only | `GET /api/voices?deleted=true` | `GET /api/history?deleted=true` |

`deleted` defaults to `false`, so normal searches, totals, pagination, the voice picker, and tag counts exclude removed entries. `GET /api/voice-tags?deleted=true` counts tags in deleted voices. Search/tag/sort/pagination still apply to the selected list. Getting or editing a deleted library voice returns 404 until it is restored; direct take/audio links work while the files remain. Restore after the 24-hour deadline returns **410** while cleanup is pending, and **404** after removal. New generator submissions cannot select deleted voices; already admitted jobs can finish. Deleted list entries include `deletion` with `deleted_at`, `purge_after`, `cleanup_started`, and optional `cleanup_deferred_reason`. `GET /api/collection/cleanup` reports the retention seconds, sweep interval, running state, last check/error, and last sweep’s removed/deferred counts. This read-only endpoint never triggers deletion.

Delete and Restore return `200` with the same shape:

```json
{"id":"VOICE_OR_TAKE_ID","kind":"voices","deleted":true,"changed":true}
```

`kind` is `voices` or `outputs`. `deleted` reports the requested state; `changed:false` means it was already in that state. Repeating an operation is safe. Unknown/missing entries return 404. Unavailable or unwritable storage returns an actionable 422 without changing files or creating a fallback store. These endpoints belong to the generator; the standalone queue API is unaffected.

### Upload limits

| Limit | Value |
| --- | --- |
| Audio file | At most 25 MiB; complete multipart request at most 26 MiB. |
| Decoded recording | 1–30 seconds, 8–96 kHz, mono or stereo. |
| Formats | WAV, FLAC, or MP3 supported by the installed audio reader. |
| Working copy | Mono 16 kHz PCM16; original upload retained separately. |

Unsupported or unreadable audio returns 422; oversized uploads return 413. Nothing is automatically pruned.

## Manage jobs through the generator

These endpoints use **port 5743**. They manage the same saved jobs used by the UI.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/queue` | List waiting and running jobs across models. |
| POST | `/api/queue/pause` / `/api/queue/resume` | Pause/resume normal waiting work. |
| GET | `/api/jobs/{id}` | Get status, settings, result, or error. |
| DELETE | `/api/queue/{id}` | Remove one waiting job. |
| DELETE | `/api/queue` | Clear waiting jobs. |
| POST | `/api/jobs/{id}/retry` | Retry a failed job; returns 202. |
| POST | `/api/queue/{id}/move` | Reorder a waiting job; returns 200. |
| POST | `/api/queue/{id}/run-now` | Put a waiting job first; returns 200. |

Example listing:

```sh
curl --fail-with-body \
  'http://localhost:5743/api/queue?status=pending&model=higgs&limit=24&offset=0'
```

| Query field | Default | Options |
| --- | --- | --- |
| `status` | `pending` | `pending` means queued or running. Also accepts `all`, `queued`, `running`, `done`, `error`, or `deleted`. |
| `model` | All models | Optional model ID. Also supported when clearing waiting jobs. |
| `limit` | `24` | 1–100. |
| `offset` | `0` | Zero or greater. |

List responses contain `data`, `total`, `counts`, `limit`, `offset`, `has_more`, and `next_offset`. Counts cover all statuses for the chosen model; `total` reflects the chosen status filter.

Deletion affects **waiting work only**. It does not cancel active generation or delete completed audio, History, or voices. Deleting an already deleted job reports zero removed. Retrying requires an `error` job whose model is ready; other states return 409. If its model is unavailable, retry returns 422 with setup guidance and leaves the failed entry unchanged.

`POST /api/queue/pause` pauses normal waiting work; `/api/queue/resume` resumes it. Both APIs share this saved switch. Immediate jobs can run while paused. A running job is never interrupted. Both endpoints return `{"paused": true}` or `{"paused": false}`, including the saved state shared by the two services.

### Reorder or run next

**Move:** `POST /api/queue/{id}/move` with exactly one of these fields:

| Field | Meaning |
| --- | --- |
| `direction` | `"up"` or `"down"`: move one place among waiting jobs in the shared queue, across all models. |
| `before_id` | Place immediately before this waiting job ID. |
| `after_id` | Place immediately after this waiting job ID. |

```sh
curl --fail-with-body http://localhost:5743/api/queue/JOB_ID_HERE/move \
  -H 'Content-Type: application/json' \
  -d '{"direction": "up"}'
```

The UI's left-side handles use the same API. Drag to a highlighted position, or focus a handle and press the up/down arrow keys. Dropping beside a job in another priority group adopts that group's immediate/normal priority. Unchanged positions are a no-op.

**Run Now:** `POST /api/queue/{id}/run-now` puts a waiting job first, ahead of existing immediate jobs:

```sh
curl --fail-with-body -X POST \
  http://localhost:5743/api/queue/JOB_ID_HERE/run-now
```

Both actions return **200** with the updated job and persist across restarts. Fetch the queue again for its current list order. The current candidate batch finishes first. The generator switches models as needed, reusing the current backend for consecutive jobs and releasing it before loading another. Run Now cannot make a missing model available or enable a disabled LLM. Blocked jobs remain queued while eligible work proceeds.

Only waiting jobs and waiting anchors can move. A running/completed/failed/deleted job returns **409**; a missing job/anchor returns **404**. Moving relative to itself or supplying multiple move fields returns **422**. If a job starts while you drag, refresh and try another waiting job.

IDs, admission `sequence`, original generation settings, and submission retry keys stay unchanged. `queue_order` is an internal sort key. Each listed waiting entry also has its global 1-based `position`, retained when filtering by model. `generate_immediately` reports the current priority; the original submission fingerprint is retained. Retry an uncertain submission with its original body, even after reordering it.

For an uncertain move response, refresh the queue before repeating an up/down move: repeating a relative step moves again. Before/after anchors are preferable when you need a specific position.

These same scheduling paths work on either service. Port **55205** returns its normal queue-entry response and can prioritize jobs while the generator is off.

## Standalone queue API (port 55205)

Use this API to add work while the generator is off. Start it with `make queue`; `make serve` also starts or reuses it. Existing entries remain readable, movable and removable without model storage. New admissions require a set-up model; the queue process uses read-only checks and never loads speech weights.

**The request format differs from the generator's:** generation fields go inside `payload`.

### List models

**`GET /v1/models`** on either API returns only ready models:

```json
{
  "default_model": "higgs",
  "active_model": null,
  "runtime_model_switching": true,
  "data": [{"id": "higgs", "name": "Higgs TTS 3", "modes": ["reference"], "is_default": true}]
}
```

Supported registry IDs are `higgs`, `voxcpm2`, `qwen3`, `chatterbox`, `indextts`, `moss`, and `fish`; discovery includes only those whose files, storage and compatible runtime are ready. Unavailable models return **422** on new submission with `make setup MODEL=<id>` guidance. The queue API has no model process, so its `active_model` is null. The generator reports the backend actually loading or holding weights separately from the saved default.

### Add a job

**`POST /api/queue`**

```sh
curl --fail-with-body http://localhost:55205/api/queue \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "higgs",
    "generate_immediately": false,
    "idempotency_key": "scene-2-queued-1",
    "payload": {
      "voice_id": "VOICE_ID_HERE",
      "text": "Welcome home."
    }
  }'
```

| Field | Required? | Meaning |
| --- | --- | --- |
| `payload` | Yes | One generation request. Keep the retry key and priority outside it. |
| `model` | No | Omitted/null uses the shared saved default, initially Higgs. The resolved model is immutable on that job. |
| `generate_immediately` | No | Default `false`; `true` runs ahead of ordinary waiting jobs. |
| `idempotency_key` | No | Retry key, 1–200 characters. |

**Returns:** HTTP **202** with the saved entry and resolved `model`. The queue checks the request structure; the generator checks speech settings when it picks up the job. An invalid speech request becomes an `error` without blocking later jobs.

Start `make serve` to process the shared queue across ready models. `MODEL=higgs` changes the saved default; it does not restrict processing to Higgs. If an admitted model becomes unavailable, its entries stay queued with `blocked_reason` until ready again; they are never rerouted. For 1–5-option batches or LLM design planning, use the generator's `/api/candidates` instead.

### List, change, and remove entries

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/health` | Check the queue service and database. |
| GET | `/api/queue` | List jobs across models. |
| POST | `/api/queue/pause` / `/api/queue/resume` | Pause/resume normal waiting work. |
| GET | `/api/queue/{id}` | Get an entry's payload, status, result, or error. |
| PATCH | `/api/queue/{id}` | Change a waiting entry's `payload` and/or `generate_immediately`. |
| POST | `/api/queue/{id}/move` | Reorder a waiting entry. |
| POST | `/api/queue/{id}/run-now` | Put a waiting entry first. |
| DELETE | `/api/queue/{id}` | Remove a waiting entry. |
| DELETE | `/api/queue` | Clear waiting entries. |

Listing uses the same status/pagination fields as the generator, including optional **`model`** filtering with global positions. Clear can also use `?model=higgs`.

Entries include `id`, `sequence`, `queue_order`, `position`, `blocked_reason`, `model`, `payload`, `generate_immediately`, `status`, `stage`, timestamps, `attempts`, `result`, `error`, `prepared`, and the same [estimated `progress`](#estimated-progress) the generator reports. `position` is the global 1-based waiting position (null for other states); filtering retains global positions. `blocked_reason` explains unavailable-model work and is null when unblocked. `prepared: true` means the generator has checked and saved the generation settings. Lists also include totals, counts, and pagination.

PATCH restrictions:

- The entry must still be waiting.
- Its payload can change only before the generator prepares it and only if it has no retry key.
- PATCH cannot change priority on a keyed entry. Use the explicit move/Run Now actions for scheduling; changing its generation payload requires a new request and key.
- Send at least one field; empty or `null` updates return 422.

The same key, model, payload, and priority recover the original entry, including a removed entry. Changed inputs return 409. Queue-service keys are separate from generator keys. Failed jobs are retried through the generator's retry endpoint.

## Model settings

Choose the model per generator request, for example `"model":"fish"`. `make serve MODEL=fish` saves the default for requests that omit it. Model-specific settings go under that same model ID; validation, defaults and prompt-expansion profiles follow the request’s model.

Capabilities describe the active model's `modes`, `languages`, `controls`, and `limits`. New settings specific to a model use its named JSON object. [VOICE_QUALITY.md](VOICE_QUALITY.md) explains the recommended starting choices and their limitations.

### Higgs TTS 3 — `higgs`

Requires a reference voice and exact transcript. Start without acting tags for a baseline, then compare one change at a time.

| Setting | Default | Accepted values |
| --- | --- | --- |
| `higgs.emotion` | None | Catalog values such as `amusement`, `anger`, or `contemplation`; full list in `controls.higgs`. |
| `higgs.prosody` | None | `speed_very_slow`, `speed_slow`, `speed_fast`, `speed_very_fast`, `pitch_low`, `pitch_high`, `expressive_high`, `expressive_low`. |
| `higgs.style` | None | `singing`, `shouting`, `whispering`. |
| `higgs.top_p` | `0.8` | 0.1–1. |
| `higgs.top_k` | `30` | Integer 1–100. |
| `higgs.text_chunk_size` | `1024` | 80–1,024 characters for native long-text chunking. Smaller chunks can change pacing and expression. |
| `max_tokens` | API: `1000`; UI/recommended: `2000` | 50–2,000 generated audio tokens per text chunk. Explicit values and admitted jobs keep their limits. |
| `temperature` | `0.8` | 0.05–2. |

The service renders these controls before the unchanged dialogue. Manual local tags such as `<|prosody:pause|>` and `<|prosody:long_pause|>` can remain in `text`; they do not promise exact timings. Do not combine a structured control with a manual leading directive of the same category.

These generation fields accept **catalog values**, not arbitrary prose. The UI offers searchable text inputs plus optional LLM suggestions for custom wording. Capabilities expose `values`, `free_text:false`, `searchable:true`, and a `custom_direction_preview` descriptor for each control. Choosing catalog values needs no LLM; translating a custom direction does. The UI keeps unresolved drafts across reloads. Turning expansion off pauses those drafts and uses the last selected native controls or Follow recording; turning it on restores them. Approved native selections remain usable with expansion off.

Top-level prose `emotion`/`accent` fields and description-based voice design are unsupported. Native generation loads and releases the model per job, so `model_loaded: false` while idle is expected.

**If a take reaches `max_tokens` before EOC:** the model did not produce its end-of-chunk marker within that audio budget. Different seeds can hit different lengths. Incomplete audio is not published. Keep any successful options, then submit one replacement with a new key and `"max_tokens":2000`. For longer passages, consider `"higgs":{"text_chunk_size":400}` or shorter dialogue; native chunking can change delivery. This reduces one cause of failure and cannot guarantee the model will stop correctly.

The UI's **Use higher audio limit** action loads the failed option's settings with a 2,000-token limit and one option selected for review. **Retry this option** keeps its original frozen limit. Existing saved UI drafts keep their chosen limit; **Apply recommended defaults** selects 2,000. Capabilities distinguish `controls.max_tokens.default` (API), `ui_default`, and `recommended`.

### VoxCPM2 — `voxcpm2`

Supports voice design and recorded voices. Example for directing a reference voice:

```json
{
  "text": "Welcome home. Sit, please.",
  "voice_id": "VOICE_ID_HERE",
  "clone_mode": "reference",
  "emotion": "Quietly amused, with deliberate emphasis.",
  "accent": "Romanian-accented English."
}
```

| Setting | Default | Meaning |
| --- | --- | --- |
| `clone_mode` | `"auto"` | Chooses the mode below. |
| `emotion`, `accent` | Empty | Written directions for design or standard reference mode. |
| `steps` | `10` | Integer 1–30. |
| `guidance` | `2` | 1–5. |

| Clone mode | Behavior |
| --- | --- |
| `auto` | Uses combined mode for a transcribed recording with no new emotion/accent directions; otherwise standard reference mode. |
| `reference` | Uses the recording for speaker identity, with optional new delivery directions. |
| `combined` | Uses the recording for both identity and recorded performance. Requires its exact transcript. |
| `continuation` | Continues the recorded performance. Requires its exact transcript. |

Combined and continuation modes reject new `emotion` or `accent` directions. Supply the transcript through the saved recording or `ref_text` in supported modes. Standard reference mode does not require a transcript. Vox accepts `language: "auto"`; it does not expose temperature or speed controls.

For design, omit the recording ID and supply `description`, with optional `emotion` and `accent`.

### Qwen3-TTS — `qwen3`

| Voice source | Behavior |
| --- | --- |
| `description` without a recording | VoiceDesign: creates a new voice using description, emotion, and accent directions. |
| `preset_voice` | CustomVoice: directs a built-in speaker. IDs are listed in capabilities. |
| `voice_id` or `reference_id` | Base: clones the recording. An exact transcript enables performance conditioning; without it, uses speaker-embedding conditioning. |

Base rejects new emotion/accent directions. VoiceDesign does not lock one identity across different lines.

`temperature` defaults to **0.9**, range 0–2. Languages: `auto`, English, Chinese, Japanese, Korean, German, French, Russian, Portuguese, Spanish, and Italian. No speed control is exposed.

Built-in speakers can also use the installed experimental emotion-steering pack: `steering_emotion` from capabilities and `steering_strength` from 0–12, default 8. With steering selected and temperature omitted, temperature defaults to 1.1. Steering does not apply to cloned recordings. See [EXTENSIONS.md](EXTENSIONS.md).

### Chatterbox Original — `chatterbox`

Requires a recorded voice, with text up to 1,000 characters. Supports `language: "auto"` or `"English"`; prose emotion/accent and `ref_text` controls are unsupported.

| Setting | Default | Range |
| --- | --- | --- |
| `chatterbox.exaggeration` | `0.3` | 0–1. |
| `chatterbox.cfg_weight` | `0.5` | 0–1. |
| `temperature` | `0.8` | 0.05–2. |
| `max_tokens` | `1000` | Fixed; other values are rejected. |

Higher exaggeration is an experiment, not a guarantee of better acting or accent. Available punctuation controls are described [below](#sentence-and-comma-pauses).

### IndexTTS 2.5 — `indextts`

Requires a recorded speaker and an explicit `language`: **English, Chinese, Japanese, Spanish, or Arabic**. Optional emotion sources change delivery while retaining the speaker reference.

| Setting | Default | Meaning and limits |
| --- | --- | --- |
| `indextts.emotion_reference_id` | None | A separately uploaded recording supplying emotion. |
| `indextts.emotion_vector` | None | Eight values in happy, angry, sad, afraid, disgusted, melancholic, surprised, calm order. Each 0–1; sum at most 1. |
| `indextts.emotion_alpha` | `1` with a source | Blend strength, 0–1; requires an emotion source. |
| `indextts.duration_factor` | `1` | 0.7–1.5; higher is slower. |
| `indextts.interval_silence` | `400` | 0–2,000 ms between runtime segments, not necessarily sentences. |
| `temperature` | `0.8` | 0.05–2. |
| `max_tokens` | `1000` | 50–1,500 per segment. |

Choose an emotion recording or a vector, not both. Prose emotion/accent and `ref_text` controls are unsupported. Explicit pronunciation annotations in `text` pass through unchanged.

### MOSS VoiceGenerator — `moss`

Design-only: requires `description` and explicit `language: "English"` or `"Chinese"`. It accepts `emotion` and `accent` as design instructions, but no reference recordings.

```json
{
  "text": "Welcome home.",
  "description": "A rich, smooth baritone with measured delivery.",
  "emotion": "Quietly amused.",
  "language": "English"
}
```

Text is limited to **500 characters**. `temperature` defaults to **1.5**, range 0.05–2. `max_tokens` limits audio frames; the runtime can raise it to its text-derived minimum. A seed does not preserve one speaker across new lines. Save a result and clone it with a reference-capable model if needed.

### Fish Audio S2 Pro — `fish`

Requires a reference and exact transcript. Use supported delivery tags rather than top-level prose emotion/accent fields.

```json
{
  "text": "Welcome home.",
  "voice_id": "VOICE_ID_HERE",
  "fish": {
    "delivery_tags": ["low voice", "quietly amused"]
  }
}
```

| Setting | Default | Range |
| --- | --- | --- |
| `fish.delivery_tags` | None | Up to four unique tags, each 1–60 visible characters, without brackets or control delimiters. |
| `fish.top_p` | `0.7` | 0.1–1. |
| `fish.top_k` | `30` | Integer 1–100. |
| `temperature` | `0.7` | 0.05–2. |

Structured tags become `[tag]` prefixes; the original `text` is retained. Manual tags can also appear in `text`, but conflicting or duplicate manual/structured tags return 422. No repetition-penalty or speed control is exposed.

## Sentence and comma pauses

Fish, IndexTTS, Chatterbox, and Qwen recorded/preset voices default to additional pauses at punctuation. Put controls under the request model's ID:

```json
{
  "text": "My guests. You came. Sit, please.",
  "voice_id": "VOICE_ID_HERE",
  "qwen3": {
    "punctuation_pauses": true,
    "sentence_pause_ms": 500,
    "comma_pause_ms": 200
  }
}
```

| Field | Default | Range |
| --- | --- | --- |
| `punctuation_pauses` | `true` in supported modes | Set `false` for whole-line generation. |
| `sentence_pause_ms` | `500` | Integer 0–3,000. |
| `comma_pause_ms` | `200` | Integer 0–1,500. |

For these four models, values are **extra silence** between separately generated phrases, in addition to pauses already produced by the model. They are not exact total gap lengths. Zero disables that punctuation class. Final punctuation adds no trailing pause.

The same speaker settings are used throughout. More phrase calls can take longer or change delivery, so compare against `punctuation_pauses: false`. Limits are 32 phrases and 600 seconds of output; output-token limits apply per phrase.

Special cases:

- **Qwen VoiceDesign:** whole-line generation; save and clone a voice to use phrase pacing.
- **Fish manual tags:** whole-line by default; explicitly combining manual tags and phrase pacing is rejected. Structured `delivery_tags` work with phrase pacing.
- **MOSS:** accepts the fields under `moss` as approximate total-pause instructions in one generation pass. It does not insert measured silence.
- **Higgs and Vox:** these fields are unsupported. Use native Higgs pause tags or [edit a completed take](#edit-pauses-in-finished-audio).

## Voice guidance preview and application

The optional local **Qwen3.5-2B 4-bit LLM** expands written voice and performance directions using model-specific rules. `make serve` enables it by default; `NO_LLM=1` disables it. Check `health.prompt_expansion.enabled` and `capabilities.limits.prompt_expansion` for availability.

It does not receive dialogue, exact reference transcripts, recordings, file paths, or voice IDs. Single-take requests are not expanded automatically.

### Preview

**`POST /api/prompts/expand`**

```json
{
  "generation": {
    "voice_id": "VOICE_ID_HERE",
    "text": "Welcome home. Sit, please."
  },
  "guidance": "Quietly amused, with restrained emphasis."
}
```

`generation` is a normal request for the running model. Optional `guidance` adds up to 1,500 characters of written direction.

For Higgs, target a particular control by adding a preview-only `higgs` object **beside** `generation`. This can replace that control while preserving the other native settings:

```json
{
  "generation": {
    "model": "higgs",
    "voice_id": "VOICE_ID_HERE",
    "text": "Welcome home.",
    "higgs": {"emotion": "amusement", "prosody": "expressive_low"}
  },
  "guidance": "Speak in a hushed whisper.",
  "higgs": {"control": "style"}
}
```

`higgs.control` accepts `emotion`, `prosody`, or `style`. It restricts the LLM to that category's supported values; it does not add custom native tags. The response's `higgs.control` identifies the target, and `updates.higgs` retains the other controls. A no-match suggestion returns `changed:false` and unchanged settings. An unknown/out-of-category completion fails with 422 without creating a preview or speech job. Other models reject this Higgs-specific preview option. Omitting it lets the LLM choose one category to change while preserving the others, even when structured controls are already selected. Manual Higgs tags in spoken `text` must be cleared before either kind of suggestion; use the structured controls instead. Empty delivery guidance returns 422; the UI explains this and disables its preview button until guidance is entered.

**Returns:** HTTP **200** with `id`, `original`, `updates`, `context`, `request_guidance`, and `llm`. This creates no speech job.

| Model or mode | Possible suggestion |
| --- | --- |
| Vox design / flexible reference | Added description / acting direction. Auto combined mode is unsupported; choose `clone_mode: "reference"` for new directions. |
| Qwen VoiceDesign / built-in speaker | Added description / acting direction. Base cloning is unsupported. |
| MOSS | Added voice-design direction. |
| Fish | Up to two short delivery tags. |
| Higgs | At most one existing emotion, prosody, or style control. |
| Chatterbox / IndexTTS | Prompt expansion is unsupported. Use their native controls. |

For Vox with `voice_id` or `reference_id`, `clone_mode:"auto"` resolves to flexible `reference` when a new emotion or accent direction is supplied. An expansion preview with a nonempty `emotion` is supported in that case; keep the same Auto setting when applying it. With an exact transcript and empty directions, Auto resolves to `combined`, which follows the recorded performance and does not accept expansion. Explicit `combined` and `continuation` also keep that boundary. The UI uses the same resolution when enabling its preview. The two UI tasks and Voice source menu still submit the existing `voice_id`, `reference_id`, or `preset_voice` fields; no API source field was added.

A suggestion may be empty. Review it: a valid instruction can still sound wrong or weaken an accent.

The response includes `request_guidance` (your written direction), `original` (the fields before expansion), `updates`, and `changed`. Check `changed` before applying: `false` means no settings changed. Higgs also returns `higgs.message` explaining its control/recording boundary. It selects one existing catalog control rather than expanding prose; for example, “more expressive intonation” can select `{"higgs":{"emotion":null,"prosody":"expressive_high","style":null}}`. An accent-only request should keep the original controls because Higgs has no text accent setting. Use a short acting direction or choose the native controls directly when a complex request is rejected.

### Apply a reviewed suggestion

Merge `updates` into the original generation request, add `prompt_expansion_id` from the preview, and submit to `/api/generate` or `/api/candidates`.

Python example using the `requests` package, with Higgs running:

```python
import requests

base = "http://localhost:5743"
generation = {
    "voice_id": "VOICE_ID_HERE",
    "text": "Welcome home. Sit, please.",
}
response = requests.post(base + "/api/prompts/expand", json={
    "generation": generation,
    "guidance": "Quietly amused, with restrained emphasis.",
})
response.raise_for_status()
preview = response.json()
print(preview["updates"])
if not preview["changed"]:
    print(preview.get("higgs", {}).get("message", "No change suggested."))
    raise SystemExit("Original settings kept; no speech submitted.")
if input("Apply this guidance? [y/N] ").strip().lower() != "y":
    raise SystemExit("Original guidance kept; no speech submitted.")

applied = {
    **generation,
    **preview["updates"],
    "prompt_expansion_id": preview["id"],
    "idempotency_key": "expanded-line-1",
}
response = requests.post(base + "/api/generate", json=applied)
response.raise_for_status()
print(response.json())  # Poll /api/jobs/{id} for its result.
```

Keep the same voice and guidance context when applying the preview. Changing them returns 422; preview again for a different direction. The preview can be reused for another line with the same voice and guidance. To restore the original, use `original` fields and omit `prompt_expansion_id`.

Distinct voice-design batches can separately use automatic per-option planning through `candidate_use_llm`. Already saved expansions/plans are retained across restarts. See [prompt expansion details](docs/voice-prompt-expansion.md) for profile rules and evaluation.

## Edit pauses in finished audio

**`POST /api/artifacts/{id}/pauses`** creates a separate recording by extending quiet gaps you have listened to and selected. It does not regenerate speech or change the original.

```json
{
  "reviewed": true,
  "spans": [
    {"start_seconds": 1.20, "end_seconds": 1.55}
  ],
  "minimum_gap_ms": 750,
  "idempotency_key": "reviewed-gap-take-1"
}
```

Offsets above are examples: measure them in your own recording. Select the **entire quiet gap**, not a guessed sentence boundary.

| Field | Meaning |
| --- | --- |
| `reviewed` | Must be `true`: you have listened to each selection. |
| `spans` | 1–30 ordered, nonoverlapping gaps strictly inside the source. Each must be at least 20 ms and pass the quietness check. |
| `minimum_gap_ms` | Desired minimum total selected gap, default 750; integer 100–5,000. Longer gaps stay longer. |
| `output_dir` | Optional absolute output folder on the Mac. |
| `idempotency_key` | Optional 1–128 character retry key. |

**Returns:** HTTP **201** with the new output. It appears in History and uses the normal audio/metadata endpoints. The original samples remain in their original order; only missing silence is inserted. This does not automatically find sentences or alter unselected pauses.

The source must be PCM16 WAV, mono/stereo, 8–96 kHz, at most 600 seconds and 128 MiB. Quiet selections must have every sample within ±32. See [detailed limits and recovery](docs/api-behavior.md#pause-editing) for additional constraints. Identical keyed retries recover the edit; changed input or source bytes with the same key return 409.

## History and downloads

An **output** (also called an artifact in endpoint names) is a generated or edited recording. Its ID is available as `job.result.id`.

Browser links use `/?candidates=BATCH_ID&option=OUTPUT_ID`, `/?job=JOB_ID`, or `/?artifact=OUTPUT_ID`. The option is optional. Monitoring a batch child reopens all its options. Studio remembers its last recording per browser origin and serving project, separately from model-specific form drafts, and reconnects using GET requests only. Explicit links take precedence. Observability uses `/observability?job=JOB_ID`, with a return link to the recording. Add `#listening-room` to Studio recording links to land on the audio panel; return links include it automatically. The cross-page recording strip uses these existing GET endpoints and never submits jobs. A queued job has no resource samples yet; its detail API continues to return 404 until an attempt is recorded. Audio/queue APIs are unchanged.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/history?q=&limit=24&offset=0` | Search generated text, voice descriptions, and acting directions; newest first. |
| GET | `/api/artifacts/{id}` | Get a recording's details. |
| DELETE | `/api/artifacts/{id}` | Remove it from active History while retaining audio and job links. |
| POST | `/api/artifacts/{id}/restore` | Return it to active History. |
| GET | `/api/metadata/{id}` | Download its generation settings as JSON. |
| GET | `/api/audio/outputs/{id}` | Play a generated or edited WAV. |
| GET | `/api/audio/voices/{id}` | Play a saved voice. |
| GET | `/api/audio/references/{id}` | Play a reference's working copy. |

Add `?download=true` to an audio URL to download it. Absolute `path` and `metadata_path` values refer to files on the Mac; use audio URLs from a client on another device.

Results include duration, sample rate, channels/format where available, waveform preview, and:

- **`requested`:** the original settings you sent.
- **`effective`:** resolved settings actually used, including model-specific controls and pacing.
- **Timings and model details:** model revision, runtime, generation time, and available memory measurements.
- **Warnings:** possible output truncation or other reported limits. A warning is not a word-accuracy check; listen to the take.

History uses `limit` 1–100 (default 24) and nonnegative `offset`, with totals and pagination. For less common metadata fields and their limits, see [response metadata](docs/api-behavior.md#response-metadata).

## Health and errors

### Health and available controls

Generator health uses `service:"voicegen-service"`, `mode:"generator"`, `api_version:"4.0.0"`, `queue_scope:"shared"` and `runtime_model_switching:true`. Identify the service by these fields plus `project` and `queue_database`; do not identify it by the selected or active model. `default_model` is the saved admission default, `active_model` is the actual loading/resident speech backend (or null), and `queue_paused` reports the shared normal-queue switch. `blocked_models` contains latest readiness errors. Switching model does not require restarting the generator.

Queue health uses `service:"voicegen-queue"`, `role:"queue-crud"`, `api_version:"4.0.0"`, `queue_scope:"shared"`, `project`, and `queue_database`. It also reports `status`, `default_model`, and `paused`. Both VoiceGen health endpoints publish **`api_version`**, not `version`. External launchers such as Daemon Monitor must check these published identities rather than assume another project's health field names.

| Method | Endpoint | Service | Purpose |
| --- | --- | --- | --- |
| GET | `/health` | Both | Check service/database status. Generator health also reports model readiness, current work, and errors. |
| GET | `/api/capabilities` | Generator | Discover the selected model, sources, languages, controls, limits, and license notices. |
| POST | `/api/model/unload` | Generator | Release a loaded model's memory when no generation is active. |

Unloading during generation returns 409. Pending work remains queued and can load the model again. Native Higgs/MOSS models already exit after each job.

Generator health reports two separate queue checks:

| Field | Values | What to do if unavailable |
| --- | --- | --- |
| `queue_service.status` | `ready`, `unavailable`, `mismatch` | Check the separate queue API. The studio can continue using SQLite directly. |
| `queue_storage.status` | `ready`, `unavailable` | Restore access or free space in project `state/`; new queue operations need the database. |

A `mismatch` means the queue API belongs to another project/database or has the wrong role. Its message explains the issue. These checks do not restart services or cancel work.

### HTTP status codes

| Status | Meaning | Next step |
| --- | --- | --- |
| **200** | Read, preview, or update succeeded. | Read the response. |
| **201** | Voice, reference, or edited recording created. | Save its ID. |
| **202** | Generation accepted. | Poll the job or batch ID. |
| **404** | ID or media unavailable. | Check the ID and storage. |
| **409** | Conflicting retry key, invalid job state, or a busy preview/edit/unload operation. | Read the error; keep the original key for unchanged submission retries. |
| **413** | Request too large. | Reduce the upload/body size. JSON requests are limited to 128 KiB. |
| **422** | Invalid or unsupported input/control, missing required transcript, unavailable expansion, or media/storage validation error. | Correct the request using capabilities and the error message. |
| **503** | SQLite unavailable or required model assets incomplete. | Restore storage or finish setup/download; retry unchanged work with its original key. |

SQLite failures include `Retry-After: 2` and an error body like:

```json
{
  "code": "queue_storage_unavailable",
  "detail": "Queue storage is temporarily unavailable. ..."
}
```

A job that fails after acceptance reports `status: "error"` through its job endpoint. A client timeout or disconnect does not cancel accepted work. Check its existing ID before submitting another take.

### Local access

The services listen on localhost and are intended for trusted local clients. Browser writes must be same-origin; cross-origin browser access is not enabled. These APIs have no LAN/internet authentication layer. See [BACKENDS.md](BACKENDS.md) for operation and [API behavior notes](docs/api-behavior.md) for restart guarantees, limits, and compatibility.
