# VoiceGen quick start

Create a voice, record dialogue, and save the takes you like. This guide starts with **Higgs TTS 3**, the default speech model. Higgs needs a reference recording and its exact transcript; choose VoxCPM2, Qwen VoiceDesign, or MOSS if you want to design a voice from a description instead.

## 1. Install and set up

You need an Apple Silicon Mac, Git, and Make. You do not need to install specific Python versions first: setup reuses a compatible installed Python, or downloads one into the project's ignored `.python` folder when needed. It keeps separate environments for model dependencies and leaves your system Python and shell settings alone. An internet connection is needed for first-time downloads.

Higgs and MOSS also need Xcode command-line tools. If missing, run `xcode-select --install` and complete the installation before continuing.

Clone the project onto your Mac:

```sh
mkdir -p ~/Documents/models
cd ~/Documents/models
git clone https://github.com/ashwinmahesh/VoiceGen-Service.git
cd VoiceGen-Service
make setup
```

During setup:

1. Choose a speech model from the numbered menu. **Enter selects Higgs.**
2. On first setup, choose the folder for model files and recordings. Enter uses the project folder. To keep large files off your Mac's internal drive, enter an absolute SSD path, such as `/Volumes/ASH-SSD/models/VoiceGen`. The choice is remembered.
3. Wait for the environments and downloads to finish. Setup selects Python for each environment, installs the chosen speech model, queue dependencies, and **Qwen3.5-2B 4-bit**, the local LLM for prompt expansion. Existing compatible environments and verified model files are reused.

Keep the project and its Python environments on the internal drive. With external storage selected, model weights, large caches, reference recordings, and generated audio use that drive. All seven speech models share one SQLite database inside the project: `state/queue.sqlite3`.

