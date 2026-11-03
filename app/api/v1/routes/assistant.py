"""AI legal assistant endpoints (المساعد القانوني الذكي).

POST   /assistant/ask                       - ask a grounded legal question
GET    /assistant/conversations             - the caller's threads
GET    /assistant/conversations/{id}        - one thread with its messages
DELETE /assistant/conversations/{id}        - delete a thread

The assistant is available to every authenticated role (it is a reading aid),
but a thread is private to the user who created it.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import AssistantDep, CurrentUserDep, require_permission
from app.schemas.assistant import (
    AssistantAnswer,
    AssistantAskRequest,
    AssistantConversationDetail,
    AssistantConversationList,
)

router = APIRouter(prefix="/assistant", tags=["assistant"])


@router.post(
    "/ask",
    response_model=AssistantAnswer,
    summary="اسأل المساعد القانوني الذكي",
    description=(
        "يستقبل سؤالاً قانونياً، يسترجع النصوص اليمنية ذات الصلة (RAG)، ثم يولّد "
        "إجابة موثقة بالاستشهادات. يحفظ السؤال والجواب في محادثة خاصة بالمستخدم، "
        "ويمكن متابعة المحادثة بإرسال معرفها (conversation_id)."
    ),
    dependencies=[Depends(require_permission("assistant.use"))],
)
async def ask(payload: AssistantAskRequest, user: CurrentUserDep, assistant: AssistantDep) -> AssistantAnswer:
    return await assistant.ask(
        user=user,
        question=payload.question,
        conversation_id=str(payload.conversation_id) if payload.conversation_id else None,
        case_id=str(payload.case_id) if payload.case_id else None,
        case_type=payload.case_type,
        top_k=payload.top_k,
        include_history=payload.include_history,
    )


@router.get(
    "/conversations",
    response_model=AssistantConversationList,
    summary="محادثات المساعد الخاصة بالمستخدم الحالي",
)
async def list_conversations(
    user: CurrentUserDep,
    assistant: AssistantDep,
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> AssistantConversationList:
    return await assistant.list_conversations(user=user, limit=limit, offset=offset)


@router.get(
    "/conversations/{conversation_id}",
    response_model=AssistantConversationDetail,
    summary="تفاصيل محادثة ورسائلها",
)
async def get_conversation(
    conversation_id: uuid.UUID, user: CurrentUserDep, assistant: AssistantDep
) -> AssistantConversationDetail:
    return await assistant.get_conversation(
        user=user, conversation_id=str(conversation_id)
    )


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="حذف محادثة",
)
async def delete_conversation(
    conversation_id: uuid.UUID, user: CurrentUserDep, assistant: AssistantDep
) -> None:
    await assistant.delete_conversation(user=user, conversation_id=str(conversation_id))
