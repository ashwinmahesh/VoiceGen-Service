"""Shared queue-management requests; no inference or storage configuration imports."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class QueueMove(BaseModel):
    model_config = ConfigDict(extra='forbid')
    before_id: str | None = Field(default=None,pattern=r'^[a-f0-9]{32}$')
    after_id: str | None = Field(default=None,pattern=r'^[a-f0-9]{32}$')
    direction: Literal['up','down'] | None = None

    @model_validator(mode='after')
    def one_target(self):
        if sum(value is not None for value in (self.before_id,self.after_id,self.direction)) != 1:
            raise ValueError('Supply exactly one of before_id, after_id, or direction')
        return self
