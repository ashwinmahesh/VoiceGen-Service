"""Model-scoped notices derived from the bundled primary license texts; no approval gate."""
NOTICES={
    'fish':{
        'license':'LicenseRef-Fish-Audio-Research',
        'license_url':'https://huggingface.co/fishaudio/s2-pro/blob/1de9996b6be38b745688de084d87a5633f714e4e/LICENSE.md',
        'license_file':'third_party/fish-LICENSE.md',
        'notice':'Fish S2 Pro: research/noncommercial model use is free; commercial use of the model, including generating audio for commercial purposes, requires a separate Fish Audio license. Output ownership does not remove these model-use conditions. Outputs must not create or improve foundational generative AI models except the licensed models/derivatives. Read the full terms.',
    },
    'higgs':{
        'license':'LicenseRef-Boson-Higgs-TTS-3-Research-Noncommercial',
        'license_url':'https://huggingface.co/bosonai/higgs-tts-3-4b/blob/239f63fb7b02b1aa085f98d9efae5e35cc5523e8/LICENSE',
        'license_file':'third_party/higgs-LICENSE',
        'notice':"Higgs TTS 3: research/noncommercial use is permitted. The conditional Creator Use Grant permits creative audio, including monetized creator content on channels you own/control, with prominent Boson AI Higgs Audio acknowledgment in the audio or accompanying text. Hosting/serving the model to others, embedding it in third-party products/services, resale/redistribution and other commercial uses outside that grant require a separate Boson license. Read the full terms.",
    },
    'indextts':{
        'license':'LicenseRef-Bilibili-IndexTTS',
        'license_url':'https://github.com/index-tts/index-tts/blob/d9e41aac89fd00b3d71497fddb287b7f24613712/LICENSE',
        'license_file':'third_party/indextts-LICENSE',
        'notice':'IndexTTS 2.5 uses the custom Bilibili Model Use License, including output/derivative and downstream obligations. A separate written license is required if you or affiliates exceed 100 million monthly active users in the preceding month or RMB 1 billion annual revenue in the preceding year. Retain required notices/license and observe restrictions on improving other AI models. Commercial use is not universally prohibited. Read the full terms.',
    },
}


def notice_for(backend):
    return dict(NOTICES.get(backend,{}))
