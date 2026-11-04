"""Tests for the role portals, the legal assistant, the workspace and drafting.

These run fully offline against the in-memory vector store and the deterministic
mock LLM, so they exercise the real retrieval, grounding, citation-validation,
permission and portal-assembly code paths.
"""
from __future__ import annotations

import httpx
import pytest

from app.core.permissions import Role
from app.db.user_repository import user_repository
from app.main import app

OWNER = "owner"
OWNER_PW = "Owner@Yemen2026"


@pytest.fixture
async def client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _make_token(client: httpx.AsyncClient, username: str, role: Role) -> str:
    await user_repository.create(username=username, password="Secret@12345", role=role)
    response = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": "Secret@12345"}
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


# --------------------------------------------------------------------------- #
# Portals
# --------------------------------------------------------------------------- #
async def test_portal_me_reflects_role(client: httpx.AsyncClient, seeded_store) -> None:
    judge = await _make_token(client, "judge_p", Role.JUDGE)
    lawyer = await _make_token(client, "lawyer_p", Role.LAWYER)

    r_judge = await client.get("/api/v1/portals/me", headers=_auth(judge))
    assert r_judge.status_code == 200
    assert r_judge.json()["portal"] == "judge"
    tool_keys = {t["key"] for t in r_judge.json()["tools"]}
    assert "sessions" in tool_keys and "draft" in tool_keys

    r_lawyer = await client.get("/api/v1/portals/me", headers=_auth(lawyer))
    assert r_lawyer.status_code == 200
    assert r_lawyer.json()["portal"] == "lawyer"
    assert {t["key"] for t in r_lawyer.json()["tools"]} >= {"contracts", "defences"}


