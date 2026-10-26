# VoiceGen Service

VoiceGen is a local voice studio for creating character voices and spoken dialogue on an Apple Silicon Mac. Describe a new voice or start with a recording, direct its performance, and compare takes before choosing one for your game or other project.

The studio supports **seven speech models**, with **Higgs TTS 3 as the default**. It includes a searchable voice Collection, audio downloads, and a saved queue so unfinished work can continue after a restart. You can use everything through the browser or an API.

**[Follow the quick start](QUICKSTART.md)** for installation, your first recording, API examples, and settings. If you have already set up the project, run `make serve` and open **[localhost:5743](http://localhost:5743/)**.

## Inside the studio

**Generate:** choose or design a voice, enter dialogue, direct the performance, and audition the results.

![Full Generate page with voice controls and multiple listening options](docs/images/studio-page.jpg)

**Collection:** browse previous takes or save and upload voices with names, descriptions, and searchable tags.

![Full Collection page with a saved voice, search, and tags](docs/images/collection-page.jpg)

**Queue:** drag waiting jobs into order, choose **Run Now** to run one next, monitor progress, or remove pending work. The studio's default “Generate immediately” option runs next after the current take, ahead of ordinary waiting jobs.

![Full page with the Queue modal open](docs/images/queue-modal.jpg)

Screenshots use demo data. Model files and recordings can live on an external SSD. Source code, Python environments, and one shared SQLite queue stay together in the project folder on your Mac. See [storage setup](QUICKSTART.md#1-install-and-set-up).

## Getting better voices and performances

VoiceGen brings several techniques into one place:

- **Start with the right recording.** For models that reuse a voice, a clear sample with the intended accent and delivery gives them a stronger starting point. Higgs and Fish also use the exact words spoken in that sample.
- **Use each model's acting controls.** These include delivery instructions, emotion recordings, expression settings, and supported performance tags. The studio shows the controls available for the selected model.
- **Refine written directions with a local LLM.** Prompt expansion can turn a voice description or acting direction into guidance suited to the chosen speech model. You can review the suggestion before applying it.
- **Compare options.** Generate 1–5 options at once, with two selected by default. Voice-design models can explore contrasting descriptions; a saved or recorded voice produces alternative takes of the same speaker.
- **Adjust the pacing.** Supported models offer sentence and comma pause settings. You can also extend quiet gaps in a completed take and save the edit separately from the original.

More expression can change the voice or weaken an accent. Listen for all three—delivery, accent, and voice identity—when comparing takes. [Voice quality tips](VOICE_QUALITY.md) explain the recommended starting settings and model limitations.

## Local LLM for prompt expansion

The optional prompt expander uses **Qwen3.5-2B, a 4-bit local language model**. It helps write voice and performance directions; the selected speech model creates the audio. It is separate from the Qwen3-TTS speech model listed below.

`make setup` downloads or reuses the LLM along with your chosen speech model. `make serve` enables it by default. The UI's **Prompt expansion** switch lets you opt out for a request; start with `make serve NO_LLM=1` to disable it for the session.

Expansion follows different rules for each supported model and mode. The studio shows the original and expanded guidance. The LLM receives written voice and delivery directions, while dialogue, reference transcripts, and recordings stay out of its input. Suggestions still need listening checks; extra wording does not always produce a better performance.

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

Choose an installed model at startup. Your Collection and queue are shared across models. Some features vary by model, and Chatterbox and IndexTTS are experimental on 24 GB Macs. See [model requirements and licenses](BACKENDS.md) before choosing one; the studio also displays relevant license notices.

## API overview

<details>
<summary>Available API endpoints</summary>

Here are the available endpoints. See [API.md](API.md) for request formats, examples, and errors. Both services also offer interactive documentation at `/docs` and the full schema at `/openapi.json` while running.

### Generator — localhost:5743

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/health` | Check the generator and queue status. |
| GET | `/api/capabilities` | See the selected model's features and controls. |
| POST | `/api/generate` | Generate one take. |
| POST | `/api/candidates` | Generate 1–5 voice options. |
| GET | `/api/candidates/{id}` | Check a batch and its results. |
| POST | `/api/prompts/expand` | Preview expanded voice or delivery directions. |
| GET | `/api/jobs` | List jobs. |
| GET | `/api/jobs/{id}` | Check a job and its result. |
| DELETE | `/api/jobs/{id}` | Remove a waiting job. |
| DELETE | `/api/jobs` | Clear waiting jobs. |
| POST | `/api/jobs/{id}/retry` | Retry a failed job. |
| POST | `/api/jobs/{id}/move` | Move a waiting job up/down or beside another. |
| POST | `/api/jobs/{id}/run-now` | Put a waiting job first. |
| GET | `/api/history` | Browse generated takes. |
| GET | `/api/artifacts/{id}` | Get a take's details. |
| GET | `/api/metadata/{id}` | Download a take's saved metadata. |
| GET | `/api/audio/{kind}/{id}` | Play or download audio. |
| POST | `/api/artifacts/{id}/pauses` | Save a version with adjusted pauses. |
| GET | `/api/voices` | Search the voice library. |
| GET | `/api/voices/{id}` | Get a saved voice. |
| POST | `/api/voices` | Save a voice to the library. |
| PATCH | `/api/voices/{id}` | Edit a voice's name, notes, tags, or transcript. |
| POST | `/api/voices/upload` | Upload a recording directly to the library. |
| GET | `/api/voice-tags` | List voice-library tags. |
| POST | `/api/references` | Upload a reference recording. |
| GET | `/api/references/{id}/original` | Download the original reference file. |
| POST | `/api/model/unload` | Release the model's memory. |

On the generator, `/api/queue` is an alias for the `/api/jobs` routes above. `POST /api/jobs` and `POST /api/queue` are also aliases for `/api/generate`.

### Queue service — localhost:55205

This service can accept jobs while the generator is off. Its queue endpoints use a different request format from the generator; see the [queue API guide](API.md#standalone-queue-api-port-55205).

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/health` | Check the queue service. |
| GET | `/api/models` | List supported models and the default. |
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

For development and automated operation, see [AGENTS.md](AGENTS.md) and the [service skill](skill/local-voice-studio/SKILL.md).
