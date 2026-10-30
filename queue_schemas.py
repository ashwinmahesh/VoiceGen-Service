"""Shared queue-management requests; no inference or storage configuration imports."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class QueueMove(BaseModel):
    """Supply exactly one target: before_id, after_id, or direction (up/down)."""
    model_config = ConfigDict(extra='forbid')
    before_id: str | None = Field(default=None,pattern=r'^[a-f0-9]{32}$')
    after_id: str | None = Field(default=None,pattern=r'^[a-f0-9]{32}$')
    direction: Literal['up','down'] | None = None

    @model_validator(mode='after')
    def one_target(self):
        if sum(value is not None for value in (self.before_id,self.after_id,self.direction)) != 1:
            raise ValueError('Supply exactly one of before_id, after_id, or direction')
        return self


class JobProgress(BaseModel):
    """Estimated from recent finished takes of the same model; speech models report no step count."""
    percent: int = Field(ge=0,le=100,description='0 while preparing, at most 95 until the take is saved, 100 when done.')
    phase: Literal['preparing','generating','waiting','done'] = Field(
        description='preparing: starting, loading or planning; generating: speech is being made; waiting: a batch between takes.')
    estimated: bool = Field(description='True until the take is saved; the percentage is a time estimate, not a measured step.')
    eta_seconds: int | None = Field(default=None,description='Estimated seconds left; null while preparing or when overdue.')
    overdue: bool = Field(default=False,description='The take is taking longer than its estimate; percent holds at 95.')
    basis: Literal['history','default'] | None = Field(default=None,
        description='history: recent finished takes of this model; default: built-in starting rate before any history.')


class QueueState(BaseModel):
    """Persisted global pause state; immediate work can still run while paused."""
    paused: bool


class ModelDescriptor(BaseModel):
    id: str
    name: str
    modes: list[str]
    is_default: bool


class ModelList(BaseModel):
    default_model: str
    active_model: str | None = None
    data: list[ModelDescriptor]
    runtime_model_switching: bool = True
