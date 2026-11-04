"""Platform backup and restore (النسخة الاحتياطية).

A snapshot captures the user-generated data — cases, evidence, judgments, users,
sections, assistant threads, workspace items and the audit trail — so an operator
can take a copy, move it between environments, or roll back a bad change. The
legal corpus is a versioned asset and is recorded by version only.

Two storage modes are supported transparently:

* **Postgres/Supabase** — reads and writes the real tables.
* **In-memory** — reads and writes the process dicts, so a developer can export
  and re-import a local session without a database.

Passwords are exported as PBKDF2 hashes (never plaintext) and only when
``include_credentials`` is set, so a backup can be shared for inspection without
leaking credentials.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from app.core.config import settings
from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.db import vector_store as vector_store_module
from app.db.assistant_repository import assistant_repository
from app.db.pool import database
from app.db.repositories import case_repository
from app.db.user_repository import section_repository, user_repository
from app.db.workspace_repository import workspace_repository
from app.schemas.backup import (
    BACKUP_FORMAT,
    BACKUP_VERSION,
    BackupCounts,
    BackupSnapshot,
    BackupSummary,
    RestoreResult,
)

logger = get_logger(__name__)

#: Collections a snapshot may carry, in restore order (parents before children).
_COLLECTIONS: tuple[str, ...] = (
    "sections",
    "users",
    "cases",
    "evidence",
    "judgments",
    "assistant_conversations",
    "assistant_messages",
    "workspace_items",
    "audit",
)


def _jsonable(value: Any) -> Any:
    """Make a value JSON-safe.

    Rows read from PostgreSQL arrive as ``asyncpg.Record`` (a mapping, not a
    ``dict``) and numeric columns as ``Decimal``, so both are normalised here.
    Without this, an export against a real database fails to serialise — the
    in-memory stores used by the tests never expose either type.
    """
    if isinstance(value, Mapping):
        return {key: _jsonable(item) for key, item in value.items()}
    # asyncpg.Record is neither a dict nor a Mapping, but is key/value-addressable
    # (keys() + __getitem__). dict() materialises it into a plain mapping.
    if not isinstance(value, str | bytes | bytearray) and hasattr(value, "keys"):
        try:
            items = dict(value)
        except (TypeError, ValueError):
            return value
        return {key: _jsonable(item) for key, item in items.items()}
    if isinstance(value, list | tuple | set):
        return [_jsonable(item) for item in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).decode("utf-8", "replace")
    return value


def _as_datetime(value: Any) -> Any:
    """Coerce an ISO-8601 string from a JSON snapshot back to a datetime.

    Snapshots store timestamps as ISO strings; asyncpg rejects a string for a
    ``timestamptz`` parameter, so a restore into PostgreSQL must convert them.
    ``None`` and unparseable values pass through as ``None``.
    """
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return value


class BackupService:
    """Export and restore the platform's user-generated data."""

    # -- export ------------------------------------------------------------
    async def summary(self) -> BackupSummary:
        counts = await self._counts()
        return BackupSummary(
            created_at=datetime.now(UTC),
            app_name=settings.app_name,
            database=settings.database_enabled,
            corpus_version=self._corpus_version(),
            counts=counts,
        )

    async def export(self, *, include_credentials: bool = False) -> BackupSnapshot:
        """Build a full snapshot of the current data set."""
        data: dict[str, Any] = {}
        data["sections"] = _jsonable(await self._dump_sections())
        data["users"] = _jsonable(await self._dump_users(include_credentials))
        cases = await self._dump_cases()
        data["cases"] = _jsonable(cases["cases"])
        data["evidence"] = _jsonable(cases["evidence"])
        data["judgments"] = _jsonable(cases["judgments"])
        data["audit"] = _jsonable(cases["audit"])
        assistant = await self._dump_assistant()
        data["assistant_conversations"] = _jsonable(assistant["conversations"])
        data["assistant_messages"] = _jsonable(assistant["messages"])
        data["workspace_items"] = _jsonable(await self._dump_workspace())

        counts = BackupCounts(
            sections=len(data["sections"]),
            users=len(data["users"]),
            cases=len(data["cases"]),
            evidence=len(data["evidence"]),
            judgments=len(data["judgments"]),
            audit=len(data["audit"]),
            assistant_conversations=len(data["assistant_conversations"]),
            assistant_messages=len(data["assistant_messages"]),
            workspace_items=len(data["workspace_items"]),
        )
        return BackupSnapshot(
            created_at=datetime.now(UTC),
            app_name=settings.app_name,
            database=settings.database_enabled,
            corpus_version=self._corpus_version(),
            includes_credentials=include_credentials,
            counts=counts,
            data=data,
        )

    def export_bytes(self, snapshot: BackupSnapshot) -> bytes:
        """Serialise a snapshot to UTF-8 JSON bytes for download."""
        return json.dumps(
            snapshot.model_dump(mode="json"), ensure_ascii=False, indent=2
        ).encode("utf-8")

    # -- import ------------------------------------------------------------
    async def restore(
        self,
        payload: dict[str, Any] | BackupSnapshot,
        *,
        replace: bool = False,
        dry_run: bool = False,
        include_credentials: bool = True,
    ) -> RestoreResult:
        """Restore a snapshot, optionally clearing existing data first.

        ``dry_run`` validates the payload and reports what *would* be written
        without touching any store, so an operator can inspect before committing.
        """
        snapshot = self._coerce(payload)
        warnings: list[str] = []
        if snapshot.format != BACKUP_FORMAT:
            raise ValidationError("الملف ليس نسخة احتياطية صالحة لهذه المنصة")
        if snapshot.version > BACKUP_VERSION:
            raise ValidationError(
                "إصدار النسخة الاحتياطية أحدث من إصدار المنصة الحالي"
            )
        data = snapshot.data or {}
        unknown = set(data) - set(_COLLECTIONS)
        if unknown:
            warnings.append(f"مجموعات غير معروفة تم تجاهلها: {sorted(unknown)}")

        users = list(data.get("users") or [])
        if users and not include_credentials:
            warnings.append("تم تجاهل بيانات الدخول (كلمات المرور) بحسب الإعداد")
        if users and not any("password_hash" in u for u in users):
            warnings.append(
                "النسخة لا تحوي بصمات كلمات المرور؛ لن يستطيع المستخدمون المستورَدون "
                "تسجيل الدخول حتى تُعاد تعيين كلمات مرورهم"
            )

        if dry_run:
            return RestoreResult(
                restored=self._count_payload(data),
                replaced=replace,
                dry_run=True,
                warnings=warnings,
            )

        if replace:
            await self._clear_all()
        if users:
            await self._restore_users(users, include_credentials)
        if data.get("sections"):
            await self._restore_sections(list(data["sections"]))
        await self._restore_cases(data)
        await self._restore_assistant(data)
        if data.get("workspace_items"):
            await self._restore_workspace(list(data["workspace_items"]))
        if data.get("audit"):
            await self._restore_audit(list(data["audit"]))

        await case_repository.audit(
            "backup",
            "restored",
            None,
            actor="system",
            payload={"replace": replace, "counts": self._count_payload(data).model_dump()},
        )
        return RestoreResult(
            restored=self._count_payload(data),
            replaced=replace,
            dry_run=False,
            warnings=warnings,
        )

    # -- internals: counts / corpus ---------------------------------------
    async def _counts(self) -> BackupCounts:
        cases = await self._dump_cases()
        assistant = await self._dump_assistant()
        return BackupCounts(
            users=await user_repository.count(),
            sections=len(await section_repository.list_sections()),
            cases=len(cases["cases"]),
            evidence=len(cases["evidence"]),
            judgments=len(cases["judgments"]),
            audit=len(cases["audit"]),
            assistant_conversations=len(assistant["conversations"]),
            assistant_messages=len(assistant["messages"]),
            workspace_items=len(await self._dump_workspace()),
        )

    def _count_payload(self, data: dict[str, Any]) -> BackupCounts:
        return BackupCounts(
            users=len(data.get("users") or []),
            sections=len(data.get("sections") or []),
            cases=len(data.get("cases") or []),
            evidence=len(data.get("evidence") or []),
            judgments=len(data.get("judgments") or []),
            audit=len(data.get("audit") or []),
            assistant_conversations=len(data.get("assistant_conversations") or []),
            assistant_messages=len(data.get("assistant_messages") or []),
            workspace_items=len(data.get("workspace_items") or []),
        )

    def _corpus_version(self) -> str | None:
        store = getattr(vector_store_module, "vector_store", None)
        docs = getattr(store, "documents", None)
        if docs:
            return f"{len(docs)} وثيقة"
        return None

    @staticmethod
    def _coerce(payload: dict[str, Any] | BackupSnapshot) -> BackupSnapshot:
        if isinstance(payload, BackupSnapshot):
            return payload
        try:
            return BackupSnapshot.model_validate(payload)
        except Exception as exc:  # surface a clear message
            raise ValidationError("تعذّر قراءة ملف النسخة الاحتياطية", details=str(exc)) from exc

    # -- dump helpers ------------------------------------------------------
    async def _dump_sections(self) -> list[dict[str, Any]]:
        return await section_repository.list_sections()

    async def _dump_users(self, include_credentials: bool) -> list[dict[str, Any]]:
        if user_repository.persistent:
            rows = await database.fetch(
                "select id, username, email, full_name, role, is_active, permissions, "
                "last_login_at, created_by, created_at, updated_at, password_hash "
                "from platform_users order by created_at asc"
            )
            users = [dict(r) for r in rows]
        else:
            users = [dict(u) for u in user_repository._users.values()]
        if not include_credentials:
            users = [{k: v for k, v in u.items() if k != "password_hash"} for u in users]
        return users

    async def _dump_cases(self) -> dict[str, list[dict[str, Any]]]:
        if case_repository.persistent:
            cases = [dict(r) for r in await database.fetch("select * from cases order by created_at")]
            evidence = [
                dict(r) for r in await database.fetch("select * from case_evidence order by created_at")
            ]
            judgments = [
                dict(r) for r in await database.fetch("select * from judgments order by created_at")
            ]
            audit = [
                dict(r)
                for r in await database.fetch(
                    "select entity, entity_id, action, actor, payload, created_at "
                    "from audit_log order by created_at"
                )
            ]
            return {"cases": cases, "evidence": evidence, "judgments": judgments, "audit": audit}
        cases = [dict(c) for c in case_repository._cases.values()]
        evidence = [e for items in case_repository._evidence.values() for e in items]
        judgments = [j for items in case_repository._judgments.values() for j in items]
        audit = [dict(a) for a in case_repository._audit]
        return {"cases": cases, "evidence": evidence, "judgments": judgments, "audit": audit}

    async def _dump_assistant(self) -> dict[str, list[dict[str, Any]]]:
        if assistant_repository.persistent:
            conversations = [
                dict(r)
                for r in await database.fetch(
                    "select * from assistant_conversations order by created_at"
                )
            ]
            messages = [
                dict(r)
                for r in await database.fetch("select * from assistant_messages order by created_at")
            ]
            return {"conversations": conversations, "messages": messages}
        conversations = [dict(c) for c in assistant_repository._conversations.values()]
        messages = [m for items in assistant_repository._messages.values() for m in items]
        return {"conversations": conversations, "messages": messages}

    async def _dump_workspace(self) -> list[dict[str, Any]]:
        if workspace_repository.persistent:
            return [
                dict(r)
                for r in await database.fetch(
                    "select * from portal_workspace_items order by created_at"
                )
            ]
        return [dict(i) for i in workspace_repository._items.values()]

    # -- restore helpers ---------------------------------------------------
    async def _clear_all(self) -> None:
        """Delete every user-generated record before a replace-restore."""
        if settings.database_enabled:
            # Children first to respect foreign keys.
            for table in (
                "portal_workspace_items",
                "assistant_messages",
                "assistant_conversations",
                "judgments",
                "case_evidence",
                "cases",
                "platform_sections",
                "platform_users",
                "audit_log",
            ):
                await database.execute(f"delete from {table}")
            return
        user_repository.reset()
        section_repository.reset()
        workspace_repository.reset()
        assistant_repository.reset()
        case_repository._cases.clear()
        case_repository._evidence.clear()
        case_repository._judgments.clear()
        case_repository.reset_audit()

    async def _restore_users(self, users: list[dict[str, Any]], include_credentials: bool) -> None:
        for raw in users:
            user = dict(raw)
            user_id = str(user.get("id") or uuid.uuid4())
            username = user.get("username")
            if not username:
                continue
            password_hash = user.get("password_hash")
            if not include_credentials or not password_hash:
                # A login-less account is unusable; keep it inactive so an
                # operator can reset the password deliberately.
                password_hash = None
            record = {
                "id": user_id,
                "username": username,
                "email": user.get("email"),
                "full_name": user.get("full_name") or "",
                "role": user.get("role") or "viewer",
                "is_active": bool(user.get("is_active", True)) and password_hash is not None,
                "permissions": list(user.get("permissions") or []),
                "password_hash": password_hash,
                "last_login_at": user.get("last_login_at"),
                "created_by": user.get("created_by"),
                "created_at": user.get("created_at") or datetime.now(UTC),
                "updated_at": user.get("updated_at") or datetime.now(UTC),
            }
            if user_repository.persistent:
                await database.execute(
                    "insert into platform_users "
                    "(id, username, email, full_name, password_hash, role, is_active, permissions, "
                    " last_login_at, created_by, created_at, updated_at) "
                    "values ($1,$2,$3,$4,$5,$6::user_role,$7,$8::jsonb,$9,$10,$11,$12) "
                    "on conflict (username) do nothing",
                    uuid.UUID(user_id),
                    record["username"],
                    record["email"],
                    record["full_name"],
                    record["password_hash"],
                    record["role"],
                    record["is_active"],
                    _jsonable(record["permissions"]),
                    _as_datetime(record["last_login_at"]),
                    uuid.UUID(str(record["created_by"])) if record["created_by"] else None,
                    _as_datetime(record["created_at"]),
                    _as_datetime(record["updated_at"]),
                )
            else:
                if record["username"] in {u["username"] for u in user_repository._users.values()}:
                    continue
                user_repository._users[user_id] = record

    async def _restore_sections(self, sections: list[dict[str, Any]]) -> None:
        for raw in sections:
            section = dict(raw)
            section_id = str(section.get("id") or uuid.uuid4())
            if not section.get("key"):
                continue
            record = {
                "id": section_id,
                "key": section["key"],
                "title_ar": section.get("title_ar") or section["key"],
                "description": section.get("description"),
                "icon": section.get("icon"),
                "sort_order": int(section.get("sort_order") or 0),
                "is_active": bool(section.get("is_active", True)),
                "created_at": section.get("created_at") or datetime.now(UTC),
                "updated_at": section.get("updated_at") or datetime.now(UTC),
            }
            if section_repository.persistent:
                await database.execute(
                    "insert into platform_sections "
                    "(id, key, title_ar, description, icon, sort_order, is_active, created_at, updated_at) "
                    "values ($1,$2,$3,$4,$5,$6,$7,$8,$9) on conflict (key) do nothing",
                    uuid.UUID(section_id),
                    record["key"],
                    record["title_ar"],
                    record["description"],
                    record["icon"],
                    record["sort_order"],
                    record["is_active"],
                    _as_datetime(record["created_at"]),
                    _as_datetime(record["updated_at"]),
                )
            else:
                if record["key"] in {s["key"] for s in section_repository._sections.values()}:
                    continue
                section_repository._sections[section_id] = record

    async def _restore_cases(self, data: dict[str, Any]) -> None:
        for raw in data.get("cases") or []:
            case = dict(raw)
            case_id = str(case.get("id") or uuid.uuid4())
            case["id"] = case_id
            if case_repository.persistent:
                await self._insert_case_pg(case)
            else:
                case_repository._cases[case_id] = case
        for raw in data.get("evidence") or []:
            item = dict(raw)
            item["id"] = str(item.get("id") or uuid.uuid4())
            case_id = str(item.get("case_id")) if item.get("case_id") else None
            if not case_id:
                continue
            if case_repository.persistent:
                await self._insert_evidence_pg(item)
            else:
                case_repository._evidence.setdefault(case_id, []).append(item)
        for raw in data.get("judgments") or []:
            item = dict(raw)
            item["id"] = str(item.get("id") or uuid.uuid4())
            case_id = str(item.get("case_id")) if item.get("case_id") else None
            if not case_id:
                continue
            if case_repository.persistent:
                await self._insert_judgment_pg(item)
            else:
                case_repository._judgments.setdefault(case_id, []).append(item)

    async def _insert_case_pg(self, case: dict[str, Any]) -> None:
        await database.execute(
            "insert into cases (id, case_number, case_type, court_name, circuit, judge_name, "
            "jurisdiction, status, plaintiff_name, plaintiff_details, defendant_name, "
            "defendant_details, plaintiff_claims, defendant_responses, subject_matter, claim_value, "
            "currency, facts_summary, extra, created_at, updated_at) "
            "values ($1,$2,$3,$4,$5,$6,$7,$8::case_status,$9,$10::jsonb,$11,$12::jsonb,$13::jsonb,"
            "$14::jsonb,$15,$16,$17,$18,$19::jsonb,$20,$21) on conflict (id) do nothing",
            uuid.UUID(case["id"]),
            case.get("case_number"),
            case.get("case_type") or settings.default_case_type,
            case.get("court_name") or settings.default_court_name,
            case.get("circuit"),
            case.get("judge_name"),
            case.get("jurisdiction") or settings.jurisdiction,
            case.get("status") or "submitted",
            case.get("plaintiff_name") or "",
            _jsonable(case.get("plaintiff_details") or {}),
            case.get("defendant_name") or "",
            _jsonable(case.get("defendant_details") or {}),
            _jsonable(case.get("plaintiff_claims") or []),
            _jsonable(case.get("defendant_responses") or []),
            case.get("subject_matter"),
            case.get("claim_value"),
            case.get("currency") or "YER",
            case.get("facts_summary"),
            _jsonable(case.get("extra") or {}),
            _as_datetime(case.get("created_at")) or datetime.now(UTC),
            _as_datetime(case.get("updated_at")) or datetime.now(UTC),
        )

    async def _insert_evidence_pg(self, item: dict[str, Any]) -> None:
        await database.execute(
            "insert into case_evidence (id, case_id, submitted_by, evidence_type, title, description, "
            "proof_category, strength, admissibility, weight, document_ref, mime_type, file_size, "
            "sha256, extracted_text, metadata, created_at) "
            "values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16::jsonb,$17) "
            "on conflict (id) do nothing",
            uuid.UUID(item["id"]),
            uuid.UUID(str(item["case_id"])),
            item.get("submitted_by") or "plaintiff",
            item.get("evidence_type") or "document",
            item.get("title") or "دليل",
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
            _jsonable(item.get("metadata") or {}),
            _as_datetime(item.get("created_at")) or datetime.now(UTC),
        )

    async def _insert_judgment_pg(self, item: dict[str, Any]) -> None:
        await database.execute(
            "insert into judgments (id, case_id, version, status, language, model, preamble, facts, "
            "reasoning, verdict, citations, evidence_analysis, markdown_ar, confidence, "
            "grounding_score, warnings, raw_llm, created_at) "
            "values ($1,$2,$3,$4::judgment_status,$5,$6,$7::jsonb,$8::jsonb,$9::jsonb,$10::jsonb,"
            "$11::jsonb,$12::jsonb,$13,$14,$15,$16::jsonb,$17::jsonb,$18) "
            "on conflict (case_id, version) do nothing",
            uuid.UUID(item["id"]),
            uuid.UUID(str(item["case_id"])),
            int(item.get("version") or 1),
            item.get("status") or "ai_generated",
            item.get("language") or "ar",
            item.get("model"),
            _jsonable(item.get("preamble") or {}),
            _jsonable(item.get("facts") or {}),
            _jsonable(item.get("reasoning") or {}),
            _jsonable(item.get("verdict") or {}),
            _jsonable(item.get("citations") or []),
            _jsonable(item.get("evidence_analysis") or []),
            item.get("markdown_ar"),
            item.get("confidence"),
            item.get("grounding_score"),
            _jsonable(item.get("warnings") or []),
            _jsonable(item.get("raw_llm") or {}),
            _as_datetime(item.get("created_at")) or datetime.now(UTC),
        )

    async def _restore_assistant(self, data: dict[str, Any]) -> None:
        conversations = list(data.get("assistant_conversations") or [])
        for raw in conversations:
            conv = dict(raw)
            conv_id = str(conv.get("id") or uuid.uuid4())
            conv["id"] = conv_id
            if assistant_repository.persistent:
                await database.execute(
                    "insert into assistant_conversations (id, user_id, title, case_id, case_type, "
                    "is_archived, created_at, updated_at) "
                    "values ($1,$2,$3,$4,$5,$6,$7,$8) on conflict (id) do nothing",
                    uuid.UUID(conv_id),
                    uuid.UUID(str(conv["user_id"])),
                    conv.get("title") or "محادثة جديدة",
                    uuid.UUID(str(conv["case_id"])) if conv.get("case_id") else None,
                    conv.get("case_type"),
                    bool(conv.get("is_archived", False)),
                    _as_datetime(conv.get("created_at")) or datetime.now(UTC),
                    _as_datetime(conv.get("updated_at")) or datetime.now(UTC),
                )
            else:
                assistant_repository._conversations[conv_id] = conv
                assistant_repository._messages.setdefault(conv_id, [])
        for raw in data.get("assistant_messages") or []:
            msg = dict(raw)
            msg_id = str(msg.get("id") or uuid.uuid4())
            conv_id = str(msg.get("conversation_id")) if msg.get("conversation_id") else None
            if not conv_id:
                continue
            msg["id"] = msg_id
            if assistant_repository.persistent:
                await database.execute(
                    "insert into assistant_messages (id, conversation_id, role, content, citations, "
                    "grounding_score, model, provider, needs_review, created_at) "
                    "values ($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9,$10) on conflict (id) do nothing",
                    uuid.UUID(msg_id),
                    uuid.UUID(conv_id),
                    msg.get("role") or "user",
                    msg.get("content") or "",
                    _jsonable(msg.get("citations") or []),
                    msg.get("grounding_score"),
                    msg.get("model"),
                    msg.get("provider"),
                    bool(msg.get("needs_review", False)),
                    _as_datetime(msg.get("created_at")) or datetime.now(UTC),
                )
            else:
                assistant_repository._messages.setdefault(conv_id, []).append(msg)

    async def _restore_workspace(self, items: list[dict[str, Any]]) -> None:
        for raw in items:
            item = dict(raw)
            item_id = str(item.get("id") or uuid.uuid4())
            item["id"] = item_id
            if workspace_repository.persistent:
                await database.execute(
                    "insert into portal_workspace_items (id, owner_id, kind, title, case_id, case_type, "
                    "status, scheduled_at, body, payload, created_at, updated_at) "
                    "values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11,$12) "
                    "on conflict (id) do nothing",
                    uuid.UUID(item_id),
                    uuid.UUID(str(item["owner_id"])),
                    item.get("kind") or "memo",
                    item.get("title") or "عنصر",
                    uuid.UUID(str(item["case_id"])) if item.get("case_id") else None,
                    item.get("case_type"),
                    item.get("status") or "draft",
                    _as_datetime(item.get("scheduled_at")),
                    item.get("body"),
                    _jsonable(item.get("payload") or {}),
                    _as_datetime(item.get("created_at")) or datetime.now(UTC),
                    _as_datetime(item.get("updated_at")) or datetime.now(UTC),
                )
            else:
                workspace_repository._items[item_id] = item

    async def _restore_audit(self, entries: list[dict[str, Any]]) -> None:
        for raw in entries:
            entry = dict(raw)
            if case_repository.persistent:
                await database.execute(
                    "insert into audit_log (entity, entity_id, action, actor, payload, created_at) "
                    "values ($1,$2,$3,$4,$5::jsonb,$6)",
                    entry.get("entity") or "unknown",
                    uuid.UUID(str(entry["entity_id"])) if entry.get("entity_id") else None,
                    entry.get("action") or "restored",
                    entry.get("actor"),
                    _jsonable(entry.get("payload") or {}),
                    _as_datetime(entry.get("created_at")) or datetime.now(UTC),
                )
            else:
                case_repository._audit.append(entry)
        if not case_repository.persistent and len(case_repository._audit) > case_repository._audit_cap:
            del case_repository._audit[: len(case_repository._audit) - case_repository._audit_cap]


backup_service = BackupService()
