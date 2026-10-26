# VoiceGen Service

A local voice studio for creating character voices, directing dialogue, and building a reusable voice library. It runs on an Apple Silicon Mac and supports seven voice models through the same interface.

Generate audio on your Mac, compare takes, and save voices with names, descriptions, and tags. **Higgs TTS 3 is the default model.**

## Start the studio

If setup is already complete, run this from the project folder:

```sh
make serve
```

Choose a model from the numbered menu, then open **[localhost:5743](http://localhost:5743/)**. Press Enter to choose Higgs when it is available. Models that still need setup appear separately with the command to install them.

Startup also asks whether to enable the local prompt assistant. Enter keeps it on. To start without it:

```sh
make serve NO_LLM=1
```

Keep the terminal open while you use the studio. [First time here? Set up the project below.](#first-time-setup)

## Choose a model

| Model | What you can do with it |
| --- | --- |
| **Higgs TTS 3 — default** | Generate dialogue from a reference voice and its exact transcript, with optional delivery controls. |
| VoxCPM2 | Design a voice from a description, or reuse a recorded voice and performance. |
| Qwen3-TTS | Design a voice, clone a recording, or use an expressive built-in speaker. |
| Chatterbox Original | Clone a recorded voice and adjust exaggeration and guidance. |
| IndexTTS 2.5 | Clone a voice and guide emotion with another recording or emotion controls. |
| MOSS VoiceGenerator | Create voices from descriptions; it does not clone recordings. |
| Fish Audio S2 Pro | Clone a reference voice with its exact transcript and add delivery tags. |

To install another model, then use it:

```sh
make setup MODEL=voxcpm2
make serve MODEL=voxcpm2
```

Other model IDs are `higgs`, `qwen3`, `chatterbox`, `indextts`, `moss`, and `fish`. Stop the current generator before switching models. Your Collection is shared across models, although each model supports different voice and delivery features.

Chatterbox and IndexTTS are experimental on 24 GB Macs and can be slow or use substantial memory. See [model details](BACKENDS.md) for requirements and limitations.

Each model has its own license. The studio shows the relevant notices at startup and in the UI; see the [license table](BACKENDS.md#provenance-and-licenses) for the full details.

## Generate dialogue

1. **Choose a voice source.** Select a saved voice, upload a reference recording, or use **Design a voice** when the selected model supports it.
2. **Add the reference transcript if needed.** Higgs and Fish require the exact words spoken in the reference recording. This is separate from the new line you want to generate.
3. **Enter your dialogue and delivery.** Available emotion, accent, and pacing controls depend on the model. Look under **Advanced settings** for more options.
4. **Choose 1–5 options** and select **Generate speech**. The default is two. Reference voices produce alternative takes of the same speaker; supported voice-design models can explore different designs.
5. **Listen and keep the take you like.** Download the audio, or choose **Save voice** to reuse that voice for more lines.

A clear reference with the accent and performance you want is a useful starting point. Short passages make it easier to compare delivery. Listen to each result: a stronger emotion setting can also change the accent or voice identity.

![Full Generate page showing voice controls and multiple listening options](docs/images/studio-page.jpg)

### Prompt expansion and pauses

The **Prompt expansion** switch lets the local assistant expand written voice or delivery directions. Select **Preview expanded guidance**, review the suggestion, then choose **Apply guidance** or **Keep original**. Original and expanded instructions stay visible. The assistant does not receive your dialogue, reference transcript, or recording.

With multiple voice designs, expansion can suggest different directions for each option. Turning it off uses the built-in contrast directions instead. Saved and reference voices remain alternative takes of the selected speaker.

Some models offer sentence and comma pause controls under **Advanced settings**. You can also use **Adjust pauses** on a completed take: listen, mark a quiet gap, and extend it. The edited version is saved separately, keeping the original.

For more help with accents and acting, see [voice quality tips](VOICE_QUALITY.md).

## Save and find voices

Open **Collection** to browse:

- **History:** generated takes, with playback and options to reuse their settings.
- **Voice library:** saved voices you can name, describe, tag, search, and use for new dialogue.

Generated takes enter History automatically. Add a voice to the library by choosing **Save voice**, or use **Upload voice** in Collection to import a recording directly.

Uploads can be **1–30 seconds**, up to **25 MB**. A clean 5–15 second sample is a good starting point. Add its exact transcript when using a model that requires one.

![Full Collection page showing a saved voice, search, and tags](docs/images/collection-page.jpg)

## Queue work and come back later

Open **Queue** in the header to monitor jobs, remove a waiting entry, clear pending work, or retry a failed take. Removing pending work keeps completed recordings and leaves active generation running.

**Generate immediately** is checked by default in the studio. It puts your request ahead of ordinary waiting work, after the current generation finishes. Turn it off to join the normal queue.

Jobs survive restarts. Each remembers its model and waits until that model is running. Stopping the generator finishes the current take and leaves the remaining jobs for later.

![Full page with the Queue modal open](docs/images/queue-modal.jpg)

### Stop the studio

Press **Ctrl-C** in the terminal running `make serve`. The separate queue service stays available so scripts can still add work. To stop that too:

```sh
make stop-queue
```

If you prefer to run the generator in the background, use `make start` and later `make stop`. When using a model other than the default, include the same `MODEL=...` when starting and stopping it. A managed generator must be idle before `make stop` will stop it.

## First-time setup

You need **Apple Silicon macOS**, Git, Make, and the appropriate Python versions. Higgs and the other native/PyTorch models use Python 3.11. Vox, Qwen, Fish, and the local prompt assistant use Python 3.14. Higgs and MOSS also need Xcode command-line tools to build their runtime.

If you use Homebrew, install Python with:

```sh
brew install python@3.11 python@3.14
```

If Xcode command-line tools are not installed, run `xcode-select --install` and complete the installation. Then:

```sh
mkdir -p ~/Documents/models
cd ~/Documents/models
git clone https://github.com/ashwinmahesh/VoiceGen-Service.git
cd VoiceGen-Service
make setup
make serve
```

Setup asks which model to install and where to store model files and recordings. **Enter selects Higgs.** At the storage prompt, **Enter uses the project folder**; choose an absolute folder on your external SSD if you want to keep large files there. Your choice is remembered for later runs.

First setup downloads several gigabytes and may take a while. It installs the selected model, the queue service dependencies, and the local prompt assistant. Existing verified model files are reused. Starting the studio does not download models automatically.

### Where files live

Keep the project and its Python environments on your Mac's internal drive. Model files, caches, reference recordings, and generated audio can live on your external SSD.

There is **one shared SQLite queue** for all models, stored inside the project at `state/queue.sqlite3`. It stays with the project even when models and recordings are external.

On this Mac, the project is at `~/Documents/models/VoiceGen-Service`. The existing shared SSD data folder is still named `/Volumes/ASH-SSD/models/VoxCPM2`; that name does not limit which model you can use.

## Troubleshooting

| What you see | What to do |
| --- | --- |
| A model is missing from the startup choices | Run `make setup MODEL=<model-id>`, then start the studio again. |
| The external drive is unavailable | Reconnect it before starting or generating. Your saved storage choice is retained. |
| A queue API warning appears | Run `make queue` in another terminal. The studio can still process jobs while the shared database is accessible. The warning clears when the queue API returns. |
| Queue storage is unavailable | Check free space and access to the project's `state/` folder. New generation is disabled until storage is available again. |
| Generation seems to have timed out | Check Queue before submitting again; an accepted job may still be running. |
| The voice loses its accent or sounds different | Try a clearer reference, check its transcript, and compare one delivery change at a time. See [voice quality tips](VOICE_QUALITY.md). |

`make status` shows generator state; `make queue-status` checks the queue service. If you changed ports, use the same `PORT` or `QUEUE_PORT` value with later commands.

## API overview

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

This service can accept jobs while the generator is off. Its queue endpoints use a different request format from the generator; see the [queue API guide](API.md#standalone-queue-crud-api-port-55205).

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/health` | Check the queue service. |
| GET | `/api/models` | List supported models and the default. |
| POST | `/api/queue` | Add a job. |
| GET | `/api/queue` | List jobs. |
| GET | `/api/queue/{id}` | Get a job's details and result. |
| PATCH | `/api/queue/{id}` | Update a waiting entry. |
| DELETE | `/api/queue/{id}` | Remove a waiting entry. |
| DELETE | `/api/queue` | Clear waiting entries. |

## More documentation

- [API guide](API.md): request examples, queue operations, model controls, and errors.
- [Model details](BACKENDS.md): environments, downloads, licenses, and supported features.
- [Prompt assistant guide](docs/voice-prompt-expansion.md): configuration and model-specific behavior.
- [Development instructions](AGENTS.md) and [agent skill](skill/local-voice-studio/SKILL.md): maintaining the project and operating it from an AI tool.

Run `make test` for automated checks that use simulated generation. These do not measure real voice quality; use the studio to audition actual output.
