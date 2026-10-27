# VoiceGen Service

VoiceGen is a local voice studio for creating character voices and spoken dialogue on an Apple Silicon Mac. Describe a new voice or start with a recording, direct its performance, and compare takes before choosing one for your game or other project.

The studio supports **seven speech models**, with **Higgs TTS 3 as the default**. It includes a searchable voice Collection, audio downloads, and a saved queue so unfinished work can continue after a restart. You can use everything through the **Voice Studios** browser app or an API.

**[Follow the quick start](QUICKSTART.md)** for installation, your first recording, API examples, and settings. If you have already set up the project, run `make serve` and open **[localhost:5743](http://localhost:5743/)**.

## Inside the studio

Studio, Collection, and Observability share the same navigation, Queue button, service status, and memory-release control.

Click the Voice Studios icon, title, or subtitle in the header to return to Studio.

Screenshots use benign demonstration records and fake inference; displayed timings are not model benchmarks.

**Generate:** choose or design a voice, enter dialogue, direct the performance, and audition the results.

Use **Design a voice** to create a character, or **Record lines** to reuse one. Record lines has a **Voice source** menu for saved voices, uploaded reference audio, and built-in speakers when available. Browse voice catalog opens your Collection in a new tab so your recording form stays open.

An elapsed timer below Generate and in the Listening room updates every second through waiting and generation, then keeps the final duration. Reference transcripts stay with saved voices; you can save an edit directly or let Generate save it before submitting. Higgs has searchable Emotion, Prosody, and Style inputs. Choose an option, or type a custom direction and preview a supported control with the local LLM. Accent comes from the reference recording.

Higgs starts new UI settings with a 2,000-token audio limit. If one option cannot finish within its limit, the others stay available; the failed card can prepare a single replacement with a higher limit. Advanced settings also offer native text chunk size for longer passages.

**Design a voice** stays visible when the current model needs a recording, such as Higgs. Open it and choose a set-up VoxCPM2, Qwen3-TTS, or MOSS VoiceGenerator from the model menu. If none is set up, the tab stays disabled and its tooltip lists those alternatives.

![Full Generate page with Higgs performance controls and audio playback](docs/images/studio-page.jpg)

**Collection:** browse previous takes or save and upload voices with names, descriptions, and searchable tags.

Voice library appears first and opens by default; History holds your generated takes. Both offer **Delete** with a confirmation popup. Use **Show deleted → Restore** to bring an entry back. Deletion removes it from the active collection while retaining its audio for queued jobs and existing recordings; it does not free disk space.

[View the deletion confirmation](docs/images/collection-delete-confirmation.jpg).

![Full Collection page with a saved voice, search, and tags](docs/images/collection-page.jpg)

**Queue:** drag waiting jobs into order, choose **Run Now** to run one next, monitor progress, or remove pending work. The studio's default **Generate immediately** switch runs next after the current take and keeps Generate busy until your options finish. Turn it off to keep adding ordinary queued jobs.

![Full page with the Queue modal open](docs/images/queue-modal.jpg)

**Observability:** chart CPU, memory, and GPU usage over time, filter by model, or inspect a job's resource use. Choose windows from five minutes to seven days. History is saved automatically while the generator runs; GPU figures cover the whole Mac. Each job view also shows sampled stages and any retained generation log.

![Full Observability page with demonstration resource history](docs/images/observability-page.jpg)

Screenshots use demo data. Model files and recordings can live on an external SSD. Source code, Python environments, and one shared SQLite queue stay together in the project folder on your Mac. See [storage setup](QUICKSTART.md#1-install-and-set-up).

Setup automatically reuses a compatible installed Python, or downloads one into the project when needed. You do not need to install Python 3.11 and 3.14 yourself. Starting the studio uses the environments already set up and does not download anything. `make serve` exits with setup instructions unless at least one model has its verified files and compatible runtime ready.

You can safely rerun `make setup`: verified model downloads are reused, while dependencies and support files are checked and updated in place as needed. Choose **q** in the model menu or press **Ctrl+C** during setup to cancel; existing downloads, voices, recordings, and queued jobs are kept.

## Getting better voices and performances

VoiceGen brings several techniques into one place:

- **Start with the right recording.** For models that reuse a voice, a clear sample with the intended accent and delivery gives them a stronger starting point. Higgs and Fish also use the exact words spoken in that sample.
- **Use each model's acting controls.** These include delivery instructions, emotion recordings, expression settings, and supported performance tags. The studio shows the controls available for the selected model.
- **Refine written directions with a local LLM.** Prompt expansion can turn a voice description or acting direction into guidance suited to the chosen speech model. You can review the suggestion before applying it.
- **Compare options.** Generate 1–5 options at once, with two selected by default and a fresh random seed for every new Generate click. Voice-design models can explore contrasting descriptions; a saved or recorded voice produces alternative takes of the same speaker. Advanced settings also allow a fixed seed.
- **Adjust the pacing.** Supported models offer sentence and comma pause settings. You can also extend quiet gaps in a completed take and save the edit separately from the original.

More expression can change the voice or weaken an accent. Listen for all three—delivery, accent, and voice identity—when comparing takes. [Voice quality tips](VOICE_QUALITY.md) explain the recommended starting settings and model limitations.

## Local LLM for prompt expansion

The optional prompt expander uses **Qwen3.5-2B, a 4-bit local language model**. It helps write voice and performance directions; the selected speech model creates the audio. It is separate from the Qwen3-TTS speech model listed below.

`make setup` downloads or reuses the LLM along with your chosen speech model. `make serve` enables it by default. The UI's **Prompt expansion** switch lets you opt out for a request; start with `make serve NO_LLM=1` to disable it for the session.

Expansion follows different rules for each supported model and mode. The studio shows the original and expanded guidance. The LLM receives written voice and delivery directions, while dialogue, reference transcripts, and recordings stay out of its input. Suggestions still need listening checks; extra wording does not always produce a better performance.

With a saved or uploaded voice in **Vox Auto**, enter an Emotion & delivery direction to preview its expansion. Auto then uses flexible voice conditioning. With no new directions and an exact transcript, Auto follows the recorded performance; the preview explains that choice. Disabled fields show why they are unavailable and how to enable a supported alternative.

For **Higgs**, enter a short acting direction and select **Suggest delivery control**. The LLM chooses one supported Emotion, Prosody, or Style setting; it does not write a longer prose prompt. For example, “more expressive intonation” can suggest `expressive_high`. The preview shows what you typed and makes **No change suggested** explicit. Accent comes from the reference recording, and can still drift.

## Speech models

| Model | Main use in this studio |
| --- | --- |
| **Higgs TTS 3 — default** | Recorded voices with exact transcripts, plus optional acting controls. |
| VoxCPM2 | Voice design, recorded voices, and recorded-performance reuse. |
| Qwen3-TTS | Voice design, recorded voices, or built-in speakers. |
| Chatterbox Original | Recorded voices with adjustable expression. |
| IndexTTS 2.5 | Recorded voices with emotion and timing controls. |
| MOSS VoiceGenerator | Voice design from descriptions. |
| Fish Audio S2 Pro | Recorded voices with exact transcripts and delivery tags. |

Startup chooses the saved default. The studio’s Voice model selector can use any set-up model for new requests. One generator switches models between jobs, and your Collection and queue are shared. Some features vary by model, and Chatterbox and IndexTTS are experimental on 24 GB Macs. See [model requirements and licenses](BACKENDS.md) before choosing one; the studio also displays relevant license notices.

## API overview

<details>
<summary>Available API endpoints</summary>

Here are the available endpoints. See [API.md](API.md) for request formats, examples, and errors. Both services also offer interactive documentation at `/docs` and the full schema at `/openapi.json` while running.

### Generator — localhost:5743

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/health` | Check the generator and queue status. |
| GET | `/v1/models` | List set-up models and the saved default. |
| GET | `/api/capabilities?model=higgs` | See a model's features and controls. |
| GET | `/api/observability` | Chart resource history by time window and model. |
| GET | `/api/observability/jobs` | List recorded job attempts and their resource peaks. |
| GET | `/api/observability/jobs/{id}` | Inspect resource charts for one job or attempt. |
| GET | `/api/jobs/{id}/logs` | Read available generation output for a job. |
| POST | `/api/generate` | Generate one take. |
| POST | `/api/candidates` | Generate 1–5 voice options. |
| GET | `/api/candidates/{id}` | Check a batch and its results. |
| POST | `/api/prompts/expand` | Preview expanded voice or delivery directions. |
| GET | `/api/queue` | List jobs across models. |
| GET | `/api/jobs/{id}` | Check a job and its result. |
| DELETE | `/api/queue/{id}` | Remove a waiting job. |
| DELETE | `/api/queue` | Clear waiting jobs. |
| POST | `/api/jobs/{id}/retry` | Retry a failed job. |
| POST | `/api/queue/{id}/move` | Move a waiting job up/down or beside another. |
| POST | `/api/queue/{id}/run-now` | Put a waiting job first. |
| GET | `/api/history` | Browse generated takes. |
| GET | `/api/artifacts/{id}` | Get a take's details. |
| DELETE | `/api/artifacts/{id}` | Move a take to deleted items. |
| POST | `/api/artifacts/{id}/restore` | Restore a deleted take. |
| GET | `/api/metadata/{id}` | Download a take's saved metadata. |
| GET | `/api/audio/{kind}/{id}` | Play or download audio. |
| POST | `/api/artifacts/{id}/pauses` | Save a version with adjusted pauses. |
| GET | `/api/voices` | Search the voice library. |
| GET | `/api/voices/{id}` | Get a saved voice. |
| POST | `/api/voices` | Save a voice to the library. |
| PATCH | `/api/voices/{id}` | Edit a voice's name, notes, tags, or transcript. |
| DELETE | `/api/voices/{id}` | Move a voice to deleted items. |
| POST | `/api/voices/{id}/restore` | Restore a deleted voice. |
| POST | `/api/voices/upload` | Upload a recording directly to the library. |
| GET | `/api/voice-tags` | List voice-library tags. |
| POST | `/api/references` | Upload a reference recording. |
| GET | `/api/references/{id}/original` | Download the original reference file. |
| POST | `/api/model/unload` | Release the model's memory. |

Both APIs also provide `POST /api/queue/pause` and `/api/queue/resume`. Run Now waits for the active batch, then runs ahead of normal work.

### Queue service — localhost:55205

This service can accept jobs while the generator is off. Its queue endpoints use a different request format from the generator; see the [queue API guide](API.md#standalone-queue-api-port-55205).

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/health` | Check the queue service. |
| GET | `/v1/models` | List set-up models and the saved default. |
| POST | `/api/queue` | Add a job. |
| GET | `/api/queue` | List jobs. |
| GET | `/api/queue/{id}` | Get a job's details and result. |
| PATCH | `/api/queue/{id}` | Update a waiting entry. |
| POST | `/api/queue/{id}/move` | Reorder a waiting entry. |
| POST | `/api/queue/{id}/run-now` | Put a waiting entry first. |
| DELETE | `/api/queue/{id}` | Remove a waiting entry. |
| DELETE | `/api/queue` | Clear waiting entries. |

</details>

## Guides

- [Quick start](QUICKSTART.md): setup, UI and API walkthroughs, settings, and stopping the services.
- [API guide](API.md): complete request formats and examples.
- [Model details](BACKENDS.md): requirements, downloads, features, and licenses.
- [Voice quality tips](VOICE_QUALITY.md): references, accents, expression, and pacing.
- [Prompt expansion details](docs/voice-prompt-expansion.md): model-specific rules and evaluation.
- [Resource history](docs/observability.md): charts, measured values, job attempts, and seven-day retention.

For development and automated operation, see [AGENTS.md](AGENTS.md) and the [service skill](skill/local-voice-studio/SKILL.md).
