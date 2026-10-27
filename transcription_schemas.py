"""Inference-free descriptions for the local upload transcriber."""
from pydantic import BaseModel,Field


class TranscriptionInfo(BaseModel):
    engine: str
    model: str
    language: str
    seconds: float=Field(ge=0)
    device: str='CPU'


class TranscriptionStatus(BaseModel):
    available: bool
    engine: str
    model: str
    device: str
    max_seconds: int
    languages: dict[str,str]
    message: str
