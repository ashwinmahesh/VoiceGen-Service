# API behavior notes

These details support the [API guide](../API.md). Most clients only need the task examples and endpoint tables there.

## Saved requests and restarts

- The generator saves validated requested/effective settings, model/mode, destination, reference identity/path/metadata, exact transcript, and reference audio hash before acknowledging a submission. Standalone queue submissions are checked and prepared when the generator picks them up.
- Later catalog edits do not change a prepared job. Missing or changed reference bytes cause a visible failure. IndexTTS's separate emotion reference is checked too. Restore the original bytes before retrying.
- After an interrupted generation, restart preserves its saved queue ordering, including manual moves. A matching complete WAV plus JSON metadata file can be recognized as finished without repeating synthesis. Missing or inconsistent completion records require recovery or review.
- A crash before complete metadata exists can repeat synthesis under the same job ID. Saved admission and retry keys do not guarantee that generation happens exactly once across arbitrary crashes.
- Failed work requires explicit retry. Removal retains the job's deleted record and retry-key mapping; it does not remove completed audio or reference media. Nothing is automatically pruned.

One generator owns queue processing. The separate queue API does not claim jobs, recover generation, or reset active work. Immediate jobs run ahead of normal work for the selected model; each group is first-in-first-out unless explicitly reordered. Manual moves and Run Now update scheduling order/priority, preserve admission sequence and fingerprints, and serialize with claims. A continuous stream of immediate jobs can delay normal work.

The database and journals stay in project `state/`. SQLite uses rollback journaling and FULL synchronous writes. Back up queue state together with outputs and their metadata/indexes. Test overrides through `QUEUE_DB` must remain inside project `state/` and agree between services.

Legacy SSD queues are backed up and migrated by generator startup. Conflicting old/new databases stop startup for review. Legacy JSON job/key records remain readable; interrupted JSON jobs without replayable inputs remain errors requiring History review.

## Model and request limits

| Model or input | Additional limit or behavior |
| --- | --- |
| VoxCPM2 | Combined input budget 4,096 tokenizer tokens. Whole passage sent together. |
| Qwen | Combined input budget 2,048 tokens. Base uses whole-line generation unless phrase pacing is enabled. Reference/transcript conditioning has an effective repetition-penalty floor of 1.5. |
| Chatterbox | Up to 1,000 text characters and a published 400-token input budget. |
| Fish / Higgs / MOSS | Published input budget 2,048 tokens; actual validation/count availability depends on the runtime. |
| Reference upload | At most 5.76 million decoded scalar samples, in addition to the duration/channel/rate limits in the API guide. |
| Phrase pacing | At most 32 phrases and 600 seconds output. Token limit applies per phrase. Common abbreviations, initials, decimals, numeric separators, URLs, emails, and explicit annotations are protected from splitting where recognized. Ambiguous punctuation can remain unsplit. |

Model output limits can cut off speech. Qwen/Fish truncation warnings use available output token counts; Vox uses a duration heuristic. Neither verifies that every final word was spoken.

Chatterbox uses up to the first six seconds for speech-token conditioning, ten seconds for decoder conditioning, and the full waveform for speaker embedding. Index uses up to fifteen seconds of reference. These consumption differences do not alter the stored original recording. Other runtime details are in [BACKENDS.md](../BACKENDS.md).

## Batches and prompt expansion

Batches and their child jobs are saved together. Seeds increment from the base seed, wrapping at 2**32. Distinct descriptions freeze together before speech; retries reuse them. A planning failure marks waiting siblings as errors without silently substituting other prompts. Deleted children stay deleted. Older batches remain seed-only.

`candidate_variant` records the original description, added direction, refinement, fixed contrast direction, method (`llm` or `built_in`), policy/profile, and option index/label. Batch/index/count/variation information is also retained on outputs.

Prompt expansion uses a bounded local child process, with a 180-second limit. Busy previews return 409 and create no speech job. Added prose is limited to 600 characters/60 whitespace-separated words; Fish suggestions to two tags and Higgs to one catalog control. Invalid output is rejected rather than silently repaired. No dialogue or transcript is sent to the LLM.

