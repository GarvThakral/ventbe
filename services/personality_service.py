from typing import List
import httpx
from db.supabase_client import get_settings
from services.logging import get_logger

logger = get_logger("vent.personality")

async def recommend_personality(
    person_name: str,
    memories: List[str],
    recent_messages: List[dict],
) -> str:
    settings = get_settings()
    
    memory_summary = "\n".join(memories[:5])
    chat_snippet = "\n".join([f"{m['role']}: {m['content']}" for m in recent_messages[-5:]])
    
    prompt = (
        "You are an AI that helps users pick the best personality for their journaling companion.\n"
        "Available Personalities:\n"
        "1. Golden Retriever: Calm, supportive, simple, loyal.\n"
        "2. Chihuahua: Chaotic, hyper-alert, protective, quick to spot red flags.\n"
        "3. Wise Owl: Patient, analytical, focus on long-term growth.\n"
        "4. Cat: Aloof, observant, dry wit, tells it like it is.\n\n"
        f"The user is talking about {person_name}.\n"
        f"Memories of this person: {memory_summary}\n"
        f"Recent conversation: {chat_snippet}\n\n"
        "BASED ON THE ABOVE, which personality should the user use right now to gain the best perspective? "
        "Return ONLY the key name (golden_retriever, chihuahua, wise_owl, or cat) followed by a short one-sentence explanation."
    )

    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.primary_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.5,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{settings.openrouter_base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            data = response.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error("Failed to get personality recommendation: %s", str(e))
        return "golden_retriever: Always a good choice for support."
