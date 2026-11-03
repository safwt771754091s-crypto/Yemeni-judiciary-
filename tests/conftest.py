"""Shared pytest fixtures.

The suite runs fully offline: no database and no hosted LLM are required. The
in-memory vector store and the deterministic mock LLM provider are used, which
means these tests exercise the real chunking, retrieval, evidence-weighing,
citation-validation and markdown-rendering code paths.
"""
from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# The suite is hermetic: it always exercises the offline code paths (in-memory
# vector store + deterministic mock LLM), regardless of any .env on the machine.
# Set before app.core.config is imported for the first time.
os.environ["SUPABASE_DB_URL"] = ""
os.environ["EMBEDDING_PROVIDER"] = "hash"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["N8N_ENABLED"] = "false"
os.environ["APP_ENV"] = "test"

# The tests speak only to loopback. A connection that slips through to a real
# host must fail in seconds, not block until the OS gives up and the run times
# out. The loopback n8n receiver sets its own timeouts, so a small default is
# safe here.
socket.setdefaulttimeout(10.0)

from app.db.repositories import case_repository
from app.db.user_repository import section_repository, user_repository
from app.db.vector_store import legal_document_admin_repository, reset_vector_store
from app.db.workspace_repository import workspace_repository
from app.schemas.legal import IngestTextRequest, LegalDocumentCreate
from app.services.ingestion import ingestion_service

SAMPLE = ROOT / "app/data/samples/yemeni_laws_excerpt.json"


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
async def clean_state():
    """Fresh in-memory stores for every test."""
    reset_vector_store()
    case_repository._cases.clear()
    case_repository._evidence.clear()
    case_repository._judgments.clear()
    case_repository._audit.clear()
    user_repository.reset()
    section_repository.reset()
    legal_document_admin_repository.reset()
    workspace_repository.reset()
    yield


@pytest.fixture
async def seeded_store():
    """Load the bundled Yemeni corpus into the (in-memory) vector store."""
    payload = json.loads(SAMPLE.read_text(encoding="utf-8"))
    for entry in payload["documents"]:
        await ingestion_service.ingest_text(
            IngestTextRequest(
                document=LegalDocumentCreate(**entry["document"]),
                text=entry["text"],
            )
        )
    return ingestion_service


@pytest.fixture
def sample_case_payload() -> dict:
    return {
        "case_number": "1445/2026",
        "case_type": "مدني",
        "court_name": "المحكمة الابتدائية - صنعاء",
        "judge_name": "القاضي/ محمد الحميري",
        "subject_matter": "المطالبة بتعويض عن الإخلال بعقد بيع سيارة",
        "claim_value": 3500000,
        "currency": "YER",
        "facts_summary": (
            "اتفق المدعي والمدعى عليه على بيع سيارة بثمن محدد، وسلّم المدعي السيارة "
            "وتأخر المدعى عليه عن سداد المبلغ المتفق عليه دون سبب مشروع."
        ),
        "plaintiff": {"name": "أحمد عبدالله ناصر", "role": "plaintiff", "address": "صنعاء"},
        "defendant": {"name": "صالح محمد المقطري", "role": "defendant", "address": "صنعاء"},
        "plaintiff_claims": [
            {
                "title": "إلزام المدعى عليه بأداء الثمن",
                "description": "إلزام المدعى عليه بسداد الثمن المتفق عليه وقدره ثلاثة ملايين وخمسمائة ألف ريال",
                "amount": 3500000,
                "currency": "YER",
                "relief_sought": "الحكم بإلزام المدعى عليه بأداء الثمن",
                "legal_basis": ["المادة 146", "المادة 147"],
            },
            {
                "title": "التعويض عن التأخر",
                "description": "التعويض عن الضرر اللاحق بالمدعي جراء التأخر في السداد",
                "amount": 200000,
                "currency": "YER",
                "legal_basis": ["المادة 191"],
            },
        ],
        "defendant_responses": [
            {
                "claim_ref": "إلزام المدعى عليه بأداء الثمن",
                "response_type": "denial",
                "description": "ينكر المدعى عليه استلام السيارة بحالة جيدة ويدّعي وجود عيوب خفية",
                "legal_basis": ["المادة 15"],
            }
        ],
        "evidence": [
            {
                "id": "ev-1",
                "submitted_by": "plaintiff",
                "evidence_type": "contract",
                "title": "عقد البيع الابتدائي",
                "description": "عقد بيع موقّع من الطرفين يحدد الثمن وطريقة السداد",
                "document_ref": "storage://evidence/contract_1445.pdf",
                "extracted_text": "عقد بيع سيارة بثمن ثلاثة ملايين وخمسمائة ألف ريال، يلتزم المشتري بالسداد خلال ثلاثين يوماً.",
            },
            {
                "id": "ev-2",
                "submitted_by": "defendant",
                "evidence_type": "digital",
                "title": "محادثات واتساب",
                "description": "محادثات تطبيق واتساب بين الطرفين",
                "document_ref": "storage://evidence/chat_1445.txt",
            },
        ],
        "generate_judgment": True,
        "language": "ar",
        "max_retrieved_articles": 10,
    }
