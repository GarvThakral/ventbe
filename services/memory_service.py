import re
from typing import Any

import httpx

from db.supabase_client import get_settings, get_supabase_admin_client
from models.schemas import MemoryResponse
from services.logging import get_logger

logger = get_logger("vent.memory")


def _vector_literal(embedding: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in embedding) + "]"


async def embed_text(text: str) -> list[float]:
    cleaned = " ".join(text.split()).strip()
    if not cleaned:
        return []

    settings = get_settings()
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://vent.app",
        "X-Title": settings.app_name,
    }
    payload = {
        "model": settings.embedding_model,
        "input": cleaned,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{settings.openrouter_base_url}/embeddings",
                headers=headers,
                json=payload,
            )
            if response.status_code != 200:
                logger.error("OpenRouter embedding failed: %s %s", response.status_code, response.text)
                return []
            
            data = response.json()
            return data["data"][0]["embedding"]
    except Exception as e:
        logger.error("Error calling OpenRouter embeddings: %s", str(e))
        return []


async def retrieve_relevant_memories(
    user_id: str,
    chat_id: str,
    query_text: str,
    limit: int = 3,
) -> list[MemoryResponse]:
    if not query_text.strip():
        return []

    embedding = await embed_text(query_text)
    if not embedding:
        return []

    admin_client = get_supabase_admin_client()
    try:
        response = admin_client.rpc(
            "match_memories",
            {
                "query_embedding": _vector_literal(embedding),
                "filter_chat_id": chat_id,
                "filter_user_id": user_id,
                "match_count": limit,
            },
        ).execute()
    except Exception:
        return []

    rows = getattr(response, "data", None) or []
    return [MemoryResponse(**row) for row in rows]


def get_recent_memories(
    user_id: str,
    chat_id: str,
    limit: int = 5,
) -> list[MemoryResponse]:
    admin_client = get_supabase_admin_client()
    try:
        response = (
            admin_client.table("memories")
            .select("id, content, created_at")
            .eq("user_id", user_id)
            .eq("chat_id", chat_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        rows = getattr(response, "data", None) or []
        return [MemoryResponse(**row) for row in rows]
    except Exception as e:
        logger.error("Error fetching recent memories: %s", str(e))
        return []


async def _extract_memories_with_ai(chat_id: str, text: str, mood_tag: str | None = None) -> list[str]:
    """Uses LLM to extract short, factual insights from a message."""
    settings = get_settings()
    admin_client = get_supabase_admin_client()
    
    # Get chat name to provide context to the extraction prompt
    try:
        chat_response = admin_client.table("chats").select("name").eq("id", chat_id).execute()
        chat_name = "this person"
        if chat_response.data and len(chat_response.data) > 0:
            chat_name = chat_response.data[0].get("name", "this person")
    except Exception:
        chat_name = "this person"

    prompt = (
        f"You are a memory extractor for a journaling app. The user is talking about {chat_name}.\n"
        f"User's current emotion/mood: {mood_tag or 'Not specified'}\n"
        "Extract 1-3 clear, insightful bullet points about what happened or how the user feels.\n"
        "Each point should be a complete thought that provides enough context to be understood later without the original message.\n"
        "IMPORTANT: Include specific quotes or 'exact phrases' from the user in quotation marks if they are significant.\n"
        "Aim for 15-30 words per point to ensure clarity. Focus on patterns, major events, or deep-seated feelings.\n"
        "If there's nothing significant to remember, return an empty list.\n"
        "Format: Return ONLY the bullet points, one per line, no numbering, no symbols.\n\n"
        f"User Message: {text}"
    )

    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://vent.app",
        "X-Title": settings.app_name,
    }
    models = [settings.primary_model, settings.fallback_model]
    last_error = None
    
    for model in models:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
        }
        
        try:
            logger.debug("Extracting memories from chat_id=%s with model=%s", chat_id, model)
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    f"{settings.openrouter_base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                
                if response.status_code == 429:
                    logger.warning("Rate limit hit for model=%s during extraction, trying next...", model)
                    continue

                if response.status_code != 200:
                    logger.error("OpenRouter extraction failed for model=%s: %s %s", model, response.status_code, response.text)
                    continue
                    
                data = response.json()
                content = data["choices"][0]["message"]["content"].strip()
                memories = []
                for line in content.split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    # Strip leading bullet points/symbols like "-", "*", "•", "1.", etc.
                    cleaned = re.sub(r"^[ \t]*([-*•]|\d+\.)[ \t]*", "", line)
                    if cleaned:
                        memories.append(cleaned)
                
                logger.info("Extracted %d memories for chat_id=%s using model=%s", len(memories), chat_id, model)
                return memories
        except Exception as e:
            logger.error("Error during AI memory extraction with model=%s: %s", model, str(e))
            last_error = e
            continue
            
    if last_error:
        logger.error("All models failed for memory extraction.")
    return []


async def store_memory_if_significant(
    user_id: str,
    chat_id: str,
    content: str,
    mood_tag: str | None = None,
) -> None:
    try:
        memories = await _extract_memories_with_ai(chat_id, content, mood_tag)
        if not memories:
            logger.debug("No significant memories extracted from content.")
            return

        admin_client = get_supabase_admin_client()
        for memory_text in memories:
            logger.debug("Embedding memory: %s", memory_text)
            embedding = await embed_text(memory_text)
            if not embedding:
                logger.warning("Failed to get embedding for memory: %s", memory_text)
                continue

            logger.info("Storing memory for chat_id=%s: %s", chat_id, memory_text)
            admin_client.table("memories").insert(
                {
                    "user_id": user_id,
                    "chat_id": chat_id,
                    "content": memory_text,
                    "embedding": _vector_literal(embedding),
                }
            ).execute()
    except Exception as e:
        logger.error("Critical error in store_memory_if_significant: %s", str(e), exc_info=True)


def list_memories(user_id: str, chat_id: str, limit: int = 50) -> list[MemoryResponse]:
    admin_client = get_supabase_admin_client()
    response = (
        admin_client.table("memories")
        .select("id, content, created_at")
        .eq("user_id", user_id)
        .eq("chat_id", chat_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    return [MemoryResponse(**row) for row in rows]


def clear_memories(user_id: str, chat_id: str) -> dict[str, Any]:
    admin_client = get_supabase_admin_client()
    response = (
        admin_client.table("memories")
        .delete()
        .eq("user_id", user_id)
        .eq("chat_id", chat_id)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    return {"deleted": len(rows)}
