from fastapi import APIRouter

from models.schemas import BreathingExercise, GroundingTechnique, MeditationScript

router = APIRouter(prefix="/api/wellness", tags=["wellness"])


@router.get("/breathing", response_model=list[BreathingExercise])
def get_breathing() -> list[BreathingExercise]:
    return [
        BreathingExercise(
            id="box",
            title="Box Breathing",
            inhale=4,
            hold=4,
            exhale=4,
            hold_after_exhale=4,
            description="Inhale, hold, exhale, and hold again for equal counts.",
        ),
        BreathingExercise(
            id="478",
            title="4-7-8 Breathing",
            inhale=4,
            hold=7,
            exhale=8,
            hold_after_exhale=0,
            description="A slower pattern that helps ease physical tension before bed or after a spike of stress.",
        ),
    ]


@router.get("/grounding", response_model=GroundingTechnique)
def get_grounding() -> GroundingTechnique:
    return GroundingTechnique(
        title="5-4-3-2-1 Grounding",
        intro="Use your senses to reconnect with the room around you one step at a time.",
        steps=[
            {"count": 5, "sense": "see", "prompt": "Name 5 things you can see right now."},
            {"count": 4, "sense": "touch", "prompt": "Name 4 things you can physically feel."},
            {"count": 3, "sense": "hear", "prompt": "Name 3 sounds you can hear."},
            {"count": 2, "sense": "smell", "prompt": "Name 2 things you can smell."},
            {"count": 1, "sense": "taste", "prompt": "Notice 1 thing you can taste."},
        ],
    )


@router.get("/meditations", response_model=list[MeditationScript])
def get_meditations() -> list[MeditationScript]:
    return [
        MeditationScript(
            id="soft-reset",
            title="Soft Reset",
            duration_minutes=3,
            script="Sit back, unclench your jaw, and notice the chair holding you up. "
            "Let your breath lengthen without forcing it. "
            "If a thought about the day returns, label it gently and come back to the next exhale.",
        ),
        MeditationScript(
            id="after-the-text",
            title="After The Text",
            duration_minutes=4,
            script="Place one hand on your chest and one on your stomach. "
            "Notice the urge to replay what happened, then let that urge pass through without fighting it. "
            "You do not have to solve the whole relationship in this moment.",
        ),
        MeditationScript(
            id="night-unwind",
            title="Night Unwind",
            duration_minutes=5,
            script="Let your eyes soften and imagine setting each hard thought into a cup beside you. "
            "They are still there if you need them later, but you do not need to hold them right now. "
            "Stay with the feeling of the room getting quieter around you.",
        ),
    ]