async def test_portal_catalogue_lists_five(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/portals/catalogue")
    assert response.status_code == 200
    assert {p["portal"] for p in response.json()} == {
        "admin", "judge", "lawyer", "prosecution", "researcher",
    }


async def test_foreign_portal_is_forbidden(client: httpx.AsyncClient) -> None:
    # A viewer has no judge-portal tool permissions, so the judge portal is refused.
    viewer = await _make_token(client, "viewer_x", Role.VIEWER)
    response = await client.get("/api/v1/portals/judge", headers=_auth(viewer))
    assert response.status_code == 403


# --------------------------------------------------------------------------- #
# Workspace
# --------------------------------------------------------------------------- #
async def test_session_requires_schedule(client: httpx.AsyncClient) -> None:
    judge = await _make_token(client, "judge_w", Role.JUDGE)
    response = await client.post(
        "/api/v1/workspace",
        headers=_auth(judge),
        json={"kind": "session", "title": "جلسة بلا موعد"},
    )
    assert response.status_code == 422

    ok = await client.post(
        "/api/v1/workspace",
        headers=_auth(judge),
        json={
            "kind": "session",
            "title": "جلسة المرافعة",
            "scheduled_at": "2026-11-10T09:00:00Z",
        },
    )
    assert ok.status_code == 201
    assert ok.json()["kind"] == "session"
    assert ok.json()["kind_label_ar"] == "جلسة"


async def test_contract_is_private_to_author(client: httpx.AsyncClient) -> None:
    lawyer = await _make_token(client, "lawyer_priv", Role.LAWYER)
    judge = await _make_token(client, "judge_priv", Role.JUDGE)

    created = await client.post(
        "/api/v1/workspace",
        headers=_auth(lawyer),
        json={"kind": "contract", "title": "عقد إيجار"},
    )
    assert created.status_code == 201

    # The judge holds no contracts.draft permission at all.
    forbidden = await client.get(
        "/api/v1/workspace?kind=contract", headers=_auth(judge)
    )
    assert forbidden.status_code == 403

    own = await client.get("/api/v1/workspace?kind=contract", headers=_auth(lawyer))
    assert own.status_code == 200
    assert own.json()["total"] == 1


async def test_workspace_kind_requires_permission(client: httpx.AsyncClient) -> None:
    researcher = await _make_token(client, "researcher_w", Role.RESEARCHER)
    # A researcher may keep notes but not create a hearing session.
    denied = await client.post(
        "/api/v1/workspace",
        headers=_auth(researcher),
        json={
            "kind": "session",
            "title": "جلسة",
            "scheduled_at": "2026-11-10T09:00:00Z",
        },
    )
    assert denied.status_code == 403

    allowed = await client.post(
        "/api/v1/workspace",
        headers=_auth(researcher),
        json={"kind": "note", "title": "ملاحظة بحث"},
    )
    assert allowed.status_code == 201


# --------------------------------------------------------------------------- #
# Grounded drafting
# --------------------------------------------------------------------------- #
async def test_lawyer_drafts_contract_but_not_charge(
    client: httpx.AsyncClient, seeded_store
) -> None:
    lawyer = await _make_token(client, "lawyer_d", Role.LAWYER)

    contract = await client.post(
        "/api/v1/workspace/draft",
        headers=_auth(lawyer),
        json={"draft_type": "contract", "brief": "عقد بيع سيارة بثمن محدد وشرط غرامة تأخير"},
    )
    assert contract.status_code == 200, contract.text
    body = contract.json()
    assert body["draft_type_label_ar"] == "عقد"
    assert body["markdown"].strip()
    assert body["cited_articles"], "a grounded draft must cite retrieved articles"
    # Every citation must resolve to a retrieved article with a real law title.
    assert all(c["law_title"] for c in body["cited_articles"])

    charge = await client.post(
        "/api/v1/workspace/draft",
        headers=_auth(lawyer),
        json={"draft_type": "charge", "brief": "اتهام بالاستيلاء على مال الغير"},
    )
    assert charge.status_code == 403


async def test_judge_drafts_judgment_note(
    client: httpx.AsyncClient, seeded_store
) -> None:
    judge = await _make_token(client, "judge_d", Role.JUDGE)
    response = await client.post(
        "/api/v1/workspace/draft",
        headers=_auth(judge),
        json={"draft_type": "judgment_note", "brief": "نزاع على تنفيذ التزام تعاقدي"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["draft_type"] == "judgment_note"


async def test_prosecutor_drafts_charge(client: httpx.AsyncClient, seeded_store) -> None:
    prosecutor = await _make_token(client, "pros_d", Role.PROSECUTOR)
    response = await client.post(
        "/api/v1/workspace/draft",
        headers=_auth(prosecutor),
        json={"draft_type": "charge", "brief": "الاستيلاء على مال الغير بالاحتيال"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["draft_type"] == "charge"


async def test_draft_without_corpus_is_flagged(client: httpx.AsyncClient) -> None:
    # No seeded corpus: the draft must be flagged for review, never fabricated.
    lawyer = await _make_token(client, "lawyer_empty", Role.LAWYER)
    response = await client.post(
        "/api/v1/workspace/draft",
        headers=_auth(lawyer),
        json={"draft_type": "contract", "brief": "عقد بيع سيارة"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["needs_review"] is True
    assert body["cited_articles"] == []


# --------------------------------------------------------------------------- #
# Legal assistant
# --------------------------------------------------------------------------- #
async def test_assistant_grounds_answer_and_keeps_conversation(
    client: httpx.AsyncClient, seeded_store
) -> None:
    lawyer = await _make_token(client, "lawyer_a", Role.LAWYER)
    first = await client.post(
        "/api/v1/assistant/ask",
        headers=_auth(lawyer),
        json={"question": "ما حكم الإخلال بتنفيذ الالتزام التعاقدي؟"},
    )
    assert first.status_code == 200, first.text
    answer = first.json()
    assert answer["answer"].strip()
    assert answer["cited_articles"], "the assistant must cite retrieved statutes"
    conv_id = answer["conversation_id"]

    # A follow-up in the same conversation must be recorded, not start a new one.
    second = await client.post(
        "/api/v1/assistant/ask",
        headers=_auth(lawyer),
        json={"question": "وهل يشترط الإعذار؟", "conversation_id": conv_id},
    )
    assert second.status_code == 200
    assert second.json()["conversation_id"] == conv_id

    listing = await client.get("/api/v1/assistant/conversations", headers=_auth(lawyer))
    assert listing.status_code == 200
    assert listing.json()["total"] >= 1

    detail = await client.get(
        f"/api/v1/assistant/conversations/{conv_id}", headers=_auth(lawyer)
    )
    assert detail.status_code == 200
    roles = [m["role"] for m in detail.json()["messages"]]
    assert roles.count("user") == 2
    assert roles.count("assistant") == 2


# --------------------------------------------------------------------------- #
# Admin monitoring
# --------------------------------------------------------------------------- #
async def test_admin_overview_and_audit_require_permission(
    client: httpx.AsyncClient,
) -> None:
    user_repository.reset()
    from app.services.auth import auth_service

    await auth_service.ensure_owner()
    login = await client.post(
        "/api/v1/auth/login", json={"username": OWNER, "password": OWNER_PW}
    )
    owner = login.json()["access_token"]

    overview = await client.get("/api/v1/admin/overview", headers=_auth(owner))
    assert overview.status_code == 200
    body = overview.json()
    assert "users" in body and "corpus" in body

    # A viewer may not read the admin monitoring surface.
    viewer = await _make_token(client, "viewer_m", Role.VIEWER)
    denied = await client.get("/api/v1/admin/overview", headers=_auth(viewer))
    assert denied.status_code == 403


async def test_workspace_mutations_are_audited(client: httpx.AsyncClient) -> None:
    user_repository.reset()
    from app.services.auth import auth_service

    await auth_service.ensure_owner()
    login = await client.post(
        "/api/v1/auth/login", json={"username": OWNER, "password": OWNER_PW}
    )
    owner = login.json()["access_token"]

    created = await client.post(
        "/api/v1/workspace",
        headers=_auth(owner),
        json={"kind": "note", "title": "ملاحظة تدقيق"},
    )
    assert created.status_code == 201

    audit = await client.get("/api/v1/admin/audit?entity=workspace", headers=_auth(owner))
    assert audit.status_code == 200
    assert any(a["action"] == "created" for a in audit.json())


# --------------------------------------------------------------------------- #
# Static assets and PWA
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "path",
    [
        "/ui/landing.html",
        "/ui/index.html",
        "/ui/judge.html",
        "/ui/lawyer.html",
        "/ui/prosecution.html",
        "/ui/researcher.html",
        "/ui/assistant.html",
        "/ui/workspace.html",
        "/ui/install.html",
        "/ui/offline.html",
        "/ui/portal.css",
        "/ui/portal.js",
        "/ui/portal-app.js",
        "/ui/workspace.css",
        "/ui/workspace.js",
    ],
)
async def test_static_assets_are_served(client: httpx.AsyncClient, path: str) -> None:
    response = await client.get(path)
    assert response.status_code == 200, path


async def test_root_serves_the_landing_page(client: httpx.AsyncClient) -> None:
    response = await client.get("/")
    assert response.status_code == 200
    body = response.text
    # The public front door is the marketing/landing page, not the raw tool.
    assert "منصة الذكاء الاصطناعي القانوني" in body
    assert "/ui/workspace.html" in body
    assert 'id="installBtn"' in body


async def test_workspace_page_is_chat_first_and_grounded(client: httpx.AsyncClient) -> None:
    response = await client.get("/ui/workspace.html")
    assert response.status_code == 200
    body = response.text
    assert "مساحة العمل القانونية" in body
    # The workspace shell (and its grounding panel) is rendered by workspace.js.
    js = (await client.get("/ui/workspace.js")).text
    assert "/assistant/ask" in js
    assert "cited_articles" in js and "grounding_score" in js
    assert "المحادثات" in js and "السياق والأسانيد" in js


async def test_install_page_documents_android_flow(client: httpx.AsyncClient) -> None:
    response = await client.get("/ui/install.html")
    assert response.status_code == 200
    body = response.text
    assert "أندرويد" in body and "إضافة إلى الشاشة الرئيسية" in body
    assert "/ui/install.js" in body


async def test_pwa_manifest_and_worker_at_root(client: httpx.AsyncClient) -> None:
    manifest = await client.get("/manifest.json")
    assert manifest.status_code == 200
    assert manifest.json()["name"]
    assert manifest.json()["start_url"]

    worker = await client.get("/sw.js")
    assert worker.status_code == 200
    # The worker must be served with a root scope or it cannot cache the origin.
    assert worker.headers.get("service-worker-allowed") == "/"