Expansion records persist under configured data storage. Applying a preview freezes its original/updated guidance and model/profile information into the queued job. A later profile change does not rewrite admitted jobs. Old applied records remain usable even with expansion disabled. Full profile/schema rules are in [prompt expansion details](voice-prompt-expansion.md).

## Pause editing

Pause editing inserts missing zero samples at selected gap midpoints. Sample times round to the nearest source sample; longer existing gaps remain intact. It does not remove, fade, stretch, trim, or regenerate original samples. Its quietness check cannot prove that a selection contains no quiet speech.

Additional limits:

| Item | Limit |
| --- | --- |
| Source audio | PCM16 WAV, mono/stereo, 8–96 kHz, 600 seconds, 128 MiB. |
| Decoded source | 48 million scalar samples. |
| Output | 900 seconds and 128 MiB. |
| Source metadata | 1 MiB before editing. |
| Prior edit chain | At most 20 transformations. |

Editing has its own serial operation slot; a busy edit returns 409 without occupying the speech model. A complete output and metadata may remain discoverable if a final index/key update fails: inspect History after a timeout or error before submitting new work.

`transformation` records the reviewed selection, source ID/path/hash, lineage, source frames, selected and inserted sample positions, output frames, and CPU time. Earlier edits remain in `transformation_history`. Generation `requested`/`effective` fields stay unchanged. Pause-edit keys are separate from generation keys.

## Response metadata

Outputs retain original text, requested/effective controls, model revision/runtime/quantization, reference properties, waveform preview, and available timings. Additional fields depend on the runtime:

| Field or group | Interpretation |
| --- | --- |
| `real_time_factor` | Generation seconds divided by audio duration. |
| `load_seconds` | Separate load timing where available; null for native Higgs/MOSS. |
| Native `generation_seconds` | Includes child startup, model load, and speech generation; `timing_scope` describes it. |
| `peak_process_rss_gb` | Native child peak process memory, not total system/GPU memory. |
| Torch MPS measurements | Current active/driver allocation differs from process-lifetime peak RSS. Older `current_device_memory_gb` means end-of-job MPS driver allocation. |
| `effective.<model>.pacing` | Phrase text and inserted silence positions, or approximate native MOSS instructions. |
| `effective.fish` / `effective.higgs` | Rendered text, resolved tags, and consumed sampling settings. Original dialogue remains in `requested.text`. |

Memory counters may overlap; do not add them together as a system-memory estimate. Higgs's native command does not expose an input-token count, so that count remains unavailable. Audio and metadata use unique IDs and atomic replacement; the output index allows configured custom folders to remain discoverable after restart.

Capabilities include model-specific license links and notices. `commercial_use_notice` is the short startup summary; `notice`, `license`, `license_url`, and `license_file` provide fuller context. Refer to the bundled terms and [model guide](../BACKENDS.md#provenance-and-licenses).

## Compatibility and operation

- Model-specific options and telemetry use exact model IDs. Deprecated Chatterbox/Index flat controls remain accepted; mixing them with their namespaced equivalents is rejected.
- Retry identity includes model and effective request form. Compatibility handling preserves older omitted-mode/nullable-option requests without changing stored jobs. New defaults do not modify already admitted work.
- Candidate, single-take, queue-service, and pause-edit keys have separate scopes. Keep the same endpoint and request form when recovering an unchanged submission.
- Shared accelerator ownership lasts through load/generation/unload, including after a client disconnect. Native children retain reservations until exit even if their parent is killed. One server owns a data folder; do not start additional workers or enable reloaders against the same state.
- Generator queue-health probes are read-only and bounded: 0.3 seconds for the separate API and 0.25 seconds for database lock waits. A queue HTTP outage does not trigger automatic restart or a fallback database.

See [AGENTS.md](../AGENTS.md) for development rules and [BACKENDS.md](../BACKENDS.md) for runtime setup and storage.
