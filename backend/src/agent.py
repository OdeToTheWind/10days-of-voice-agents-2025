import logging
import os
import json
import asyncio
from datetime import datetime
from dotenv import load_dotenv

from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    JobProcess,
    RunContext,
    MetricsCollectedEvent,
    RoomInputOptions,
    WorkerOptions,
    cli,
    metrics,
    tokenize,
    function_tool,
)
from livekit.plugins import (
    silero,
    deepgram,
    google,
    murf,
    noise_cancellation
)
from livekit.plugins.turn_detector.multilingual import MultilingualModel

# -------------------------------------------------------------
# LOGGING & ENV SETUP
# -------------------------------------------------------------
logger = logging.getLogger("agent")
logger.setLevel(logging.INFO)
load_dotenv(".env.local")

# -------------------------------------------------------------
# JSON LOG FILE SETUP — SAME SHAPE AS agents1.py
# -------------------------------------------------------------
LOG_FILE = "wellness_log.json"

if not os.path.exists(LOG_FILE):
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump({"entries": []}, f)


def read_log():
    """Read disk log safely."""
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("entries", [])
    except:
        return []


def write_log(entries):
    try:
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            json.dump({"entries": entries}, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error("Write log error: %s", e)


# -------------------------------------------------------------
# WELLNESS STATE (simple version)
# -------------------------------------------------------------
class WellnessState:
    def __init__(self):
        self.mood = None
        self.energy = None
        self.stress = []
        self.goals = []

    def to_dict(self):
        return {
            "mood": self.mood,
            "energy": self.energy,
            "stress": self.stress,
            "goals": self.goals,
        }


# -------------------------------------------------------------
# THE WELLNESS ASSISTANT (Gemini + Hybrid Logging)
# -------------------------------------------------------------
class WellnessAssistant(Agent):

    # Basic keyword detection rules
    DETECT = {
        "sad": ["sad", "down", "depressed", "bad", "unhappy"],
        "anxious": ["anxious", "worried", "anxiety", "panic"],
        "stressed": ["stress", "stressed", "overwhelmed"],
        "happy": ["happy", "great", "good", "excited"],
        "tired": ["tired", "exhausted", "sleepy", "drained"],
        "neutral": ["ok", "okay", "fine", "normal", "alright", "feeling okay"],
    }


    def __init__(self):
        super().__init__(
            instructions="""
You are a warm, friendly, supportive wellness companion.
Your goals:
- Ask how the user feels
- Understand mood, stress, energy, goals
- Give short, empathetic responses
- Encourage small healthy actions
- Keep tone calm, non-medical, natural
            """
        )
        self.state = WellnessState()
        self.room = None

    def set_room(self, room):
        self.room = room

    # ---------------------------------------------------------
    # INTERNAL HELPER: append entry to file
    # ---------------------------------------------------------
    def save_entry(self, entry: dict):
        data = read_log()
        data.append(entry)
        write_log(data)

    # ---------------------------------------------------------
    # HYBRID AUTO-DETECTOR
    # ---------------------------------------------------------
    async def auto_detect(self, message: str):
        """Detect mood/stress keywords and log automatically."""
        msg = message.lower()
        matched = []

        if message.lower().startswith(("i am", "i'm", "feeling", "i feel")):
            matched.append("general_mood")


        for label, kws in self.DETECT.items():
            if any(kw in msg for kw in kws):
                matched.append(label)

        if not matched:
            return False

        entry = {
            "text": message,
            "detected": matched,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "source": "auto",
        }

        # Update internal state
        if matched:
            self.state.mood = matched[0]

        self.save_entry(entry)

        # Notify frontend (optional)
        if self.room:
            await self.room.local_participant.publish_data(
                json.dumps({"type": "auto_log", "data": entry}).encode("utf-8"),
                topic="wellness_checkin"
            )

        logger.info("AUTO LOGGED: %s", entry)
        return True

    # ---------------------------------------------------------
    # Optional function tool (not used by Gemini but kept for future)
    # ---------------------------------------------------------
    @function_tool
    async def log_wellness(self, ctx: RunContext, message: str):
        entry = {
            "text": message,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "source": "tool_call"
        }
        self.save_entry(entry)
        return "Wellness entry saved."

# -------------------------------------------------------------
# PREWARM (Load VAD)
# -------------------------------------------------------------
def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()

# -------------------------------------------------------------
# ENTRYPOINT (LiveKit Worker)
# -------------------------------------------------------------
async def entrypoint(ctx: JobContext):

    session = AgentSession(
        stt=deepgram.STT(model="nova-3"),
        llm=google.LLM(model="gemini-2.5-flash"),
        tts=murf.TTS(
            voice="en-US-matthew",
            style="Conversation",
            tokenizer=tokenize.basic.SentenceTokenizer(min_sentence_len=2),
            text_pacing=True,
        ),
        vad=ctx.proc.userdata["vad"],
        turn_detection=MultilingualModel(),
        preemptive_generation=True,
    )

    assistant = WellnessAssistant()
    assistant.set_room(ctx.room)

    # -------------------------------
    # USER SPEECH DETECTION
    # -------------------------------
    @session.on("user_speech_committed")
    def on_user_speech(msg: str):
        logger.info(f"USER SAID: {msg}")
        # Non-blocking detection
        asyncio.create_task(assistant.auto_detect(msg))

    # Start
    await session.start(
        agent=assistant,
        room=ctx.room,
        room_input_options=RoomInputOptions(noise_cancellation=noise_cancellation.BVC())
    )

    await ctx.connect()


# -------------------------------------------------------------
# RUN WORKER
# -------------------------------------------------------------
if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm))
