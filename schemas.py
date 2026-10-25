"""The identical API contract used by both local studios."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChatterboxOptions(BaseModel):
    model_config = ConfigDict(extra='forbid',allow_inf_nan=False)
    exaggeration: float | None = Field(default=None,ge=0,le=1)
    cfg_weight: float | None = Field(default=None,ge=0,le=1)


class IndexTTSOptions(BaseModel):
    model_config = ConfigDict(extra='forbid',allow_inf_nan=False)
    emotion_reference_id: str | None = Field(default=None,pattern=r'^[a-f0-9]{32}$')
    emotion_vector: list[float] | None = Field(default=None,min_length=8,max_length=8)
    emotion_alpha: float | None = Field(default=None,ge=0,le=1)
    duration_factor: float | None = Field(default=None,ge=.7,le=1.5)
    interval_silence: int | None = Field(default=None,ge=0,le=2000)

    @model_validator(mode='after')
    def emotion_source(self):
        if self.emotion_vector is not None:
            import math
            if any(not math.isfinite(v) or not 0<=v<=1 for v in self.emotion_vector) or sum(self.emotion_vector)>1:
                raise ValueError('Emotion vector needs eight finite 0–1 values with total at most 1')
        if self.emotion_reference_id and self.emotion_vector is not None:
            raise ValueError('Choose an emotion reference or emotion vector')
        return self


MODEL_OPTIONS={'chatterbox':ChatterboxOptions,'indextts':IndexTTSOptions}
LEGACY_BACKEND_FIELDS=set().union(*(model.model_fields for model in MODEL_OPTIONS.values()))


class Generation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=3000)
    description: str = Field(default='', max_length=1500)
    emotion: str = Field(default='', max_length=1000)
    voice_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    reference_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    preset_voice: str | None = Field(default=None, max_length=40)
    accent: str = Field(default='', max_length=500)
    clone_mode: Literal['reference','continuation','combined'] = 'reference'
    steering_emotion: str | None = Field(default=None, max_length=30)
    steering_strength: float | None = Field(default=None, ge=0, le=12)
    ref_text: str | None = Field(default=None, max_length=3000)
    language: str = Field(default='auto', max_length=30)
    seed: int = Field(default=42, ge=0, le=2**32-1)
    steps: int | None = Field(default=None, ge=1, le=30)
    guidance: float | None = Field(default=None, ge=1, le=5)
    temperature: float | None = Field(default=None, ge=0, le=2)
    chatterbox: ChatterboxOptions | None = None
    indextts: IndexTTSOptions | None = None
    exaggeration: float | None = Field(default=None, json_schema_extra={'deprecated':True}, ge=0, le=1)
    cfg_weight: float | None = Field(default=None, json_schema_extra={'deprecated':True}, ge=0, le=1)
    emotion_reference_id: str | None = Field(default=None, json_schema_extra={'deprecated':True}, pattern=r'^[a-f0-9]{32}$')
    emotion_vector: list[float] | None = Field(default=None, json_schema_extra={'deprecated':True}, min_length=8, max_length=8)
    emotion_alpha: float | None = Field(default=None, json_schema_extra={'deprecated':True}, ge=0, le=1)
    duration_factor: float | None = Field(default=None, json_schema_extra={'deprecated':True}, ge=0.7, le=1.5)
    interval_silence: int | None = Field(default=None, json_schema_extra={'deprecated':True}, ge=0, le=2000)
    max_tokens: int = Field(default=1000, ge=50, le=2000)
    output_dir: str | None = Field(default=None, max_length=4096)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode='after')
    def combinations(self):
        for backend,model in MODEL_OPTIONS.items():
            if getattr(self,backend) is not None and any(getattr(self,key) is not None for key in model.model_fields):
                raise ValueError(f'Choose {backend} options or legacy flat aliases, not both')
        if not self.text.strip():
            raise ValueError('Enter some text to speak')
        if self.emotion_vector is not None:
            import math
            if any(not math.isfinite(v) or not 0 <= v <= 1 for v in self.emotion_vector):
                raise ValueError('Emotion vector needs eight finite values between 0 and 1')
            if sum(self.emotion_vector) > 1:
                raise ValueError('Emotion vector sum must not exceed 1')
        if self.emotion_reference_id and self.emotion_vector is not None:
            raise ValueError('Choose an emotion reference or emotion vector')
        if sum(bool(x) for x in [self.voice_id,self.reference_id,self.preset_voice]) > 1:
            raise ValueError('Choose a saved voice or uploaded reference')
        if not self.voice_id and not self.reference_id and not self.preset_voice and not self.description.strip():
            raise ValueError('Describe a voice or select a reference')
        return self


def generation_metadata(request):
    result=request.model_dump(exclude={'idempotency_key',*LEGACY_BACKEND_FIELDS})
    for backend,model in MODEL_OPTIONS.items():
        if result[backend] is None:
            aliases={key:getattr(request,key) for key in model.model_fields if getattr(request,key) is not None}
            if aliases:result[backend]=aliases
    return result


class Capabilities(BaseModel):
    api_version: str = '2.1.0'
    service: str
    name: str
    backend: str
    quantization: str
    sample_rate: int
    models: dict
    extensions: dict = Field(default_factory=dict)
    presets: list[dict] = Field(default_factory=list)
    modes: dict
    controls: dict
    languages: list[str]
    limits: dict
    storage: str
    output_dir: str


class Health(BaseModel):
    status: Literal['unloaded','loading','ready','generating','unloading','error']
    service: str
    name: str
    project: str
    storage: str
    downloaded: bool
    asset_errors: list[str]
    runtime_errors: list[str] = Field(default_factory=list)
    model_loaded: bool
    loaded_variant: str | None
    busy: bool
    active_job: str | None
    mode: str = 'local-jobs'
    last_error: str | None


class Artifact(BaseModel):
    model_config = ConfigDict(extra='allow')  # legacy records retain their original fields
    id: str
    text: str
    created_at: str
    duration: float
    sample_rate: int
    audio_url: str
    path: str
    metadata_path: str | None = None
    requested: dict = Field(default_factory=dict)
    effective: dict = Field(default_factory=dict)
    waveform: list[float] = Field(default_factory=list)


class Job(BaseModel):
    id: str
    status: Literal['queued','running','done','error']
    stage: str
    created_at: str
    updated_at: str
    error: str | None = None
    result: Artifact | None = None


class ArtifactPage(BaseModel):
    data: list[Artifact]
    total: int
    offset: int
    limit: int
    has_more: bool
    next_offset: int | None


class Reference(BaseModel):
    id: str
    name: str
    duration: float
    audio_url: str
    original_url: str
    created_at: str
    transcript: str = ''
    original: dict
    effective: dict


class Unloaded(BaseModel):
    model_loaded: bool


class TagCount(BaseModel):
    name: str
    count: int


class TagsPage(BaseModel):
    data: list[TagCount]


class ReviewedPauseSpan(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    start_seconds: float = Field(ge=0, le=600)
    end_seconds: float = Field(gt=0, le=600)

    @model_validator(mode='after')
    def increasing(self):
        if self.end_seconds <= self.start_seconds:
            raise ValueError('Each reviewed gap must end after it starts')
        return self


class PauseAdjustment(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    spans: list[ReviewedPauseSpan] = Field(min_length=1, max_length=30)
    reviewed: Literal[True]
    minimum_gap_ms: int = Field(default=750, ge=100, le=5000)
    output_dir: str | None = Field(default=None, max_length=4096)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode='after')
    def ordered(self):
        if any(a.end_seconds > b.start_seconds for a,b in zip(self.spans,self.spans[1:])):
            raise ValueError('Reviewed gaps must be ordered and must not overlap')
        return self
