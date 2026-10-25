"""CPU-only silence insertion at explicitly reviewed quiet spans. Never edits source audio."""
import hashlib
from io import BytesIO
import json
import math
import threading
import time
from uuid import uuid4
import numpy as np
import soundfile as sf
import storage
from voice_catalog import atomic_json, now

MAX_BYTES = 128*1024*1024
MAX_SCALAR_SAMPLES = 48_000_000
MAX_SOURCE_SECONDS = 600
MAX_OUTPUT_SECONDS = 900
QUIET_PEAK_PCM16 = 32  # fixed conservative ceiling, about -60 dBFS; not a sentence detector
EDIT_LOCK = threading.Lock()


class PauseEditBusy(ValueError): pass


def insert_reviewed_pauses(data, sample_rate, request):
    """Return unchanged source samples interspersed with zeros, plus sample-exact provenance."""
    total = len(data)
    target = math.ceil(request.minimum_gap_ms*sample_rate/1000)
    edits = []
    previous_end = 0
    for span in request.spans:
        start, end = round(span.start_seconds*sample_rate), round(span.end_seconds*sample_rate)
        if not (0 < start < end < total) or start < previous_end:
            raise ValueError('Select ordered, nonoverlapping gaps strictly inside the recording')
        if end-start < math.ceil(.02*sample_rate):
            raise ValueError('Review a quiet span at least 20 milliseconds long')
        peak = int(np.max(np.abs(data[start:end].astype(np.int32))))
        if peak > QUIET_PEAK_PCM16:
            raise ValueError('A selected span contains audible samples. Review a quieter gap; no audio was changed.')
        inserted = max(0,target-(end-start))
        edits.append({'start_sample':start,'end_sample':end,'insertion_sample':(start+end)//2,
            'original_gap_samples':end-start,'inserted_samples':inserted,
            'effective_gap_samples':end-start+inserted,'reviewed_span_peak_pcm16':peak})
        previous_end = end
    added = sum(row['inserted_samples'] for row in edits)
    if (total+added)/sample_rate > MAX_OUTPUT_SECONDS or (total+added)*data.shape[1] > MAX_SCALAR_SAMPLES:
        raise ValueError('Adjusted recording exceeds the bounded output size')
    pieces=[];cursor=0
    for edit in edits:
        at=edit['insertion_sample']
        pieces.extend([data[cursor:at],np.zeros((edit['inserted_samples'],data.shape[1]),dtype=np.int16)])
        cursor=at
    pieces.append(data[cursor:])
    return np.concatenate(pieces), edits


def adjust_pauses(identity, request):
    if not EDIT_LOCK.acquire(blocking=False):
        raise PauseEditBusy('Another pause adjustment is running. Wait for it to finish.')
    temp = destination = None
    try:
        started=time.monotonic()
        source=storage.asset_path('outputs',identity)
        # Mount validation precedes both reads and directory creation; never fall back to another volume.
        storage.validate_mount(storage.ROOT)
        if source.stat().st_size > MAX_BYTES:
            raise ValueError('Source audio exceeds the 128 MiB editing limit')
        with source.open('rb') as stream:
            raw=stream.read(MAX_BYTES+1)
        if len(raw)>MAX_BYTES:
            raise ValueError('Source audio exceeds the 128 MiB editing limit')
        source_hash=hashlib.sha256(raw).hexdigest()
        key_path=None
        fingerprint=hashlib.sha256(json.dumps({'source_id':identity,'source_sha256':source_hash,
            'request':request.model_dump(exclude={'idempotency_key'})},sort_keys=True).encode()).hexdigest()
        if request.idempotency_key:
            key=hashlib.sha256(request.idempotency_key.encode()).hexdigest()
            key_path=storage.ROOT/'idempotency'/f'pause-{key}.json'
            if key_path.is_file():
                saved=json.loads(key_path.read_text())
                if saved['fingerprint']!=fingerprint:
                    raise PauseEditBusy('This pause-adjustment idempotency key was used with different settings')
                return storage.read_artifact(saved['artifact'])
        with sf.SoundFile(BytesIO(raw)) as audio:
            sr,channels,frames=audio.samplerate,audio.channels,len(audio)
            if (audio.format!='WAV' or audio.subtype!='PCM_16' or not 8000<=sr<=96000 or channels not in [1,2]
                    or not 0<frames/sr<=MAX_SOURCE_SECONDS or frames*channels>MAX_SCALAR_SAMPLES):
                raise ValueError('Pause editing requires a bounded mono/stereo PCM16 WAV, up to 600 seconds')
            data=audio.read(dtype='int16',always_2d=True)
        del raw
        adjusted,edits=insert_reviewed_pauses(data,sr,request)
        if adjusted.nbytes+4096>MAX_BYTES:
            raise ValueError('Adjusted recording exceeds the 128 MiB editing limit')
        if source.with_suffix('.json').stat().st_size > 1024*1024:
            raise ValueError('Source metadata exceeds the editing limit')
        original=storage.read_artifact(identity)
        if len(original.get('transformation_history',[]))>=20:
            raise ValueError('Too many derived edits; return to the original recording')
        output=storage.output_folder(request.output_dir)
        new_id=uuid4().hex
        destination=output/f'{new_id}.wav'
        temp=destination.with_suffix('.tmp.wav')
        sf.write(temp,adjusted,sr,subtype='PCM_16')
        temp.replace(destination)
        peaks=[round(float(np.max(np.abs(chunk.astype(np.int32))))/32768,4)
               for chunk in np.array_split(adjusted,min(200,len(adjusted))) if len(chunk)]
        transformation={'type':'reviewed_pause_insertion','source':{'artifact_id':identity,
            'path':str(source),'sha256':source_hash,'frames':frames,
            'original_artifact_id':original.get('transformation',{}).get('source',{}).get('original_artifact_id',identity)},
            'requested':request.model_dump(exclude={'idempotency_key'}),
            'effective':{'sample_rate':sr,'channels':channels,'minimum_gap_ms':request.minimum_gap_ms,
                'quiet_peak_limit_pcm16':QUIET_PEAK_PCM16,'spans':edits,
                'inserted_samples':len(adjusted)-frames,'output_frames':len(adjusted),
                'source_samples_preserved':True,'automatic_sentence_alignment':False},
            'processing_seconds':round(time.monotonic()-started,3)}
        row={**original,'id':new_id,'created_at':now(),'duration':round(len(adjusted)/sr,3),
            'waveform':peaks,'sample_rate':sr,'channels':channels,'format':'WAV/PCM_16','path':str(destination),'metadata_path':str(destination.with_suffix('.json')),
            'audio_url':f'/api/audio/outputs/{new_id}','transformation':transformation,
            'transformation_history':original.get('transformation_history',[])+([original['transformation']] if original.get('transformation') else [])}
        storage.persist_artifact(destination,row)
        if key_path:atomic_json(key_path,{'fingerprint':fingerprint,'artifact':new_id})
        return row
    except Exception:
        # Preserve a completed WAV+sidecar if only its final index/idempotency write failed.
        if destination and not destination.with_suffix('.json').is_file():destination.unlink(missing_ok=True)
        raise
    finally:
        if temp:temp.unlink(missing_ok=True)
        EDIT_LOCK.release()
