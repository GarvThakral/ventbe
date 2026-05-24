from fastapi import APIRouter, Depends, HTTPException, status

from db.supabase_client import get_supabase_admin_client
from models.schemas import ChatCreateRequest, ChatResponse, ChatUpdateRequest, MemoryResponse, UserResponse
from services.auth_service import require_current_user
from services.memory_service import clear_memories, list_memories
from services.personality_service import recommend_personality

router = APIRouter(prefix="/api/chats", tags=["chats"])


@router.get("/all-memories")
def get_all_memories(user: UserResponse = Depends(require_current_user)):
    """Fetch all memories across all chats for the memory tree view."""
    admin_client = get_supabase_admin_client()

    chats_response = (
        admin_client.table("chats")
        .select("id, name, emoji")
        .eq("user_id", user.id)
        .order("created_at", desc=True)
        .execute()
    )
    chats = getattr(chats_response, "data", None) or []

    result = []
    for chat in chats:
        memories = list_memories(user.id, chat["id"], limit=50)
        result.append({
            "chat_id": chat["id"],
            "chat_name": chat["name"],
            "chat_emoji": chat["emoji"],
            "memories": [{"id": m.id, "content": m.content, "created_at": m.created_at} for m in memories],
        })

    return result

@router.get("/{chat_id}/recommend-personality")
async def get_personality_recommendation(
    chat_id: str,
    user: UserResponse = Depends(require_current_user),
):
    if not user.is_premium:
        raise HTTPException(status_code=403, detail="Premium subscription required for AI recommendations")
    
    admin_client = get_supabase_admin_client()
    chat = (admin_client.table("chats").select("name").eq("id", chat_id).single().execute()).data
    memories = [m.content for m in list_memories(user.id, chat_id, limit=5)]
    
    # We'd fetch recent messages here too, but for now let's use memories
    recommendation = await recommend_personality(chat["name"], memories, [])
    return {"recommendation": recommendation}


def _get_chat_or_404(user_id: str, chat_id: str) -> dict:
    admin_client = get_supabase_admin_client()
    response = (
        admin_client.table("chats")
        .select("id, user_id, name, emoji, image_url, personality, created_at")
        .eq("id", chat_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")
    return rows[0]


@router.get("", response_model=list[ChatResponse])
def list_chats(user: UserResponse = Depends(require_current_user)) -> list[ChatResponse]:
    admin_client = get_supabase_admin_client()
    chat_response = (
        admin_client.table("chats")
        .select("id, user_id, name, emoji, image_url, personality, created_at")
        .eq("user_id", user.id)
        .order("created_at", desc=True)
        .execute()
    )
    chats = getattr(chat_response, "data", None) or []

    summaries: list[ChatResponse] = []
    for chat in chats:
        message_response = (
            admin_client.table("messages")
            .select("content, created_at, role")
            .eq("chat_id", chat["id"])
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        messages = getattr(message_response, "data", None) or []
        last_message = messages[0] if messages else {}
        summaries.append(
            ChatResponse(
                **chat,
                last_message=last_message.get("content"),
                last_message_at=last_message.get("created_at"),
                last_message_role=last_message.get("role"),
            )
        )

    return summaries


@router.post("", response_model=ChatResponse, status_code=status.HTTP_201_CREATED)
def create_chat(
    payload: ChatCreateRequest,
    user: UserResponse = Depends(require_current_user),
) -> ChatResponse:
    admin_client = get_supabase_admin_client()
    
    if not user.is_premium:
        chats_count_res = (
            admin_client.table("chats")
            .select("id", count="exact")
            .eq("user_id", user.id)
            .execute()
        )
        existing_count = getattr(chats_count_res, "count", 0)
        if existing_count >= 3:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Free tier is limited to 3 chats. Upgrade to Premium for unlimited chats! ✨"
            )

    response = (
        admin_client.table("chats")
        .insert(
            {
                "user_id": user.id,
                "name": payload.name.strip(),
                "emoji": payload.emoji,
                "image_url": payload.image_url,
                "personality": payload.personality,
            }
        )
        .execute()
    )
    row = (getattr(response, "data", None) or [None])[0]
    if not row:
        raise HTTPException(status_code=400, detail="Unable to create chat")
    return ChatResponse(**row)


@router.get("/{chat_id}", response_model=ChatResponse)
def get_chat(chat_id: str, user: UserResponse = Depends(require_current_user)) -> ChatResponse:
    return ChatResponse(**_get_chat_or_404(user.id, chat_id))


@router.patch("/{chat_id}", response_model=ChatResponse)
def update_chat(
    chat_id: str,
    payload: ChatUpdateRequest,
    user: UserResponse = Depends(require_current_user),
) -> ChatResponse:
    current = _get_chat_or_404(user.id, chat_id)
    updates = {
        "name": payload.name.strip() if payload.name else current["name"],
        "emoji": payload.emoji or current["emoji"],
        "image_url": payload.image_url if payload.image_url is not None else current.get("image_url"),
        "personality": payload.personality if payload.personality is not None else current.get("personality"),
    }

    admin_client = get_supabase_admin_client()
    response = (
        admin_client.table("chats")
        .update(updates)
        .eq("id", chat_id)
        .eq("user_id", user.id)
        .execute()
    )
    row = (getattr(response, "data", None) or [None])[0]
    if not row:
        raise HTTPException(status_code=400, detail="Unable to update chat")
    return ChatResponse(**row)


@router.delete("/{chat_id}")
def delete_chat(chat_id: str, user: UserResponse = Depends(require_current_user)) -> dict[str, str]:
    _get_chat_or_404(user.id, chat_id)
    admin_client = get_supabase_admin_client()
    admin_client.table("chats").delete().eq("id", chat_id).eq("user_id", user.id).execute()
    return {"message": "Chat deleted"}


@router.get("/{chat_id}/memories", response_model=list[MemoryResponse])
def get_chat_memories(
    chat_id: str,
    user: UserResponse = Depends(require_current_user),
) -> list[MemoryResponse]:
    _get_chat_or_404(user.id, chat_id)
    return list_memories(user.id, chat_id)


@router.delete("/{chat_id}/memories")
def clear_chat_memories(
    chat_id: str,
    user: UserResponse = Depends(require_current_user),
) -> dict[str, int]:
    _get_chat_or_404(user.id, chat_id)
    return clear_memories(user.id, chat_id)
