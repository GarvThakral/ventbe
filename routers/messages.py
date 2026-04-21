from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

from db.supabase_client import get_supabase_admin_client
from models.schemas import (
    ConversationResponse,
    MessageCreateRequest,
    MessageListResponse,
    MessageResponse,
    UserResponse,
)
from services.ai_service import RATE_LIMIT_MESSAGE, RateLimitExceeded, build_system_prompt, generate_ai_reply
from services.auth_service import require_current_user
from services.memory_service import retrieve_relevant_memories, store_memory_if_significant

router = APIRouter(prefix="/api/messages", tags=["messages"])


def _ensure_chat_access(user_id: str, chat_id: str) -> dict:
    admin_client = get_supabase_admin_client()
    response = (
        admin_client.table("chats")
        .select("id, name, emoji")
        .eq("id", chat_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")
    return rows[0]


def _fetch_recent_messages(chat_id: str, limit: int) -> list[dict]:
    admin_client = get_supabase_admin_client()
    response = (
        admin_client.table("messages")
        .select("id, chat_id, role, content, mood_tag, used_memory, created_at")
        .eq("chat_id", chat_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    return list(reversed(rows))


def _insert_message(
    *,
    chat_id: str,
    role: str,
    content: str,
    mood_tag: str | None = None,
    used_memory: bool = False,
) -> MessageResponse:
    admin_client = get_supabase_admin_client()
    response = (
        admin_client.table("messages")
        .insert(
            {
                "chat_id": chat_id,
                "role": role,
                "content": content,
                "mood_tag": mood_tag,
                "used_memory": used_memory,
            }
        )
        .execute()
    )
    row = (getattr(response, "data", None) or [None])[0]
    if not row:
        raise HTTPException(status_code=400, detail="Unable to save message")
    return MessageResponse(**row)


@router.get("/{chat_id}", response_model=MessageListResponse)
def list_messages(
    chat_id: str,
    before: datetime | None = Query(default=None),
    user: UserResponse = Depends(require_current_user),
) -> MessageListResponse:
    _ensure_chat_access(user.id, chat_id)
    admin_client = get_supabase_admin_client()
    query = (
        admin_client.table("messages")
        .select("id, chat_id, role, content, mood_tag, used_memory, created_at")
        .eq("chat_id", chat_id)
        .order("created_at", desc=True)
        .limit(50)
    )
    if before:
        query = query.lt("created_at", before.isoformat())

    response = query.execute()
    rows = getattr(response, "data", None) or []
    rows = list(reversed(rows))

    next_before = None
    if len(rows) == 50:
        next_before = datetime.fromisoformat(rows[0]["created_at"].replace("Z", "+00:00"))

    return MessageListResponse(
        items=[MessageResponse(**row) for row in rows],
        next_before=next_before,
    )


@router.post("/{chat_id}", response_model=ConversationResponse)
async def create_message(
    chat_id: str,
    payload: MessageCreateRequest,
    background_tasks: BackgroundTasks,
    user: UserResponse = Depends(require_current_user),
) -> ConversationResponse:
    chat = _ensure_chat_access(user.id, chat_id)
    user_message = _insert_message(
        chat_id=chat_id,
        role="user",
        content=payload.content.strip(),
        mood_tag=payload.mood_tag,
    )

    recent_rows = _fetch_recent_messages(chat_id, limit=10)
    recent_messages = [{"role": row["role"], "content": row["content"]} for row in recent_rows]

    # Fetch both relevant (vector search) and most recent memories for context
    from services.memory_service import get_recent_memories
    
    relevant_memories = await retrieve_relevant_memories(
        user_id=user.id,
        chat_id=chat_id,
        query_text=payload.content,
        limit=7,
    )
    history_memories = get_recent_memories(
        user_id=user.id,
        chat_id=chat_id,
        limit=5,
    )

    # Merge and deduplicate
    all_memories = {m.id: m for m in (relevant_memories + history_memories)}
    memory_texts = [m.content for m in all_memories.values()]

    system_prompt = build_system_prompt(
        person_name=chat["name"],
        mood_tag=payload.mood_tag,
        memories=memory_texts,
        recent_messages=recent_messages,
        personality=chat.get("personality"),
    )

    try:
        ai_text, model_used = await generate_ai_reply(
            user_id=user.id,
            system_prompt=system_prompt,
            recent_messages=recent_messages,
        )
        rate_limited = False
    except RateLimitExceeded:
        ai_text = RATE_LIMIT_MESSAGE
        model_used = None
        rate_limited = True
    except Exception:
        ai_text = "I'm here with you. I had trouble replying just now, but you can send that again in a moment."
        model_used = None
        rate_limited = False

    ai_message = _insert_message(
        chat_id=chat_id,
        role="assistant",
        content=ai_text,
        used_memory=bool(memory_texts),
    )

    if not rate_limited and ai_text != RATE_LIMIT_MESSAGE:
        background_tasks.add_task(store_memory_if_significant, user.id, chat_id, user_message.content, user_message.mood_tag)

    return ConversationResponse(
        user_message=user_message,
        ai_message=ai_message,
        rate_limited=rate_limited,
        model_used=model_used,
    )
