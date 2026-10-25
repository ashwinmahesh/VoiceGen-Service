"""Opaque artifact lookup, original reference retention and atomic persistence."""
import json
import tempfile
from pathlib import Path
from config import ROOT, OUTPUT_DIR, validate_mount
from voice_catalog import IDENTITY, atomic_json


def output_folder(value):
    path = Path(value).expanduser() if value else OUTPUT_DIR
    if not path.is_absolute():
        raise ValueError('output_dir must be an absolute local directory')
    path = validate_mount(path)
    path.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path, prefix='.write-check-'):
        pass
    return path.resolve()


def asset_path(kind, identity):
    if kind not in {'outputs','voices','references'} or not IDENTITY.fullmatch(identity):
        raise FileNotFoundError('Audio not found')
    path = ROOT/kind/f'{identity}.wav'
    if kind == 'outputs' and not path.is_file():
        path = OUTPUT_DIR/f'{identity}.wav'
    if kind == 'outputs':
        index = ROOT/'artifact-index'/f'{identity}.json'
        if index.is_file():
            path = Path(json.loads(index.read_text())['path'])
    validate_mount(path)
    if not path.is_file():
        raise FileNotFoundError('Audio not found')
    return path


def read_artifact(identity):
    path = asset_path('outputs',identity)
    row = json.loads(path.with_suffix('.json').read_text())
    row.update(id=identity, path=str(path.absolute()), metadata_path=str(path.with_suffix('.json').absolute()),
               audio_url=f'/api/audio/outputs/{identity}')
    return row


def artifact_rows():
    identities = {p.stem for folder in [ROOT/'outputs', ROOT/'artifact-index'] for p in folder.glob('*.json')
                  if IDENTITY.fullmatch(p.stem)}
    if OUTPUT_DIR != ROOT/'outputs':
        identities.update(p.stem for p in OUTPUT_DIR.glob('*.json') if IDENTITY.fullmatch(p.stem))
    rows = []
    for identity in identities:
        try:
            rows.append(read_artifact(identity))
        except (OSError, ValueError, KeyError, RuntimeError):
            continue
    return sorted(rows,key=lambda row:row.get('created_at',''),reverse=True)


def persist_artifact(destination, row):
    atomic_json(destination.with_suffix('.json'),row)
    atomic_json(ROOT/'artifact-index'/f'{row["id"]}.json', {'path':str(destination.absolute())})


def preserve_reference(raw, filename, transcript=''):
    from io import BytesIO
    from math import gcd
    from uuid import uuid4
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
    from voice_catalog import now
    with sf.SoundFile(BytesIO(raw)) as source:
        sr, channels, frames, fmt = source.samplerate, source.channels, len(source), source.format
        if not (8000 <= sr <= 96000 and 1 <= channels <= 2 and 1 <= frames/sr <= 30
                and frames*channels <= 96000*30*2):
            raise ValueError('Use 1–30 seconds, 8–96 kHz, mono or stereo audio')
        data = source.read(dtype='float32',always_2d=True).mean(axis=1)
    if not np.isfinite(data).all() or float(np.max(np.abs(data))) < 1e-5:
        raise ValueError('Use audible, finite speech audio')
    if sr != 16000:
        divisor = gcd(sr,16000)
        data = resample_poly(data,16000//divisor,sr//divisor)
    identity = uuid4().hex
    target = ROOT/'references'/f'{identity}.wav'
    original = ROOT/'references/originals'/f'{identity}.bin'
    temp = target.with_suffix('.tmp.wav')
    row = {'id':identity,'name':Path(filename or 'Reference').name,'duration':round(len(data)/16000,3),
        'audio_url':f'/api/audio/references/{identity}', 'original_url':f'/api/references/{identity}/original',
        'created_at':now(),'transcript':transcript,
        'original':{'path':str(original), 'bytes':len(raw),'sample_rate':sr,'channels':channels,
                    'frames':frames,'format':fmt,'filename':Path(filename or 'Reference').name},
        'effective':{'sample_rate':16000,'channels':1,'frames':len(data),'format':'WAV/PCM_16'}}
    try:
        original.write_bytes(raw)
        sf.write(temp,data,16000,subtype='PCM_16')
        temp.replace(target)
        atomic_json(target.with_suffix('.json'),row)
    except Exception:
        for path in [target,original,target.with_suffix('.json')]: path.unlink(missing_ok=True)
        raise
    finally:
        temp.unlink(missing_ok=True)
    return row
