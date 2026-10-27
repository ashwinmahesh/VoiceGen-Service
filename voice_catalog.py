"""Persistent voice descriptions and searchable reference recordings."""
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import re
import threading
import unicodedata
from uuid import uuid4
from typing import Annotated

from transcription_schemas import TranscriptionInfo
from collection_schemas import CollectionDeletion
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

IDENTITY = re.compile(r"[a-f0-9]{32}")
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
Tags = Annotated[list[Tag], Field(max_length=20)]
metadata_lock = threading.RLock()
DELETED_RETENTION_SECONDS = 24 * 60 * 60


def now():
    return datetime.now(timezone.utc).isoformat()


def normalize_tags(tags):
    return list(dict.fromkeys(" ".join(tag.split()).casefold() for tag in tags))


class SaveVoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    description: Description | None = None
    tags: Tags = Field(default_factory=list)
    transcript: str | None = Field(default=None, max_length=3000)
    output_id: str | None = None
    reference_id: str | None = None

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, value):
        return normalize_tags(value)


class VoiceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name | None = None
    description: Description | None = None
    tags: Tags | None = None
    transcript: str | None = Field(default=None, max_length=3000)

    @field_validator("name", "description", "tags", "transcript")
    @classmethod
    def reject_explicit_null(cls, value):
        if value is None:
            raise ValueError("Use an empty description or empty tags to clear them; null is not supported")
        return value

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, value):
        return normalize_tags(value)


class Voice(BaseModel):
    id: str
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    transcript: str = ""
    transcription: TranscriptionInfo | None = None
    deletion: CollectionDeletion | None = None
    design_description: str = ""
    created_at: str
    updated_at: str
    source_id: str | None = None
    source_kind: str | None = None
    duration: float = Field(gt=0)
    audio_url: str


class VoicePage(BaseModel):
    data: list[Voice]
    total: int
    offset: int
    limit: int
    has_more: bool
    next_offset: int | None


def atomic_json(path, data):
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def collection_marker(root, kind, identity):
    if kind not in ('voices','outputs') or not IDENTITY.fullmatch(identity):
        raise FileNotFoundError('Collection entry not found')
    return Path(root)/'collection-deletions'/f'{kind}-{identity}.json'


def is_deleted(root, kind, identity):
    return collection_marker(root,kind,identity).is_file()


def deletion_info(root, kind, identity):
    marker = collection_marker(root, kind, identity)
    if not marker.is_file():
        return None
    if marker.is_symlink() or marker.parent.is_symlink():
        raise ValueError('Collection deletion state must not be a symbolic link')
    row = json.loads(marker.read_text())
    stamp = datetime.fromisoformat(row['deleted_at'])
    if stamp.tzinfo is None:
        raise ValueError('Deletion time must include its timezone')
    return CollectionDeletion(deleted_at=stamp.isoformat(),
        purge_after=(stamp + timedelta(seconds=DELETED_RETENTION_SECONDS)).isoformat(),
        cleanup_started=bool(row.get('cleanup_started')),
        cleanup_deferred_reason=row.get('cleanup_deferred_reason'))


def set_deleted(root, kind, identity, deleted):
    """Keep recordings restorable for 24 hours before generator-owned cleanup."""
    from config import validate_mount
    validate_mount(Path(root))
    with metadata_lock:
        marker=collection_marker(root,kind,identity)
        if marker.parent.is_symlink() or marker.is_symlink():
            raise ValueError('Collection deletion state must not be a symbolic link')
        if marker.is_file()==deleted:return False
        if deleted:
            marker.parent.mkdir(parents=True,exist_ok=True)
            atomic_json(marker,{'id':identity,'kind':kind,'deleted_at':now()})
        else:marker.unlink()
        return True


def read_voice(root, identity, include_deleted=False):
    if not IDENTITY.fullmatch(identity):
        raise FileNotFoundError("Invalid voice ID")
    if not include_deleted and is_deleted(root,'voices',identity):
        raise FileNotFoundError('Voice is deleted from the library; restore it first')
    folder = root / "voices"
    if not (folder / f"{identity}.wav").is_file():
        raise FileNotFoundError("Voice reference recording is unavailable")
    raw = json.loads((folder / f"{identity}.json").read_text())
    raw.update(id=identity, audio_url=f"/api/audio/voices/{identity}")
    raw.setdefault("tags", [])
    raw.setdefault("description", "")
    raw.setdefault("design_description", raw["description"])
    raw.setdefault("updated_at", raw.get("created_at", ""))
    if include_deleted:
        raw['deletion'] = deletion_info(root, 'voices', identity)
    return Voice.model_validate(raw)


def all_voices(root, deleted=False):
    rows = []
    for path in (root / "voices").glob("*.json"):
        if IDENTITY.fullmatch(path.stem):
            try:
                if is_deleted(root,'voices',path.stem)==deleted:
                    rows.append(read_voice(root,path.stem,include_deleted=deleted))
            except (OSError, ValueError, TypeError, KeyError):
                continue
    return rows


def searchable(value):
    return "".join(c for c in unicodedata.normalize("NFKD", value.casefold())
                   if not unicodedata.combining(c))


def search(root, query="", tag="", sort="newest", limit=24, offset=0, deleted=False, tags=()):
    words = searchable(query).split()
    chosen_tags = {searchable(item.strip()) for item in [tag, *tags] if item.strip()}
    rows = []
    for row in all_voices(root,deleted=deleted):
        haystack = searchable(" ".join([row.name, row.description, row.design_description, *row.tags]))
        if words and not all(word in haystack for word in words):
            continue
        if chosen_tags and chosen_tags.isdisjoint(searchable(item) for item in row.tags):
            continue
        rows.append(row)
    if sort == "name":
        rows.sort(key=lambda row: (searchable(row.name), row.id))
    else:
        field = "updated_at" if sort == "updated" else "created_at"
        rows.sort(key=lambda row: (getattr(row, field), row.id), reverse=True)
    total = len(rows)
    more = offset + limit < total
    return VoicePage(data=rows[offset:offset + limit], total=total, offset=offset, limit=limit,
                     has_more=more, next_offset=offset + limit if more else None)


def update_voice(root, identity, changes):
    with metadata_lock:
        current = read_voice(root, identity)
        path = root / "voices" / f"{identity}.json"
        raw = json.loads(path.read_text())
        # Preserve original design text before changing the editable catalog notes.
        raw.setdefault("design_description", current.design_description)
        if "transcript" in changes and changes["transcript"] != current.transcript:
            raw.pop("transcription", None)
        raw.update(changes)
        raw["updated_at"] = now()
        atomic_json(path, raw)
        return read_voice(root, identity)
