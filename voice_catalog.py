"""Persistent voice descriptions and searchable reference recordings."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import threading
import unicodedata
from uuid import uuid4
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

IDENTITY = re.compile(r"[a-f0-9]{32}")
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
Tags = Annotated[list[Tag], Field(max_length=20)]
metadata_lock = threading.RLock()


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


def read_voice(root, identity):
    if not IDENTITY.fullmatch(identity):
        raise FileNotFoundError("Invalid voice ID")
    folder = root / "voices"
    if not (folder / f"{identity}.wav").is_file():
        raise FileNotFoundError("Voice reference recording is unavailable")
    raw = json.loads((folder / f"{identity}.json").read_text())
    raw.update(id=identity, audio_url=f"/api/audio/voices/{identity}")
    raw.setdefault("tags", [])
    raw.setdefault("description", "")
    raw.setdefault("design_description", raw["description"])
    raw.setdefault("updated_at", raw.get("created_at", ""))
    return Voice.model_validate(raw)


def all_voices(root):
    rows = []
    for path in (root / "voices").glob("*.json"):
        if IDENTITY.fullmatch(path.stem):
            try:
                rows.append(read_voice(root, path.stem))
            except (OSError, ValueError, TypeError):
                continue
    return rows


def searchable(value):
    return "".join(c for c in unicodedata.normalize("NFKD", value.casefold())
                   if not unicodedata.combining(c))


def search(root, query="", tag="", sort="newest", limit=24, offset=0):
    words = searchable(query).split()
    chosen_tag = searchable(tag.strip())
    rows = []
    for row in all_voices(root):
        haystack = searchable(" ".join([row.name, row.description, row.design_description, *row.tags]))
        if words and not all(word in haystack for word in words):
            continue
        if chosen_tag and chosen_tag not in {searchable(item) for item in row.tags}:
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
        raw.update(changes)
        raw["updated_at"] = now()
        atomic_json(path, raw)
        return read_voice(root, identity)
