# Working on VoiceGen-Service

Read [README.md](README.md), [API.md](API.md) and [BACKENDS.md](BACKENDS.md) before changing behavior. For operating a running service, follow [the service skill](skill/local-voice-studio/SKILL.md). Keep those documents aligned with implementation changes.

## Service boundaries

- The generator serves the studio UI, Collection, Queue modal and generation API on localhost:5743. It validates voice requests, expands prompts and runs the startup-selected backend.
- The independent queue API uses localhost:55205. It provides SQLite CRUD, health/OpenAPI and `GET /api/models` from static metadata. Keep inference imports, asset/readiness probes, generation UI and worker management out of this process.
- `model_registry.json` supplies supported IDs/names; `service_defaults.json` selects Higgs as the default. Queue submissions that omit `model` resolve that default at admission, independently of the running generator. Preserve the model recorded on existing jobs.
- All backends share one production database, `state/queue.sqlite3`. Only the generator takes the exclusive consumer lease, claims matching jobs and performs recovery/reconciliation. Restarting the queue API must not reset active work. Retain priority/FIFO, idempotency, waiting-only removal and persisted prompts/settings.
- Distinguish queue HTTP failure from SQLite failure. Keep direct SQLite generation/CRUD working during HTTP outages; expose a bounded read-only `health.queue_service` diagnostic and a UI notice without auto-restarting the API. `health.queue_storage` must remain responsive during database lock contention. Return structured503/Retry-After for SQLite request failures, retain job IDs/retry keys and recover the UI when storage returns. Do not infer cancellation or create a fallback database.
- Keep genuinely shared request fields at the top level and model-specific controls under the exact model ID. Preserve existing generator API compatibility; the same queue route names on the two ports have different documented request bodies.

## Storage and privacy

Keep source, environments, SQLite/journals, process leases and service logs/PIDs inside the serving project. `QUEUE_DB` isolation overrides must stay under project `state/`. Keep model weights, caches, references, saved voices and generated audio on their configured data storage; preserve existing SSD paths and catalogs. Fail clearly on missing model/media volumes rather than creating fallback downloads. The queue API itself must work without the model SSD.

Keep private state, media, weights, environments, local settings and credentials out of Git. Inspect staged files before committing. Do not overwrite or relocate existing data as part of setup or maintenance.

## Commands and checks

- `make queue`: foreground queue API alone; reuse a matching instance.
- `make serve`: select a ready backend, ensure/reuse the independent queue API, then run the generator in the foreground. LLM is enabled by default; `NO_LLM=1` disables it.
- Generator Ctrl-C finishes active work and leaves pending jobs and the queue API intact. `make stop` stops only a verified idle managed generator; `make stop-queue` separately stops the verified queue API.
- `make test`: complete automated suite with fake inference, including queue/generator process isolation and crash/restart recovery. It does not establish real speech quality. `make smoke` performs real inference against a running generator when appropriate to the change and authorized work.

Use isolated project-local test state and benign media fixtures. Preserve pre-existing user-started services and active jobs. After testing or maintenance, finish active test jobs and stop every agent-started generator, queue API and preview; verify listeners are gone. Do not automatically restore Vox or another backend. Do not install login/autostart services or stop unrelated listeners.