If you want to choose Python yourself, use `BOOTSTRAP_PYTHON=/absolute/path/to/python3` for speech, `LLM_PYTHON=/absolute/path/to/python3` for prompt expansion, or `QUEUE_BOOTSTRAP_PYTHON=/absolute/path/to/python3` for the queue. Incompatible choices produce an explanation instead of silently selecting another interpreter. See [Python compatibility](BACKENDS.md#python-selection) for the supported versions. Setup preserves an incompatible existing environment; choose a different `VENV` for speech, or move the affected environment aside before rebuilding it.

On the existing installation on this Mac, the shared SSD folder is `/Volumes/ASH-SSD/models/VoxCPM2`. Keep that saved choice; there is no need to rename or move it. Changing `DATA_DIR` chooses storage and does not migrate existing files.

## 2. Open the studio

From the project folder:

```sh
make serve
```

Choose an installed model from the numbered list. Enter chooses Higgs when it is ready. Models still needing setup appear separately with installation instructions.

Startup asks whether to enable the local LLM. **Enter keeps it on.** Open **[http://localhost:5743](http://localhost:5743/)** and keep the terminal open while using the studio.

To choose explicitly or disable the LLM for this session:

```sh
make serve MODEL=higgs NO_LLM=1
```

To add another speech model, stop the current generator first, then run:

```sh
make setup MODEL=voxcpm2
make serve MODEL=voxcpm2
```

Other model IDs are `qwen3`, `chatterbox`, `indextts`, `moss`, and `fish`. Startup uses existing environments and files; it does not download Python, packages, or models. Each model has its own license, summarized at startup and in [the model guide](BACKENDS.md#provenance-and-licenses).

## 3. Generate your first dialogue in the UI

1. **Choose a voice.** For Higgs, upload a reference recording or select a saved voice. Uploads must be 1–30 seconds and at most 25 MiB. A clear 5–15 second sample is a useful starting point.
2. **Enter the exact reference transcript.** These are the words already spoken in the recording, separate from your new dialogue. Higgs and Fish require it.
3. **Enter the new dialogue.** Start with a short passage so you can judge the voice, accent, and delivery.
4. **Choose the performance settings.** The studio shows the selected model's available controls. Hover over a grey **?** icon for an explanation, focus it with the keyboard, or tap it on a phone. Expand **Advanced settings** for additional options.
5. **Try prompt expansion if available.** With the switch on, select **Preview expanded guidance**, review it, and apply it or keep your original. For supported batches of different voice designs, the LLM can refine each option automatically. Original and expanded guidance remain visible.
6. **Choose 1–5 options** and select **Generate speech**. The default is two. With Higgs or another recorded voice, these are alternative takes of the same speaker. Supported voice-design modes can explore different descriptions.
7. **Listen and save.** Download a take or choose **Save voice** to reuse it for more lines.

Prompt expansion uses the separate local Qwen3.5-2B LLM, with rules tailored to each speech model. It sees written voice and delivery directions, not your dialogue, reference transcript, or audio. Some modes do not support it. See [voice quality tips](VOICE_QUALITY.md) for choosing references and balancing expression against accent and voice consistency.

Open **Collection** for generated History and the saved voice library. Name, describe, tag, and search your voices there. **Upload voice** imports a recording directly into the library.

Open **Queue** in the header to monitor progress. Drag a left-side handle to reorder waiting jobs, or focus it and use the up/down arrow keys. **Run Now** moves a waiting job first, ahead of other immediate jobs; the current generation finishes before it starts. **Generate immediately**, checked by default in the UI, runs your request next after the active take, ahead of ordinary waiting jobs. It does not interrupt work already running. Pending jobs survive restarts and wait until their chosen model is running.

## 4. Generate through the API

Keep Higgs running and use another terminal. Check the service and available controls:

```sh
curl --fail-with-body http://localhost:5743/health
curl --fail-with-body http://localhost:5743/api/capabilities
```

### Upload a reusable voice

Replace the file path and transcript below with your actual recording and its exact words:

```sh
curl --fail-with-body http://localhost:5743/api/voices/upload \
  -F 'file=@/absolute/path/voice.wav' \
  -F 'name=My character' \
  -F 'description=A calm, cultured baritone.' \
  -F 'transcript=Exact words spoken in this recording.' \
  -F 'tags=character' -F 'tags=baritone'
```

The response contains an `id`. Copy it and replace `VOICE_ID_HERE` in the following requests. The voice is also available in Collection.

### Submit one take

```sh
curl --fail-with-body http://localhost:5743/api/generate \
  -H 'Content-Type: application/json' \
  -d '{
    "voice_id": "VOICE_ID_HERE",
    "text": "Welcome home. Sit, please.",
    "seed": 42,
    "generate_immediately": true,
    "idempotency_key": "quickstart-line-1"
  }'
```

HTTP 202 means the job was saved. Copy the returned job `id` and check it:

```sh
curl --fail-with-body http://localhost:5743/api/jobs/JOB_ID_HERE
```

Wait for `status` to become `done`. If it becomes `error`, read the `error` field. A finished job includes `result.audio_url` for playback and `result.id` for the output. Replace `OUTPUT_ID_HERE` with that output ID to download:

```sh
curl --fail-with-body \
  'http://localhost:5743/api/audio/outputs/OUTPUT_ID_HERE?download=true' \
  -o "$HOME/Downloads/voicegen-line.wav"
```

If a submission times out, retry the **same request with the same `idempotency_key`** to recover its job. Use a new key for a new line or take. Changing a request while keeping its key returns a conflict.

### Ask for multiple options

Use `/api/candidates` with the same generation fields and `candidate_count`:

```sh
curl --fail-with-body http://localhost:5743/api/candidates \
  -H 'Content-Type: application/json' \
  -d '{
    "voice_id": "VOICE_ID_HERE",
    "text": "Welcome home. Sit, please.",
    "candidate_count": 2,
    "generate_immediately": true,
    "idempotency_key": "quickstart-options-1"
  }'
```

Poll `/api/candidates/BATCH_ID_HERE` using the returned batch `id`. Its ordered `jobs` contain each take's status and result. Counts from 1–5 are supported; omitted count defaults to two.

### Preview expanded directions

With the LLM enabled, you can request a suggestion before submitting speech:

```sh
curl --fail-with-body http://localhost:5743/api/prompts/expand \
  -H 'Content-Type: application/json' \
  -d '{
    "generation": {
      "voice_id": "VOICE_ID_HERE",
      "text": "Welcome home. Sit, please."
    },
    "guidance": "Quietly amused, with restrained emphasis."
  }'
```

This creates a preview, not audio. Review its `updates`. To apply it, merge those fields into the same generation request and add `prompt_expansion_id` using the preview's returned `id`. Submit that request to `/api/generate` or `/api/candidates`. See the [complete application example](API.md#voice-guidance-preview-and-application). An enabled LLM does not automatically rewrite single-take API requests.

## 5. Queue work while the studio is off

The separate queue API can accept requests without loading a speech model. It normally remains running after you close the generator. If needed, start it in another terminal:

```sh
make queue
```

List supported models and the default:

```sh
curl --fail-with-body http://localhost:55205/api/models
```

Add a single take using the saved voice from above:

```sh
curl --fail-with-body http://localhost:55205/api/queue \
  -H 'Content-Type: application/json' \
  -d '{
    "idempotency_key": "quickstart-queued-1",
    "generate_immediately": false,
    "payload": {
      "voice_id": "VOICE_ID_HERE",
      "text": "Welcome home."
    }
  }'
```

Omitting `model` selects the project default, **Higgs**, regardless of which model ran last. Add a top-level `"model": "voxcpm2"`, for example, to choose a different one, with a payload suited to that model. Keep the retry key and priority outside `payload`.

List work with `GET http://localhost:55205/api/queue`; inspect an entry with `/api/queue/JOB_ID_HERE`. Requests are saved immediately and validated for speech when the matching generator starts. Run `make serve MODEL=higgs` to process the Higgs jobs. The queue does not switch models automatically.

Both services use the **same SQLite database**. The UI calls the generator's APIs, which read and write SQLite directly, so a separate queue API outage does not prevent the studio from working. See [the API guide](API.md) for listing, removing, clearing, and retrying work.

## Settings you can change

### When starting or setting up

| Setting | What it changes |
| --- | --- |
| `MODEL=higgs` | Speech model to install or serve. Omit it for the interactive menu. |
| `NO_LLM=1` | Disable prompt expansion for this session. Otherwise it is enabled by default. |
| `DATA_DIR=/absolute/folder` | Model and media storage, chosen during setup and remembered. Does not move existing data. |
| `OUTPUT_DIR=/absolute/folder` | Destination for new generated audio. |
| `PORT=5743` | Generator and UI port. |
| `QUEUE_PORT=55205` | Separate queue API port. |
| `PROMPT_LLM_DIR=/absolute/folder` | Reuse the expected prompt LLM weights from another folder. |

Pass settings after the Make target, for example `make serve MODEL=higgs PORT=5743`. Keep the same port overrides for later status and stop commands. Existing installations can add the LLM with `make setup-llm`.

### For each generation

| Setting | Where and how to use it |
| --- | --- |
| Voice and dialogue | Select a saved voice, recording, supported preset, or description; enter the words separately. |
| Performance | Use the selected model's controls. API options specific to a model go under its ID, such as `"higgs": {"emotion": "amusement"}`. Available options come from `/api/capabilities`. |
| Number of options | UI selector or `candidate_count` on `/api/candidates`; 1–5, default two. |
| Variation | `candidate_variation: "auto"` contrasts descriptions in supported design modes and keeps speaker conditioning for recorded voices. `"seed_only"` keeps one description. |
| Prompt expansion | UI switch and preview/apply; API preview through `/api/prompts/expand`. `candidate_use_llm: false` disables automatic LLM planning for a design batch. |
| Seed | Selects a take's random variation; does not guarantee the same voice across different dialogue. |
| Sentence/comma pauses | Advanced settings for supported models. API fields include `sentence_pause_ms` and `comma_pause_ms` under the model ID. MOSS timing is approximate; Higgs and Vox do not offer these fields. |
| Run next | New requests: **Generate immediately** or `generate_immediately: true` (API default false). Existing jobs: Queue **Run Now** or `POST /api/jobs/{id}/run-now`. |
| Queue order | Drag a handle, use its arrow keys, or call `POST /api/jobs/{id}/move`. Only waiting jobs move. |

Use **Adjust pauses** on a completed take to extend quiet gaps while keeping the original. For all model-specific ranges and restrictions, see [API.md](API.md).

## Stop and resume

Press **Ctrl-C** in the terminal running `make serve`. The current take finishes, waiting jobs remain saved, and the separate queue API stays available. Start again with the same model to continue its queue.

To stop the queue API too:

```sh
make stop-queue
```

For background operation, use `make start` and `make stop`, with the same `MODEL` and port settings. The managed generator must be idle before `make stop` will stop it. `make status` and `make queue-status` show service state.

If the SSD is disconnected, reconnect it before generating. If the studio reports queue storage unavailable, restore access or free space in the project's `state/` folder, then retry with the same submission key. Check Queue before submitting again after a timeout.

Return to the [project overview](README.md), or read the [API guide](API.md), [model guide](BACKENDS.md), and [voice quality tips](VOICE_QUALITY.md).
