"""FastAPI dependency wiring."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Header

from app.core.errors import ForbiddenError, UnauthorizedError
from app.db.repositories import CaseRepository, case_repository
from app.db.user_repository import (
    SectionRepository,
    UserRepository,
    section_repository,
    user_repository,
)
from app.services.admin import AdminService, admin_service
from app.services.assistant import AssistantService, assistant_service
from app.services.auth import AuthService, auth_service
from app.services.backup import BackupService, backup_service
from app.services.drafting import DraftingService, drafting_service
from app.services.evidence import EvidenceService, evidence_service
from app.services.ingestion import IngestionService, ingestion_service
from app.services.judgment import JudgmentService, judgment_service
from app.services.n8n import N8NClient, n8n_client
from app.services.pipeline import CasePipeline, case_pipeline
from app.services.portal import PortalService, portal_service
from app.services.retrieval import RetrievalService, retrieval_service
from app.services.workspace import WorkspaceService, workspace_service


def get_ingestion_service() -> IngestionService:
    return ingestion_service


def get_retrieval_service() -> RetrievalService:
    return retrieval_service


def get_evidence_service() -> EvidenceService:
    return evidence_service


def get_judgment_service() -> JudgmentService:
    return judgment_service


def get_case_repository() -> CaseRepository:
    return case_repository


def get_pipeline() -> CasePipeline:
    return case_pipeline


def get_n8n_client() -> N8NClient:
    return n8n_client


def get_auth_service() -> AuthService:
    return auth_service


def get_admin_service() -> AdminService:
    return admin_service


def get_user_repository() -> UserRepository:
    return user_repository


def get_section_repository() -> SectionRepository:
    return section_repository


def get_assistant_service() -> AssistantService:
    return assistant_service


def get_portal_service() -> PortalService:
    return portal_service


def get_workspace_service() -> WorkspaceService:
    return workspace_service


def get_drafting_service() -> DraftingService:
    return drafting_service


def get_backup_service() -> BackupService:
    return backup_service


async def get_current_user(
    auth: Annotated[AuthService, Depends(get_auth_service)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Resolve the caller from the ``Authorization: Bearer <token>`` header."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedError("يتطلب هذا الإجراء تسجيل الدخول")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise UnauthorizedError("يتطلب هذا الإجراء تسجيل الدخول")
    return await auth.user_from_token(token)


CurrentUserDep = Annotated[dict[str, Any], Depends(get_current_user)]


def require_permission(permission: str):
    """Build a dependency that enforces a single permission."""

    async def _guard(user: CurrentUserDep) -> dict[str, Any]:
        auth_service.require_permission(user, permission)
        return user

    return _guard


def require_owner(user: CurrentUserDep) -> dict[str, Any]:
    """Only the platform owner may proceed."""
    from app.core.permissions import Role

    if Role(user["role"]) is not Role.OWNER:
        raise ForbiddenError("هذه العملية مقصورة على مالك المنصة")
    return user


IngestionDep = Annotated[IngestionService, Depends(get_ingestion_service)]
RetrievalDep = Annotated[RetrievalService, Depends(get_retrieval_service)]
EvidenceDep = Annotated[EvidenceService, Depends(get_evidence_service)]
JudgmentDep = Annotated[JudgmentService, Depends(get_judgment_service)]
CaseRepoDep = Annotated[CaseRepository, Depends(get_case_repository)]
PipelineDep = Annotated[CasePipeline, Depends(get_pipeline)]
N8NDep = Annotated[N8NClient, Depends(get_n8n_client)]
AuthDep = Annotated[AuthService, Depends(get_auth_service)]
AdminDep = Annotated[AdminService, Depends(get_admin_service)]
UserRepoDep = Annotated[UserRepository, Depends(get_user_repository)]
SectionRepoDep = Annotated[SectionRepository, Depends(get_section_repository)]
AssistantDep = Annotated[AssistantService, Depends(get_assistant_service)]
PortalDep = Annotated[PortalService, Depends(get_portal_service)]
WorkspaceDep = Annotated[WorkspaceService, Depends(get_workspace_service)]
DraftingDep = Annotated[DraftingService, Depends(get_drafting_service)]
BackupDep = Annotated[BackupService, Depends(get_backup_service)]
OwnerDep = Annotated[dict[str, Any], Depends(require_owner)]
