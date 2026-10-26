# Emotion steering attribution

The experimental MLX steering implementation adapts the decode-only, norm-preserving algorithm in `qwen_tts_talker.c` from [Gabriele Mastrapasqua’s qwen3-tts](https://github.com/gabriele-mastrapasqua/qwen3-tts), pinned at ef339be58a778b062e1c14382347964552eae007. Downloaded steering vectors use the same upstream MIT license.

MIT License

Copyright (c) 2025 Gabriele Mastrapasqua

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.


## Startup backend additions

See BACKENDS.md and third_party/ for full exact upstream license copies. Chatterbox official code/weights and audio.cpp use MIT; MOSS VoiceGenerator and its separate v1 codec cards declare Apache-2.0. IndexTTS uses the custom Bilibili model-use license (including output and downstream conditions), Fish S2 Pro the Fish Audio Research License, and Higgs TTS 3 the Boson research/noncommercial license with conditional creator grant. These are not interchangeable MIT/Apache licenses. Pinned conversion hashes do not establish an unpublished source-conversion revision. Dependencies retain their own bundled notices. No private voice/media/catalog files are distributed.


Optional voice prompt expansion uses [Qwen3.5-2B](https://huggingface.co/Qwen/Qwen3.5-2B), Apache-2.0, through [mlx-community/Qwen3.5-2B-4bit](https://huggingface.co/mlx-community/Qwen3.5-2B-4bit/tree/674aaa7240b91e8012fcad5d791b7dfe5ba90207). Revision/checksum are pinned in prompt_assets.py. The text-only application uses MLX-LM0.32.2 in a separate environment; see requirements.enhancer.lock.txt for dependencies. Assets are downloaded/reused separately and are not distributed in this source repository. This auxiliary model does not change any speech backend's license.
