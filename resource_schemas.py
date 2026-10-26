"""Public resource-history responses, independent of inference libraries."""
from typing import Literal
from pydantic import BaseModel, Field


class ResourceMetrics(BaseModel):
    cpu_percent: float | None = Field(None,description='Interval CPU across VoiceGen descendants; 100% is one core.')
    rss_bytes: float | None = Field(None,description='Summed process-tree resident bytes; shared pages may be counted more than once.')
    gpu_percent: float | None = Field(None,description='Device-wide macOS driver GPU utilization, including other apps.')
    gpu_allocated_bytes: float | None = Field(None,description='Device-wide allocated shared system memory, not VRAM capacity.')
    gpu_in_use_bytes: float | None = Field(None,description='Device-wide in-use GPU shared system memory.')


class ResourcePeaks(BaseModel):
    peak_cpu_percent: float | None = None
    peak_rss_bytes: float | None = None
    peak_gpu_percent: float | None = None
    peak_gpu_allocated_bytes: float | None = None
    peak_gpu_in_use_bytes: float | None = None


class ResourceSummary(ResourcePeaks):
    samples: int = Field(description='Number of original snapshots before aggregation.')


class ResourcePoint(ResourceMetrics, ResourcePeaks):
    ts: float = Field(description='First sample timestamp in the bucket; Unix seconds UTC.')
    last_ts: float
    session_id: str
    backend: str = Field(description='Selected speech backend, even during LLM expansion.')
    model: str = Field(description='Concrete speech model or prompt LLM used in this phase.')
    stage: str
    run_id: str | None = None
    samples: int
    bucket: int


class ResourceCollector(BaseModel):
    running: bool
    last_sample_at: float | None
    storage_error: str | None
    counter_notices: list[str]
    dropped_samples: int
    session_id: str
    active_job: str | None


class RecordedModel(BaseModel):
    backend: str
    model: str


class AvailableResourceModel(BaseModel):
    id: str
    name: str


class ResourceSeries(BaseModel):
    start: float
    end: float
    bucket_seconds: int
    summary: ResourceSummary
    points: list[ResourcePoint]
    models: list[RecordedModel]
    truncated: bool
    scopes: dict[str,str]
    collector: ResourceCollector


class ResourceOverview(ResourceSeries):
    window: Literal['5m','30m','1h','6h','24h','3d','7d']
    retention_seconds: int
    available_models: list[AvailableResourceModel]
    cpu_cores: int | None


class ResourceRun(ResourcePeaks):
    id: str = Field(description='Resource recording ID; use as run_id to select this attempt.')
    job_id: str
    session_id: str
    backend: str
    model: str
    attempt: int
    started_at: float
    ended_at: float | None
    status: Literal['running','done','error','interrupted']
    samples: int


class ResourceJobPage(BaseModel):
    data: list[ResourceRun]
    total: int
    offset: int
    limit: int
    has_more: bool


class ResourceJobHistory(ResourceSeries):
    job_id: str
    runs: list[ResourceRun]
    selected_run: ResourceRun
