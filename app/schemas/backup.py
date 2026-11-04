"""Schemas for platform backup and restore (النسخة الاحتياطية).

A backup captures the *user-generated* data that cannot be regenerated — cases,
evidence, judgments, users, sections, assistant threads and workspace items.
The legal corpus itself is a versioned, re-seedable asset
(``scripts/seed_corpus.py``) and is recorded by version, not copied.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

#: Identifies the payload so a foreign JSON file cannot be restored by mistake.
BACKUP_FORMAT = "yemen-legal-backup"
BACKUP_VERSION = 1


class BackupCounts(BaseModel):
    """How many records of each kind a snapshot holds (or restored)."""

    users: int = 0
    sections: int = 0
    cases: int = 0
    evidence: int = 0
    judgments: int = 0
    audit: int = 0
    assistant_conversations: int = 0
    assistant_messages: int = 0
    workspace_items: int = 0


class BackupSummary(BaseModel):
    """Lightweight description of the current data set, without the payload."""

    format: str = BACKUP_FORMAT
    version: int = BACKUP_VERSION
    created_at: datetime
    app_name: str
    database: bool = Field(description="True when Postgres/Supabase persistence is active")
    corpus_version: str | None = None
    counts: BackupCounts


class BackupSnapshot(BaseModel):
    """A full backup payload.

    ``data`` maps a collection name to a list of JSON-safe records. The shape is
    deliberately a plain mapping so a snapshot written by an older build can
    still be read when new collections are added.
    """

    format: str = BACKUP_FORMAT
    version: int = BACKUP_VERSION
    created_at: datetime
    app_name: str
    database: bool = False
    corpus_version: str | None = None
    includes_credentials: bool = False
    counts: BackupCounts = Field(default_factory=BackupCounts)
    data: dict[str, Any] = Field(default_factory=dict)


class RestoreResult(BaseModel):
    """Outcome of a restore, per collection."""

    restored: BackupCounts
    replaced: bool = Field(description="True when the existing data was cleared first")
    dry_run: bool = False
    warnings: list[str] = Field(default_factory=list)
