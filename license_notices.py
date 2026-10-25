"""Model-scoped notices derived from the bundled primary license texts; no approval gate."""
import logging
from textwrap import fill


NOTICES={
    'fish':{
        'license':'LicenseRef-Fish-Audio-Research',
        'license_url':'https://huggingface.co/fishaudio/s2-pro/blob/1de9996b6be38b745688de084d87a5633f714e4e/LICENSE.md',
        'license_file':'third_party/fish-LICENSE.md',
        'commercial_use_notice':'Fish S2 Pro: using this model to generate audio here for commercial purposes, including a commercial game or product, requires a separate written commercial license from Fish Audio. Research/noncommercial model use is permitted under the license.',
        'notice':'Fish S2 Pro: research/noncommercial model use is free; commercial use of the model, including generating audio for commercial purposes, requires a separate Fish Audio license. Output ownership does not remove these model-use conditions. Outputs must not create or improve foundational generative AI models except the licensed models/derivatives. Read the full terms.',
    },
    'higgs':{
        'license':'LicenseRef-Boson-Higgs-TTS-3-Research-Noncommercial',
        'license_url':'https://huggingface.co/bosonai/higgs-tts-3-4b/blob/239f63fb7b02b1aa085f98d9efae5e35cc5523e8/LICENSE',
        'license_file':'third_party/higgs-LICENSE',
        'commercial_use_notice':"Higgs TTS 3: commercial use of audio generated here requires a separate written Boson license unless it qualifies for the Creator Use Grant. Eligible creator content, including monetized content on channels you own/control, is permitted with prominent Boson AI Higgs Audio acknowledgment. Model hosting, product integration and other uses outside that grant require a separate license. Research/noncommercial use is permitted under the license.",
        'notice':"Higgs TTS 3: research/noncommercial use is permitted. The conditional Creator Use Grant permits creative audio, including monetized creator content on channels you own/control, with prominent Boson AI Higgs Audio acknowledgment in the audio or accompanying text. Hosting/serving the model to others, embedding it in third-party products/services, resale/redistribution and other commercial uses outside that grant require a separate Boson license. Read the full terms.",
    },
    'indextts':{
        'license':'LicenseRef-Bilibili-IndexTTS',
        'license_url':'https://github.com/index-tts/index-tts/blob/d9e41aac89fd00b3d71497fddb287b7f24613712/LICENSE',
        'license_file':'third_party/indextts-LICENSE',
        'commercial_use_notice':'IndexTTS 2.5: a separate written Bilibili license is required if you or your affiliates exceed 100 million monthly active users in the preceding month or RMB 1 billion annual revenue in the preceding year. Commercial audio use is not universally prohibited; the custom license and its other obligations still apply.',
        'notice':'IndexTTS 2.5 uses the custom Bilibili Model Use License, including output/derivative and downstream obligations. A separate written license is required if you or affiliates exceed 100 million monthly active users in the preceding month or RMB 1 billion annual revenue in the preceding year. Retain required notices/license and observe restrictions on improving other AI models. Commercial use is not universally prohibited. Read the full terms.',
    },
}


def notice_for(backend):
    return dict(NOTICES.get(backend,{}))


def warn_at_startup(backend):
    """Emit on each startup, including noninteractive starts; never ask for acknowledgment."""
    notice=notice_for(backend)
    if not notice:return
    border='='*72
    message='\n'.join([border,'MODEL LICENSE WARNING',
        fill(notice['commercial_use_notice'],width=88,break_long_words=False,break_on_hyphens=False),
        'Full terms: '+notice['license_url'],border])
    logging.warning('\n%s',message)
