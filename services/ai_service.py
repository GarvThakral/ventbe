import asyncio
from collections import defaultdict, deque
import logging
from time import monotonic
from typing import Any

import httpx

from db.supabase_client import get_settings
from services.logging import get_logger

RATE_LIMIT_MESSAGE = "I'm taking a breath, send again in a moment ☕"
logger = get_logger("vent.ai")

CHARACTER_REGISTRY = {
    "default": "Expert Pattern Recognizer and Relationship Advisor. Direct, insightful, and compassionate.",
    "golden_retriever": "Loyal, enthusiastic, and endlessly supportive. Use simple, warm language. Always see the best in the user, but stay alert for things that might hurt them. Imagine you are a big, calm dog who just wants the user to be safe.",
    "chihuahua": "Chaotic, hyper-alert, and fiercely protective. High energy, a bit anxious, and very quick to spot red flags. You speak in short, punchy sentences and 'bark' (metaphorically) at any signs of disrespect from the other person.",
    "wise_owl": "Ancient, patient, and analytical. You speak in metaphors and focus on the long-term emotional growth. You are slow to judge but deep in your observations.",
    "cat": "Independent, slightly aloof, but deeply observant. You don't sugarcoat things. If the other person is acting poorly, you'll point it out with a sharp, dry wit.",
}


class RateLimitExceeded(Exception):
    pass


class PerUserRequestQueue:
    def __init__(self, max_requests: int = 2, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._timestamps: dict[str, deque[float]] = defaultdict(deque)

    async def acquire(self, user_id: str) -> None:
        async with self._locks[user_id]:
            timestamps = self._timestamps[user_id]
            now = monotonic()
            while timestamps and now - timestamps[0] >= self.window_seconds:
                timestamps.popleft()

            if len(timestamps) >= self.max_requests:
                wait_seconds = self.window_seconds - (now - timestamps[0])
                logger.warning("Rate limit hit for user_id=%s wait_seconds=%.1f", user_id, wait_seconds)
                await asyncio.sleep(min(max(wait_seconds, 0.0), 0.1))
                raise RateLimitExceeded(RATE_LIMIT_MESSAGE)

            timestamps.append(now)


rate_limiter = PerUserRequestQueue()


def build_system_prompt(
    person_name: str,
    mood_tag: str | None,
    memories: list[str],
    recent_messages: list[dict[str, str]],
    personality: str | None = None,
) -> str:
    memory_block = "\n".join(f"- {memory}" for memory in memories) if memories else "- No specific memories of this relationship yet."
    conversation_block = "\n".join(
        f"{message['role'].capitalize()}: {message['content']}" for message in recent_messages
    ) or "Beginning of conversation."

    character_instruction = CHARACTER_REGISTRY.get(personality.lower() if personality else "default", CHARACTER_REGISTRY["default"])
    if personality and personality.lower() not in CHARACTER_REGISTRY:
        character_instruction = f"{CHARACTER_REGISTRY['default']} Additionally, adopt this specific tone: {personality}"

    return (
        f"You are {character_instruction} "
        f"The user is talking to you about their relationship with {person_name}.\n"
        "Your PRIMARY ROLE: Analyze the user's input against their past memories to identify recurring patterns, "
        "contradictions, or red flags. You are NOT just a passive listener; you are here to help the user "
        "gain perspective and make informed decisions about this person.\n"
        "GUIDELINES:\n"
        f"1. Be an active advisor. If the user mentions {person_name} doing something good today, but you have memories "
        "of them being harmful or toxic 5 times in the past, POINT THIS OUT directly (e.g., 'It's nice he did that today, "
        "but remember that last week you felt crushed when he...').\n"
        "2. Help with decision making. If the user is debating whether to distance themselves from this person, "
        "use the historical data in 'What you remember' to weigh the pros and cons objectively.\n"
        "3. Be direct but compassionate. Don't sugarcoat harmful patterns. If a pattern of mistreatment is clear, call it out.\n"
        "4. Never roleplay as the person. Always maintain your perspective as the AI analyzer.\n"
        "5. Response length: Provide insightful analysis in 3-6 sentences. More if the user is facing a major decision.\n"
        f"Current mood tag: {mood_tag or 'None'}\n"
        f"What you remember about {person_name} and this relationship:\n{memory_block}\n"
        f"Conversation history:\n{conversation_block}"
    )


async def _call_openrouter(
    *,
    model: str,
    system_prompt: str,
    recent_messages: list[dict[str, str]],
) -> dict[str, Any]:
    settings = get_settings()
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://vent.app",
        "X-Title": settings.app_name,
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            *recent_messages,
        ],
        "temperature": 0.8,
    }

    async with httpx.AsyncClient(timeout=45.0) as client:
        logger.info("Calling OpenRouter model=%s", model)
        response = await client.post(
            f"{settings.openrouter_base_url}/chat/completions",
            headers=headers,
            json=payload,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            logger.error(
                "OpenRouter HTTP error model=%s status=%s body=%s",
                model,
                response.status_code,
                response.text[:1000],
            )
            raise
        return response.json()


async def generate_ai_reply(
    *,
    user_id: str,
    system_prompt: str,
    recent_messages: list[dict[str, str]],
) -> tuple[str, str]:
    settings = get_settings()
    await rate_limiter.acquire(user_id)

    models = [settings.primary_model, settings.fallback_model]
    last_error: Exception | None = None

    for model in models:
        try:
            data = await _call_openrouter(
                model=model,
                system_prompt=system_prompt,
                recent_messages=recent_messages,
            )
            content = data["choices"][0]["message"]["content"].strip()
            logger.info("OpenRouter reply received model=%s chars=%d", model, len(content))
            return content, model
        except Exception as exc:
            logger.exception("OpenRouter model failed model=%s", model)
            last_error = exc
            continue

    raise RuntimeError("OpenRouter failed for both primary and fallback models") from last_error
