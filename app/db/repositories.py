"""Repositories for cases, evidence and judgments.

A single class serves both storage modes: when SUPABASE_DB_URL is configured the
rows live in Postgres; otherwise an in-process store keeps the API fully usable
for development, demos and the test-suite.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg
from pydantic import BaseModel

from app.core.config import settings
from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.db.pool import database

logger = get_logger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def _jsonable(value: Any) -> Any:
    """Make a value safe to bind to a jsonb parameter.

    Pydantic models are dumped in JSON mode and containers are mapped
    recursively, so callers can pass schema objects without worrying about the
    storage boundary.
    """
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_jsonable(item) for item in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


class CaseRepository:
    def __init__(self) -> None:
        self._cases: dict[str, dict[str, Any]] = {}
        self._evidence: dict[str, list[dict[str, Any]]] = {}
        self._judgments: dict[str, list[dict[str, Any]]] = {}

    @property
    def persistent(self) -> bool:
        return settings.database_enabled

    # -- cases -------------------------------------------------------------
    async def create_case(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.persistent:
            try:
                return await self._create_case_pg(payload)
            except asyncpg.UniqueViolationError as exc:
                raise ValidationError(
                    f"رقم القضية «{payload.get('case_number')}» مسجَّل مسبقاً",
                    details=str(exc),
                ) from exc

        case_id = str(uuid.uuid4())
        record = {
            **payload,
            "id": case_id,
            "jurisdiction": payload.get("jurisdiction", settings.jurisdiction),
            "status": payload.get("status", "submitted"),
            "created_at": _now(),
            "updated_at": _now(),
        }
        self._cases[case_id] = record
        return record

    async def _create_case_pg(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = {k: _jsonable(v) for k, v in payload.items()}
        row = await database.fetchrow(
            """
            insert into cases (
                case_number, case_type, court_name, circuit, judge_name, jurisdiction,
                status, plaintiff_name, plaintiff_details, defendant_name, defendant_details,
                plaintiff_claims, defendant_responses, subject_matter, claim_value, currency,
                facts_summary, extra
            ) values ($1,$2,$3,$4,$5,$6,$7::case_status,$8,$9::jsonb,$10,$11::jsonb,
                      $12::jsonb,$13::jsonb,$14,$15,$16,$17,$18::jsonb)
            returning *
            """,
            payload.get("case_number"),
            payload["case_type"],
            payload["court_name"],
            payload.get("circuit"),
            payload.get("judge_name"),
            payload.get("jurisdiction", settings.jurisdiction),
            payload.get("status", "submitted"),
            payload["plaintiff_name"],
            payload.get("plaintiff_details") or {},
            payload["defendant_name"],
            payload.get("defendant_details") or {},
            payload.get("plaintiff_claims") or [],
            payload.get("defendant_responses") or [],
            payload.get("subject_matter"),
            payload.get("claim_value"),
            payload.get("currency", "YER"),
            payload.get("facts_summary"),
            payload.get("extra") or {},
        )
        return dict(row)

    async def get_case(self, case_id: str) -> dict[str, Any] | None:
        if self.persistent:
            row = await database.fetchrow("select * from cases where id = $1", uuid.UUID(case_id))
            return dict(row) if row else None
        return self._cases.get(case_id)

    async def list_cases(self, *, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        if self.persistent:
            rows = await database.fetch(
                "select * from cases order by created_at desc limit $1 offset $2", limit, offset
            )
            return [dict(r) for r in rows]
        records = sorted(self._cases.values(), key=lambda c: c["created_at"], reverse=True)
        return records[offset : offset + limit]

    async def count_cases(self) -> int:
        if self.persistent:
            return int(await database.fetchval("select count(*) from cases") or 0)
        return len(self._cases)

    async def update_case_status(self, case_id: str, status: str) -> dict[str, Any] | None:
        if self.persistent:
            row = await database.fetchrow(
                "update cases set status = $2::case_status where id = $1 returning *",
                uuid.UUID(case_id),
                status,
            )
            return dict(row) if row else None
        if case_id in self._cases:
            self._cases[case_id]["status"] = status
            self._cases[case_id]["updated_at"] = _now()
            return dict(self._cases[case_id])
        return None

    # -- evidence ----------------------------------------------------------
    async def add_evidence(self, case_id: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not items:
            return []
        if self.persistent:
            stored: list[dict[str, Any]] = []
            async with database.transaction() as conn:
                for item in items:
                    row = await conn.fetchrow(
                        """
                        insert into case_evidence (
                            case_id, submitted_by, evidence_type, title, description, proof_category,
                            strength, admissibility, weight, document_ref, mime_type, file_size,
                            sha256, extracted_text, metadata
                        ) values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::jsonb)
                        returning *
                        """,
                        uuid.UUID(case_id),
                        item.get("submitted_by", "plaintiff"),
                        item.get("evidence_type", "document"),
                        item["title"],
                        item.get("description"),
                        item.get("proof_category"),
                        item.get("strength"),
                        item.get("admissibility"),
                        item.get("weight"),
                        item.get("document_ref"),
                        item.get("mime_type"),
                        item.get("file_size"),
                        item.get("sha256"),
                        item.get("extracted_text"),
                        item.get("metadata") or {},
                    )
                    stored.append(dict(row))
            return stored

        stored = []
        for item in items:
            record = {
                **item,
                "id": str(uuid.uuid4()),
                "case_id": case_id,
                "created_at": _now(),
            }
            stored.append(record)
        self._evidence.setdefault(case_id, []).extend(stored)
        return stored

    async def update_evidence_assessment(
        self, case_id: str, assessments: list[dict[str, Any]]
    ) -> None:
        """Persist the engine's evidentiary weighing back onto the evidence rows."""
        if not assessments:
            return
        if self.persistent:
            async with database.transaction() as conn:
                for item in assessments:
                    await conn.execute(
                        """
                        update case_evidence
                           set strength = coalesce($3, strength),
                               admissibility = coalesce($4, admissibility),
                               weight = coalesce($5, weight),
                               proof_category = coalesce($6, proof_category)
                         where case_id = $1 and title = $2
                        """,
                        uuid.UUID(case_id),
                        item["title"],
                        item.get("strength"),
                        item.get("admissibility"),
                        item.get("weight"),
                        item.get("proof_category"),
                    )
            return
        for record in self._evidence.get(case_id, []):
            for item in assessments:
                if record["title"] == item["title"]:
                    record.update(
                        {
                            k: item[k]
                            for k in ("strength", "admissibility", "weight", "proof_category")
                            if item.get(k) is not None
                        }
                    )

    async def list_evidence(self, case_id: str) -> list[dict[str, Any]]:
        if self.persistent:
            rows = await database.fetch(
                "select * from case_evidence where case_id = $1 order by created_at",
                uuid.UUID(case_id),
            )
            return [dict(r) for r in rows]
        return list(self._evidence.get(case_id, []))

    # -- judgments ---------------------------------------------------------
    async def next_version(self, case_id: str) -> int:
        if self.persistent:
            value = await database.fetchval(
                "select coalesce(max(version), 0) + 1 from judgments where case_id = $1",
                uuid.UUID(case_id),
            )
            return int(value or 1)
        return len(self._judgments.get(case_id, [])) + 1

    async def save_judgment(self, case_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        payload = {k: _jsonable(v) for k, v in payload.items()}
        version = payload.get("version") or await self.next_version(case_id)
        if self.persistent:
            row = await database.fetchrow(
                """
                insert into judgments (
                    case_id, version, status, language, model, preamble, facts, reasoning,
                    verdict, citations, evidence_analysis, markdown_ar, confidence,
                    grounding_score, warnings, raw_llm
                ) values ($1,$2,$3::judgment_status,$4,$5,$6::jsonb,$7::jsonb,$8::jsonb,$9::jsonb,
                          $10::jsonb,$11::jsonb,$12,$13,$14,$15::jsonb,$16::jsonb)
                returning *
                """,
                uuid.UUID(case_id),
                version,
                payload.get("status", "ai_generated"),
                payload.get("language", "ar"),
                payload.get("model"),
                payload.get("preamble") or {},
                payload.get("facts") or {},
                payload.get("reasoning") or {},
                payload.get("verdict") or {},
                payload.get("citations") or [],
                payload.get("evidence_analysis") or [],
                payload.get("markdown_ar"),
                payload.get("confidence"),
                payload.get("grounding_score"),
                payload.get("warnings") or [],
                payload.get("raw_llm") or {},
            )
            return dict(row)

        record = {
            **payload,
            "id": str(uuid.uuid4()),
            "case_id": case_id,
            "version": version,
            "status": payload.get("status", "ai_generated"),
            "created_at": _now(),
        }
        self._judgments.setdefault(case_id, []).append(record)
        return record

    async def get_judgment(self, judgment_id: str) -> dict[str, Any] | None:
        if self.persistent:
            row = await database.fetchrow(
                "select * from judgments where id = $1", uuid.UUID(judgment_id)
            )
            return dict(row) if row else None
        for records in self._judgments.values():
            for record in records:
                if record["id"] == judgment_id:
                    return record
        return None

    async def list_judgments(self, case_id: str) -> list[dict[str, Any]]:
        if self.persistent:
            rows = await database.fetch(
                "select * from judgments where case_id = $1 order by version desc",
                uuid.UUID(case_id),
            )
            return [dict(r) for r in rows]
        return sorted(self._judgments.get(case_id, []), key=lambda r: r["version"], reverse=True)

    # -- audit -------------------------------------------------------------
    async def audit(self, entity: str, action: str, entity_id: str | None = None,
                    actor: str | None = None, payload: dict[str, Any] | None = None) -> None:
        if not self.persistent:
            return
        try:
            await database.execute(
                "insert into audit_log (entity, entity_id, action, actor, payload) "
                "values ($1,$2,$3,$4,$5::jsonb)",
                entity,
                uuid.UUID(entity_id) if entity_id else None,
                action,
                actor,
                _jsonable(payload) or {},
            )
        except Exception as exc:
            logger.warning("audit write failed", extra={"error": str(exc)})


case_repository = CaseRepository()
