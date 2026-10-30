"""Chat LLM providers with a strict JSON contract.

Providers
---------
* ``MockLLMProvider`` - deterministic, offline. It never fabricates statute
  text: it composes its answers purely from the RETRIEVED_STATUTES block it is
  handed, which makes the end-to-end pipeline verifiable in CI and demos.
* ``OpenAIProvider`` / ``OpenAICompatibleProvider`` - OpenAI Chat Completions
  with ``response_format={"type": "json_object"}`` and temperature 0.
* ``AnthropicProvider`` - Messages API, JSON enforced by the prompt plus a
  tolerant parser.

Every provider returns a ``Completion`` carrying the raw text, the parsed JSON
(when available) and token accounting.
"""
from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.config import settings
from app.core.errors import LLMError
from app.core.logging import get_logger

logger = get_logger(__name__)

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)


@dataclass(slots=True)
class ChatMessage:
    role: str
    content: str


@dataclass(slots=True)
class Completion:
    text: str
    data: dict[str, Any] | None = None
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    provider: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class LLMProvider(Protocol):
    name: str
    model: str

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        json_mode: bool = True,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> Completion: ...


def parse_json_response(text: str) -> dict[str, Any] | None:
    """Best-effort extraction of a JSON object from an LLM answer."""
    if not text:
        return None
    candidates: list[str] = []
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        candidates.append(stripped)
    candidates.extend(_JSON_BLOCK.findall(text))
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return {"items": parsed}
    return None


# ---------------------------------------------------------------------------
# Offline deterministic provider
# ---------------------------------------------------------------------------
class MockLLMProvider:
    """Produces schema-valid, citation-grounded answers without any network call.

    The provider inspects the prompt for the structured blocks emitted by
    ``app.llm.prompts`` and answers with content derived exclusively from them,
    honouring the same anti-hallucination contract as the hosted models.
    """

    name = "mock"

    def __init__(self, model: str | None = None) -> None:
        self.model = model or "mock-legal-reasoner"

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        json_mode: bool = True,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> Completion:
        prompt = "\n\n".join(m.content for m in messages)
        articles = _parse_retrieved_articles(prompt)
        data = self._dispatch(system, prompt, articles)
        text = json.dumps(data, ensure_ascii=False)
        return Completion(
            text=text,
            data=data,
            model=self.model,
            provider=self.name,
            prompt_tokens=len(prompt) // 4,
            completion_tokens=len(text) // 4,
        )

    @staticmethod
    def _dispatch(system: str, prompt: str, articles: list[dict[str, Any]]) -> dict[str, Any]:
        task = _detect_task(system)
        if task == "query_expansion":
            return {"queries": _mock_queries(prompt)}
        if task == "evidence_analysis":
            return _mock_evidence(prompt, articles)
        if task == "judgment_draft":
            return _mock_judgment(prompt, articles)
        if task == "legal_reasoning":
            return _mock_reasoning(prompt, articles)
        return {"answer": "لا يمكن إصدار إجابة دون نصوص قانونية مسترجعة."}


MOCK_TASK_MARKERS: list[tuple[str, str]] = [
    ("judgment_draft", "مسودات الأحكام"),
    ("evidence_analysis", "تقييم الأدلة"),
    ("legal_reasoning", "منهجية التحليل الإلزامية"),
    ("query_expansion", "استعلامات البحث"),
]


def _detect_task(system: str) -> str:
    """Identify which chain is calling, using unambiguous prompt markers."""
    for task, marker in MOCK_TASK_MARKERS:
        if marker in system:
            return task
    return "unknown"


def _parse_retrieved_articles(prompt: str) -> list[dict[str, Any]]:
    """Recover the retrieved statute block from the rendered prompt."""
    articles: list[dict[str, Any]] = []
    pattern = re.compile(
        r"^\[(\d+)\]\s+(?P<label>.+?)\s+من\s+(?P<law>.+)$", re.MULTILINE
    )
    content_pattern = re.compile(r"^\s*النص:\s*(?P<text>.+)$", re.MULTILINE)
    positions = [(m.start(), m) for m in pattern.finditer(prompt)]
    contents = [(m.start(), m.group("text").strip()) for m in content_pattern.finditer(prompt)]
    for pos, match in positions:
        body = ""
        for cpos, ctext in contents:
            if cpos > pos:
                body = ctext
                break
        articles.append(
            {
                "article_label": match.group("label").strip(),
                "law_title": match.group("law").strip(),
                "content": body,
            }
        )
    return articles


def _mock_queries(prompt: str) -> list[str]:
    keywords = [
        w
        for w in re.findall(r"[\u0621-\u064A]{4,}", prompt)
        if w
        not in {"المادة", "القانون", "المحكمة", "الوقائع", "المدعي", "المدعى"}
    ]
    unique: list[str] = []
    for word in keywords:
        if word not in unique:
            unique.append(word)
        if len(unique) >= 5:
            break
    return unique or ["الالتزام", "التعويض"]


