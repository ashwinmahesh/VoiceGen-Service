# Resource history

Open **Observability** in the studio header, or visit `http://localhost:5743/observability` while the generator is running. Recording starts automatically when the generator starts. You do not need to keep the page open. Its header is the same as Studio and Collection, including Queue, service status, and Release model memory. The separate recorder status appears beside the page title.

**Over time** offers 5m, 30m, 1h, 6h, 24h, 3 days, and 7 days. Filter by the speech model used by each job. Model colors and hover details identify the model recorded with each point. **By job** lists recorded attempts with their status, duration, and resource peaks. Select **Charts & logs** to see a whole attempt on a shorter elapsed-time axis. Choose another attempt to compare a retry. A Queue entry's **Resources & logs** link opens the same view.

**Model activity** keeps the infrequent loads and unloads in a separate, longer history. Choose **24h, 7 days, 30 days** (the default), **90 days**, or **All retained**. Filter by model, Load/Unload, and measured boundary. Individual dots and squares show duration against time, without connecting unrelated events. The plot shows the current page; the table provides exact times, duration, status, reason, and a link to the triggering job. Summary counts and successful-operation means/maxima cover **all matching events**, grouped by model and boundary. A failed operation is excluded from those means and maxima.

![Model activity with demonstration timing data](images/model-activity.jpg)

The screenshot above uses synthetic history, not measured performance. A selected job also shows its load/unload operations across the complete attempt, including release of the **previous** model when it caused a switch. Operations outside a job, such as Release model memory and server shutdown, remain visible in Model activity. Links to older attempts keep showing their retained model events even after the resource curves expire. Warm model reuse adds no load event.

## What load and unload mean

| Boundary | Timed operation |
| --- | --- |
| Python weights | Model loader and adapter validation; or dropping model references, garbage collection, and allocator cache clearing. Applies to Vox, Qwen, Fish, Chatterbox, and Index. Lazy GPU execution and first-inference compilation are not forced into the load timer. |
| Native weights & session | Higgs/MOSS registry loading plus native task-session construction. Input preparation and speech generation follow this boundary. |
| Native release | Destruction of that native session and model before its per-take CLI exits. |
| Prompt LLM weights | Local LLM loading, or explicit reference release, garbage collection and cache clearing. Its speech backend still identifies the request that needed expansion. |
| Backend process | Launch to ready IPC, or close request to **verified exit of the owned process group**, including teardown and any escalation/reaping. This is separate from weight-only timing. |

These scopes can overlap. Do not add the backend process shutdown and Python weight-release durations together. A completed load means its loader returned; it does not guarantee all work for the first inference has already happened. Native models load and release per take; Python models can remain warm.

Durations use monotonic clocks; wall timestamps identify when events happened. Running operations show **Measuring…**. Failed operations retain their observed duration. An operation interrupted by a process crash or generator restart has **Unknown** duration; server downtime is never counted as load/unload time. Events have model/session and optional job/attempt IDs, but no dialogue, prompts, paths, or exception text.

For an existing Higgs/MOSS installation, run `make setup-env MODEL=higgs` once after pulling this update (MOSS uses the same binary). This rebuilds the pinned native runtime with the small reviewed timing hook. It does not download model weights. New setup includes it automatically. Unrelated native source changes are preserved and rejected rather than overwritten. Timing uses a private inherited pipe, with no verbose tensor logging or extra GPU synchronization.

![Full job resource page with demonstration data](images/observability-job.jpg)

This screenshot uses demonstration data, not measured model performance.

Open **Resources & logs** from any listening-room option or selected take. **Return to recording** brings back the whole batch and selected take. Queued jobs show a waiting message until an attempt has samples, then update automatically. Returning to the page after a lost connection refreshes the charts; expired resource samples do not remove the audio or retained model events.

## Generation logs

A selected job shows **Generation log** beneath its resource charts. It refreshes every five seconds and has a **Copy visible log** button. Higgs and MOSS currently retain native runtime output; other models show a clear unavailable message, their sampled stages, and any current generation error. No output is borrowed from a shared server log.

