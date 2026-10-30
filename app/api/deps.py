"""FastAPI dependency wiring."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.db.repositories import CaseRepository, case_repository
from app.services.evidence import EvidenceService, evidence_service
from app.services.ingestion import IngestionService, ingestion_service
from app.services.judgment import JudgmentService, judgment_service
from app.services.n8n import N8NClient, n8n_client
from app.services.pipeline import CasePipeline, case_pipeline
from app.services.retrieval import RetrievalService, retrieval_service


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


IngestionDep = Annotated[IngestionService, Depends(get_ingestion_service)]
RetrievalDep = Annotated[RetrievalService, Depends(get_retrieval_service)]
EvidenceDep = Annotated[EvidenceService, Depends(get_evidence_service)]
JudgmentDep = Annotated[JudgmentService, Depends(get_judgment_service)]
CaseRepoDep = Annotated[CaseRepository, Depends(get_case_repository)]
PipelineDep = Annotated[CasePipeline, Depends(get_pipeline)]
N8NDep = Annotated[N8NClient, Depends(get_n8n_client)]