def _mock_evidence(prompt: str, articles: list[dict[str, Any]]) -> dict[str, Any]:
    titles = re.findall(r"^\s*\d+\.\s+\[(\w+)\]\s+\((\w+)\)\s+(.+)$", prompt, re.MULTILINE)
    strength_by_type = {
        "admission": ("decisive", 0.95),
        "contract": ("strong", 0.85),
        "receipt": ("strong", 0.8),
        "document": ("moderate", 0.6),
        "correspondence": ("moderate", 0.55),
        "witness": ("moderate", 0.5),
        "expert": ("strong", 0.75),
        "digital": ("weak", 0.35),
        "audio": ("weak", 0.3),
        "video": ("weak", 0.3),
        "photo": ("weak", 0.25),
    }
    assessments = []
    for role, kind, title in titles:
        strength, weight = strength_by_type.get(kind, ("moderate", 0.5))
        assessments.append(
            {
                "evidence_title": title.strip(),
                "submitted_by": role,
                "proof_category": _proof_category(kind),
                "strength": strength,
                "admissibility": "admissible",
                "weight": weight,
                "assessment": (
                    f"دليل من نوع ({kind}) مُقدَّم من {role}؛ قُدِّر وزنه الترجيحي بـ {weight} "
                    "وفق قواعد الإثبات العامة في القانون اليمني، مع مراعاة عدم وجود طعن "
                    "بالتزوير أو مطعن شكلي في الملف."
                ),
                "cited_articles": [a["article_label"] for a in articles[:1]],
            }
        )
    if not assessments:
        assessments.append(
            {
                "evidence_title": "لا توجد أدلة مرفقة",
                "submitted_by": "plaintiff",
                "proof_category": None,
                "strength": "weak",
                "admissibility": "requires_verification",
                "weight": 0.0,
                "assessment": "لم تُرفق أدلة بالملف، ولا يمكن للمحكمة أن تبني قضاءها على مجرد ادعاء.",
                "cited_articles": [],
            }
        )
    return {"assessments": assessments}


def _proof_category(kind: str) -> str | None:
    return {
        "admission": "إقرار",
        "contract": "كتابة",
        "receipt": "كتابة",
        "document": "كتابة",
        "correspondence": "كتابة",
        "witness": "شهادة",
        "expert": "خبرة",
        "inspection": "معاينة",
        "oath": "يمين",
        "presumption": "قرينة",
        "digital": "قرينة",
    }.get(kind)


def _mock_reasoning(prompt: str, articles: list[dict[str, Any]]) -> dict[str, Any]:
    cited = [
        {
            "article_label": a["article_label"],
            "law_title": a["law_title"],
            "quote": a["content"][:400],
            "relevance": "سند قانوني مباشر للفصل في موضوع الدعوى.",
        }
        for a in articles[:4]
    ]
    steps = [
        {
            "step": index + 1,
            "question": f"تطبيق {a['article_label']} من {a['law_title']} على وقائع الدعوى",
            "analysis": f"وحيث إن نص {a['article_label']} يقضي بأن: «{a['content'][:200]}…»",
            "conclusion": "يترتب على ما تقدم إعمال هذا النص في مواجهة الطرف المُلزَم به.",
            "cited_articles": [a["article_label"]],
        }
        for index, a in enumerate(articles[:4])
    ]
    return {
        "summary": "وحيث إن المحكمة استعرضت نص الدعوى وردود المدعى عليه والأدلة المقدمة، "
        "ووجدت أن النصوص القانونية المسترجعة تكفي للفصل في المسائل المطروحة.",
        "burden_of_proof": "البينة على من ادعى واليمين على من أنكر؛ وعليه يجب على المدعي "
        "إثبات دعواه، فإن عجز كان القول قول المدعى عليه مع يمينه.",
        "evidence_analysis": [],
        "legal_analysis": steps,
        "cited_articles": cited,
        "defendant_defences_assessment": [
            "لم تُقدَّم دفوع شكلية مانعة من نظر الدعوى بحسب ما ورد في الملف."
        ],
        "precedents": [],
    }