The view reads the latest 64 KiB and marks omitted earlier output. Logs stay in their existing configured media-store `logs/` folder, separate from the resource database and its seven-day retention. Disconnecting that store leaves charts readable and explains why the log is unavailable. Older retries were not archived separately; selecting one does not display the newest retry's log. **Recorded stages** is a sampled timeline for the selected attempt, not a complete console trace.

## What is measured

| Metric | Scope and interpretation |
| --- | --- |
| VoiceGen memory | Sum of current resident memory (RSS) for the generator and its descendants, including native speech processes and the prompt LLM. Shared pages may be counted more than once. This is not a physical-memory footprint measurement. |
| VoiceGen CPU | Change in process CPU time divided by elapsed time across that process tree. 100% equals one core; multiple cores can exceed 100%. The first reading and intervals shorter than 250 ms have no CPU value. |
| GPU utilization | macOS driver utilization for the whole device, including other applications. It is not isolated to VoiceGen or a particular job. |
| GPU memory allocated | The driver's device-wide `Alloc system memory` counter. Apple Silicon uses shared memory; this is neither dedicated VRAM capacity nor a value to add to RSS. |
| GPU memory in use | The driver's `In use system memory` counter, also saved in the API. It is distinct from the allocated counter and can overlap process RSS. |

CPU and RSS come from `/bin/ps` using numeric process IDs, parent IDs, CPU time, RSS, and start time. Command lines and other processes' names are not saved. GPU counters come from `PerformanceStatistics` in `/usr/sbin/ioreg -a -r -d 1 -c IOAccelerator`. No administrator permissions, external service, or network access are needed. These driver fields can vary between macOS versions. If counters disappear or time out, their values remain unavailable rather than becoming zero.

The job’s speech backend and concrete model are recorded with each sample and attempt. During a prompt-expansion phase, the sample's model identifies `mlx-community/Qwen3.5-2B-4bit`, while its backend still identifies the selected speech model. Each point also records its phase. Job charts cover validation, expansion, model loading, generation, and final saving while the worker owns that attempt. Idle residency and standalone prompt previews are included in the overall timeline. A model that runs in a child process is included automatically through its parent relationship.

## Recording and storage

The generator records every second while a job is active and every five seconds while idle, plus start/end snapshots so short jobs still get a record. These are sampled measurements, not a guarantee of catching every instantaneous peak or CPU work from a child that exits between snapshots. Each system command has a 750 ms timeout. No inference imports or GPU synchronization are needed to gather metrics.

History uses additional tables in the **existing** project database, `state/queue.sqlite3`: `resource_sessions`, `resource_runs`, `resource_samples`, and `model_lifecycle_events`. `QUEUE_DB` also isolates resource history during tests. There is no second database and nothing is written beside the model weights. Resource tables contain timestamps, numeric measurements, model IDs, job IDs, and status; they do not duplicate dialogue, prompts, transcripts, or audio.

Raw samples and completed resource attempts are retained for **seven days**; sparse model lifecycle events stay for **90 days**, independently of their resource curves. All retained means the available history within that retention period. Cleanup runs about every five minutes while the generator runs and leaves all queue entries, outputs, and voice data untouched. SQLite can reuse freed pages; the database file need not shrink immediately. Recording stops when the generator stops. Restart gaps are visible, and attempts whose recorder ended unexpectedly are marked interrupted in resource history without changing their queue status. Pre-existing jobs do not acquire historical measurements retroactively.

Resource writes wait up to 200 ms for SQLite and then drop the sample, report the problem, and retry later. Monitoring failures do not fail or replay generation. The page displays collector errors, unavailable-counter notices, and the current session's dropped-sample count. The independent queue API does not collect metrics, load models, or serve this page. The generator reads history directly from SQLite, so an independent queue API outage does not affect it.

