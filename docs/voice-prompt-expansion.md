# Voice guidance expansion: policy and evidence

Research with Astra, implementation and local validation: 2026-10-25. This is a preview tool for written performance directions. No evidence here establishes that LLM expansion universally improves speech, expressiveness or accent preservation.

## Backend-specific boundaries

The same natural-language prompt cannot be sent to every speech adapter. The installed routing, in addition to upstream model support, determines what can have an effect.

| Backend / mode | Preview output | Boundary |
| --- | --- | --- |
| VoxCPM2 design | Description addition | Audible traits/delivery; preserve the original and explicit accent |
| Vox flexible reference | Delivery addition in `emotion` | Preserve reference identity; no switch to combined/continuation |
| Vox combined/continuation | Unavailable | Recorded performance controls delivery; resolve auto first |
| Qwen VoiceDesign | Description addition | Designs a voice; matching descriptions/seeds do not lock identity |
| Qwen CustomVoice | Delivery addition | Keep the selected preset fixed |
| Qwen Base reference | Unavailable | No installed free-text acting instruction path |
| Chatterbox Original | Unavailable | Uses reference plus numeric controls; Turbo tags do not apply |
| IndexTTS current adapter | Unavailable | Installed text-to-emotion path is disabled; no uncalibrated vector mapping |
| Fish S2 Pro | One or two short delivery cues | Existing typed tags, no new speech/events/accent tokens |
| Higgs T3 | At most one existing enum control | Category-specific catalog validation; keep untagged baseline |
| MOSS VoiceGenerator | Description addition | Design only; not persistent speaker cloning |

The [official Vox project](https://github.com/OpenBMB/VoxCPM) distinguishes design, controllable reference and Ultimate cloning; a [contributor response](https://github.com/OpenBMB/VoxCPM/issues/210) confirms style is unavailable for Ultimate cloning. Adding instructions before resolving auto could silently change conditioning, so this service resolves the original request first.

[Qwen's official usage](https://github.com/QwenLM/Qwen3-TTS#python-package-usage) separates VoiceDesign, CustomVoice instructions and Base audio/transcript conditioning. [Chatterbox Original's guidance](https://github.com/resemble-ai/chatterbox#original-chatterbox-tips) concerns exaggeration/CFG rather than prose acting instructions. [Index's upstream text-to-emotion option](https://github.com/index-tts/index-tts#5-emotion-control-from-the-text-itself-use_emo_text) exists but requires enabling its classifier; this service does not enable it for this feature.

[Fish S2 Pro](https://huggingface.co/fishaudio/s2-pro#fine-grained-inline-control) accepts natural-language bracketed controls. The two-cue maximum here is conservative application policy, not a claim that every cue works reliably. [Higgs's pinned prompting catalog](https://huggingface.co/bosonai/higgs-tts-3-4b/blob/239f63fb7b02b1aa085f98d9efae5e35cc5523e8/PROMPTING.md) defines its vocabulary; unknown tags may degrade output or be read literally. A [firsthand speaker-drift report](https://huggingface.co/bosonai/higgs-tts-3-4b/discussions/20) is a reason to compare tagged and untagged takes, not population-level evidence. [MOSS VoiceGenerator](https://huggingface.co/OpenMOSS-Team/MOSS-VoiceGenerator#input-types) accepts separate text/instructions and supports English/Chinese; other MOSS family members' cloning support is not this model's capability.

## Application policy

`prompt_policy.py` separates shared fidelity instructions from trusted backend profiles. `voice-guidance-4` uses compact schemas and distinct examples for prose, Fish and Higgs; Chinese prose gets Chinese examples. Profiles are versioned and recorded with each preview. Temperature0 and disabled thinking are reproducibility choices, not a claim of optimal Qwen sampling.

The application preserves spoken `text` and exact `ref_text` verbatim and excludes both from the LLM payload. No audio, paths or speaker/reference IDs enter that payload. The model sees only written description/delivery/accent constraints. It cannot inspect a reference recording, infer its accent, select a speaker, change a clone mode or modify sampler/pause settings.

Prose keeps the original intact and appends at most600 characters/60 whitespace-separated words. Already precise guidance may return an empty addition. Fish suggestions use existing typed controls and the normal renderer; Higgs suggestions choose one category/value from the existing catalog or no suggestion. Existing manual/structured tags conflict rather than being overwritten.

Full JSON parsing rejects unknown/duplicate keys, invalid types, overlong output, markup/control characters and wrong catalog categories. There is no blind JSON repair or truncation. The service rebuilds the request and runs its ordinary validation. A syntactically valid suggestion can still introduce an unwanted nuance, repeat details or weaken an accent instruction. Prompt instructions and JSON encoding are not a semantic security guarantee; [primary prompt-injection research](https://arxiv.org/abs/2302.12173) explains that distinction. Review then apply, keep original, and restore original remain explicit UI actions.

## Runtime and durability

The auxiliary model is [Qwen3.5-2B](https://huggingface.co/Qwen/Qwen3.5-2B#best-practices), using the [MLX 4-bit artifact](https://huggingface.co/mlx-community/Qwen3.5-2B-4bit) at revision `674aaa7240b91e8012fcad5d791b7dfe5ba90207`. The1,722,271,785-byte safetensors SHA256 is `713fe7e5d3c3965f7106b0d0ee17615f7869c23c8d327996df8c1196fbcf07d5`. Setup reuses the shared SSD weights after verification. [MLX-LM](https://github.com/ml-explore/mlx-lm)0.32.2 and its dependencies live in an isolated project environment, preserving speech locks.

Preview acquires the normal shared GPU/queue/data reservations, unloads resident speech weights and runs an offline text-only child. Maximum completion320 tokens, wall timeout180 seconds, one attempt; hitting a limit fails visibly. Native descendants inherit leases, and client disconnects do not free capacity prematurely. Serving never downloads. Busy previews return409 and create no speech job.

Applied receipts persist original/expanded guidance, model/revision, profile/policy/runtime, context, timing and overlapping memory counters. Queue admission embeds the receipt before candidate fan-out. Restart/retry/each candidate uses frozen applied guidance with no LLM invocation; an already applied receipt works with the auxiliary capability disabled. No automatic per-generation expansion is implemented.

## What was measured

The final local text-only smoke used seven cases: short Vox design, explicit Romanian-accent constraints, an embedded instruction-overriding request, Qwen preset delivery, Fish tags, Higgs catalog selection and Chinese MOSS design. All seven returned structurally valid previews under `voice-guidance-4`. Latency including the short-lived child was1.54–2.01 seconds; reported MLX peak1.50–1.83GB and process peak RSS1.69GB. These counters overlap and must not be summed. Speech weights were not resident during these measurements; they are not end-to-end TTS benchmarks.

Earlier policies often echoed the original or returned invalid Fish/Higgs shapes. Compact backend-specific prompts improved structural completion, but final prose still sometimes paraphrased the original or suggested an unnecessary register/delivery nuance. Seven valid JSON results do not establish reliable semantic fidelity or improved sound. No new TTS listening comparison was performed for this feature. UI screenshots use synthetic audio and a synthetic suggestion, clearly labeled.

For audio evaluation, compare original and applied guidance with the same reference, text, seed and sampler. Rate word accuracy, identity, accent, pacing and expression separately. Keep the original take and reject a more expressive result if it loses the desired speaker/accent. Existing preferred speech defaults remain unchanged.