def _mock_judgment(prompt: str, articles: list[dict[str, Any]]) -> dict[str, Any]:
    case_number = _case_field(prompt, "رقم القضية")
    plaintiff = _case_field(prompt, "المدعي") or "المدعي"
    defendant = _case_field(prompt, "المدعى عليه") or "المدعى عليه"
    subject = _case_field(prompt, "موضوع الدعوى") or "موضوع الدعوى"
    court = _case_field(prompt, "المحكمة") or settings.default_court_name
    basis = [a["article_label"] for a in articles[:3]] or ["المواد القانونية المسترجعة"]
    return {
        "preamble": {
            "court_name": court,
            "case_number": case_number,
            "jurisdiction": settings.jurisdiction,
            "plaintiff": {"name": plaintiff},
            "defendant": {"name": defendant},
            "subject_matter": subject,
            "attendance": "حضر الطرفان أو من يمثلهما قانوناً.",
        },
        "facts": {
            "narrative": f"تخلص وقائع الدعوى في أن {plaintiff} أقام الدعوى رقم {case_number or '—'} "
            f"ضد {defendant} بخصوص {subject}، وقد أنكر المدعى عليه ما نُسب إليه.",
            "plaintiff_claims": [],
            "defendant_responses": [],
            "disputed_points": ["مدى ثبوت الحق المطالب به"],
            "undisputed_points": [],
        },
        "reasoning": _mock_reasoning(prompt, articles),
        "verdict": {
            "summary": "حكمت المحكمة بقبول الدعوى شكلاً، وفي الموضوع بإلزام المدعى عليه بما "
            "تقضي به النصوص القانونية المستشهد بها.",
            "items": [
                {
                    "decision": "قبول",
                    "subject": "الدعوى شكلاً",
                    "detail": "قبول الدعوى شكلاً لاستيفائها أوضاعها الشكلية المقررة قانوناً.",
                    "legal_basis": basis,
                },
                {
                    "decision": "إلزام",
                    "subject": subject,
                    "detail": "إلزام المدعى عليه بتنفيذ ما تقتضيه النصوص القانونية المستشهد بها "
                    "في الأسباب.",
                    "legal_basis": basis,
                },
            ],
            "costs": "ألزمت المحكمة المدعى عليه بالمصاريف ومقابل أتعاب المحاماة.",
            "final": True,
            "appeal_deadline": "خلال ثلاثين يوماً من تاريخ صدور الحكم أو إعلانه.",
        },
    }


def _case_field(prompt: str, label: str) -> str | None:
    """Read a `label: value` line emitted by ``build_case_block``.

    Anchored to the line start so a label such as «المحكمة» cannot match an
    occurrence of the same word inside prose further down the prompt, and the
    value cannot run past the end of the line.
    """
    pattern = rf"^[^\S\n]*(?:-[ ]+)?{re.escape(label)}[:：][^\S\n]*(.+)$"
    match = re.search(pattern, prompt, re.MULTILINE)
    if not match:
        return None
    value = match.group(1).strip()
    return value or None


# ---------------------------------------------------------------------------
# Hosted providers
# ---------------------------------------------------------------------------
class OpenAIProvider:
    """OpenAI Chat Completions (also serves any OpenAI-compatible gateway)."""

    name = "openai"

    def __init__(self, *, base_url: str | None = None, name: str = "openai") -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise LLMError("مكتبة openai غير مثبتة", details="pip install openai") from exc
        if not settings.llm_api_key:
            raise LLMError("LLM_PROVIDER=openai يتطلب LLM_API_KEY")
        self.name = name
        self.model = settings.llm_model
        self._client = AsyncOpenAI(
            api_key=settings.llm_api_key,
            base_url=base_url or settings.llm_base_url or None,
            timeout=settings.llm_timeout_seconds,
        )

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        json_mode: bool = True,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> Completion:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}]
            + [{"role": m.role, "content": m.content} for m in messages],
            "temperature": settings.llm_temperature if temperature is None else temperature,
            "max_tokens": max_tokens or settings.llm_max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            response = await self._client.chat.completions.create(**payload)
        except Exception as exc:
            raise LLMError("فشل استدعاء نموذج الذكاء الاصطناعي", details=str(exc)) from exc

        text = response.choices[0].message.content or ""
        usage = getattr(response, "usage", None)
        return Completion(
            text=text,
            data=parse_json_response(text) if json_mode else None,
            model=response.model,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            provider=self.name,
        )


class AnthropicProvider:
    name = "anthropic"

    def __init__(self) -> None:
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise LLMError("مكتبة anthropic غير مثبتة", details="pip install anthropic") from exc
        if not settings.llm_api_key:
            raise LLMError("LLM_PROVIDER=anthropic يتطلب LLM_API_KEY")
        self.model = settings.llm_model
        self._client = AsyncAnthropic(
            api_key=settings.llm_api_key, timeout=settings.llm_timeout_seconds
        )

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        json_mode: bool = True,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> Completion:
        system_prompt = system
        if json_mode:
            system_prompt += "\n\nأخرج الإجابة ككائن JSON صالح فقط، دون أي نص آخر."
        try:
            response = await self._client.messages.create(
                model=self.model,
                system=system_prompt,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                temperature=settings.llm_temperature if temperature is None else temperature,
                max_tokens=max_tokens or settings.llm_max_tokens,
            )
        except Exception as exc:
            raise LLMError("فشل استدعاء نموذج الذكاء الاصطناعي", details=str(exc)) from exc

        text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )
        usage = getattr(response, "usage", None)
        return Completion(
            text=text,
            data=parse_json_response(text) if json_mode else None,
            model=response.model,
            prompt_tokens=getattr(usage, "input_tokens", 0) or 0,
            completion_tokens=getattr(usage, "output_tokens", 0) or 0,
            provider=self.name,
        )


def build_llm_provider() -> LLMProvider:
    provider = settings.llm_provider
    if provider == "openai":
        return OpenAIProvider()
    if provider == "openai_compatible":
        return OpenAIProvider(base_url=settings.llm_base_url, name="openai_compatible")
    if provider == "anthropic":
        return AnthropicProvider()
    return MockLLMProvider()


llm_provider: LLMProvider = build_llm_provider()