Lifecycle boundaries instead have a bounded in-memory retry buffer (1,000 boundaries) during a storage outage. The collector reports pending/dropped boundaries separately from resource samples. The buffer is not crash-durable; a boundary that could not reach SQLite before a crash cannot be reconstructed. Stored unfinished boundaries are reconciled only after the generator acquires its exclusive consumer lease. Existing history upgrades additively, with no backfill.

## API

| Request | Result |
| --- | --- |
| `GET /api/observability?window=30m&model=higgs` | Timeline, peaks, sampled model IDs, metric scopes, and collector status. Omit `model` for all speech backends. |
| `GET /api/observability/models?window=30d&model=higgs&action=load&limit=25&offset=0` | Paginated model events, status counts and successful mean/max durations across all matches. |
| `GET /api/observability/models?window=all&job_id=JOB_ID&run_id=RUN_ID` | Lifecycle operations for an entire recorded attempt, including release of a previous model. Omit `run_id` for all that job's attempts. |
| `GET /api/observability/jobs?model=higgs&limit=25&offset=0` | Recorded attempts, newest first. Limits are 1–100. Includes IDs, attempt number, model, status, times, sample count, and peaks. |
| `GET /api/observability/jobs/JOB_ID` | Latest recorded attempt for that job, its charts, and up to 100 recorded attempts. |
| `GET /api/observability/jobs/JOB_ID?run_id=RUN_ID` | Select a particular recorded attempt belonging to that job. |
| `GET /api/jobs/JOB_ID/logs?attempt=1` | Read available runtime output for one attempt; omit `attempt` for the latest. |

The `model` filter is the speech backend ID, such as `higgs` or `voxcpm2`. It includes prompt-expansion samples associated with that backend. Missing retained job history returns 404; invalid windows and pagination return 422. SQLite read failures return 503 and can be retried. Both APIs are documented through the generator's `/docs` and `/openapi.json`.

For resource-series responses, times are Unix seconds in UTC and byte metrics are bytes. The UI displays memory in GiB. Each such response includes `start`, `end`, `bucket_seconds`, `summary`, and `points`. `summary.samples` counts original snapshots; `peak_*` fields preserve their maxima. Points contain averages plus `peak_*` and sample counts for each time bucket, session, model, and phase. Responses target roughly 360 buckets and cap at 5,000 points with `truncated: true` when necessary. Gaps and missing values are not filled with zero. Hover or use a chart's arrow keys to compare bucket averages with their recorded peaks. Individual attempts stay separate after retries or restarts.

### Model event responses

`GET /api/observability/models` accepts `window=24h|7d|30d|90d|all` (default `30d`), an optional supported `model`, `action=load|unload`, and `scope=runtime_process|weights|native_weights|native_release|llm_weights`. Optional `job_id` and `run_id` select a triggering job/attempt. Pagination uses `limit=1..100` (default 25), `offset>=0`, newest start first. Invalid values return 422; SQLite read failures return the usual structured 503.

The result contains `data`, `total`, `counts`, `summaries`, `limit`, `offset`, `has_more`, `window`, `start`, `end`, `retention_seconds`, `scopes`, and `collector`. `all` has `start: null`. Each event has `id`, `session_id`, nullable `run_id`/`job_id`/`attempt`, `backend`, concrete `model`, `action`, `scope`, `reason`, `started_at`, nullable `ended_at`/`duration_seconds`, and `status` (`running`, `success`, `error`, `interrupted`). Duration is in seconds, independent of changes to the wall clock. Reasons include first use, variant/model switches, per-take loading, prompt expansion, manual memory release, shutdown, and failure cleanup.

`counts` groups every matching event by status. Each `summaries` row groups successful observed durations by `backend`, `model`, `action`, and `scope`, with `count`, `mean_seconds`, and `max_seconds`. These describe operations, not resource-sample averages. They cover all matching pages. Running/interrupted durations are null; failure durations are recorded but excluded from successful summaries. Collector fields `pending_lifecycle_events`, `dropped_lifecycle_events`, and `lifecycle_error` describe telemetry storage trouble without altering jobs.
