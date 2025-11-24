import logging
import json
import os
from datetime import datetime

from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    JobProcess,
    RoomInputOptions,
    WorkerOptions,
    cli,
    metrics,
    MetricsCollectedEvent,
)
from livekit.plugins import murf, silero, google, deepgram, noise_cancellation
from livekit.plugins.turn_detector.multilingual import MultilingualModel

logger = logging.getLogger("agent")

load_dotenv(".env.local")

LOG_FILE = "wellness_log.json"


# -----------------------------
# JSON persistence utilities
# -----------------------------
def read_log():
    if not os.path.exists(LOG_FILE):
        return []
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# def write_log(entries):
#     with open(LOG_FILE, "w", encoding="utf-8") as f:
#         json.dump(entries, f, indent=2, ensure_ascii=False)
def write_log(entries):
    try:
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error("Error writing log: %s", e)


def last_entry():
    entries = read_log()
    return entries[-1] if entries else None


# -----------------------------
# Wellness Agent
# -----------------------------
class WellnessAssistant(Agent):
    def __init__(self):
        super().__init__(
            instructions="""
You are a Health & Wellness Voice Companion.

You talk naturally, clearly, and conversationally — never clinical or diagnostic.

Your behavior:
- Ask about mood, energy, stress levels.
- Ask for 1–3 simple goals or intentions (daily tasks, plans, or self-care).
- Offer small, realistic, supportive suggestions. (Short walk, break tasks down, stretch, drink water.)
- Keep responses concise and warm.
- Avoid medical or psychological diagnoses.
- At the end, summarize the user’s mood and goals and ask: “Does this sound right?”
- If previous check-in is provided, politely reference it once.
- No emojis or symbols.
            """
        )

    # Called after every LLM response
    async def on_llm_response(self, ctx, response_text: str):
        """
        This is where we parse the user's input (already seen by LLM),
        extract data, and persist it to JSON.
        """

        user_text = ctx.turn.last_user_message or ""
        data = read_log()
        previous = last_entry()

        # Very simple extraction (you can improve with model JSON mode)
        mood_score = None
        if any(w in user_text.lower() for w in ["tired", "low", "exhausted", "drained"]):
            mood_score = 3
        if any(w in user_text.lower() for w in ["good", "fine", "okay", "better"]):
            mood_score = 6
        if any(w in user_text.lower() for w in ["great", "energized", "amazing"]):
            mood_score = 8

        # Objective extraction heuristic
        objs = []
        lower = user_text.lower()
        for phrase in ["i want to", "i will", "i plan to", "my goal", "i’d like to"]:
            if phrase in lower:
                segment = lower.split(phrase, 1)[1]
                parts = segment.replace(".", ",").split(",")
                objs = [p.strip() for p in parts if p.strip()][:3]
                break

        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "raw_text": user_text,
            "mood_scale": mood_score,
            "objectives": objs,
            "summary": f"Mood:{mood_score} Goals:{objs}"
        }

        data.append(entry)
        write_log(data)

        logger.info("Saved wellness entry: %s", entry)


# -----------------------------
# LiveKit Agent Entrypoint
# -----------------------------
def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()


async def entrypoint(ctx: JobContext):
    ctx.log_context_fields = {
        "room": ctx.room.name,
    }

    session = AgentSession(
        stt=deepgram.STT(model="nova-3"),
        llm=google.LLM(
            model="gemini-2.5-flash",
        ),
        tts=murf.TTS(
            voice="en-US-matthew",
            style="Conversation",
        ),
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
        preemptive_generation=True,
    )

    usage_collector = metrics.UsageCollector()

    @session.on("metrics_collected")
    def _metrics(ev: MetricsCollectedEvent):
        metrics.log_metrics(ev.metrics)
        usage_collector.collect(ev.metrics)

    async def log_usage():
        summary = usage_collector.get_summary()
        logger.info("Usage: %s", summary)

    ctx.add_shutdown_callback(log_usage)

    # inject previous session into LLM context
    previous = last_entry()
    if previous:
        session.append_message(
            role="system",
            text=f"Previous check-in summary: {previous['summary']}. Feel free to reference it politely."
        )

    await session.start(
        agent=WellnessAssistant(),
        room=ctx.room,
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )

    await ctx.connect()


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm)
    )
