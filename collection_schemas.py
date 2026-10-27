"""Collection retention fields shared by voice and recording responses."""
from pydantic import BaseModel


class CollectionDeletion(BaseModel):
    deleted_at: str
    purge_after: str
    cleanup_started: bool = False
    cleanup_deferred_reason: str | None = None


class CollectionCleanupStatus(BaseModel):
    retention_seconds: int
    interval_seconds: int
    running: bool
    last_checked_at: str | None = None
    last_error: str | None = None
    removed_last_run: int = 0
    deferred_last_run: int = 0
