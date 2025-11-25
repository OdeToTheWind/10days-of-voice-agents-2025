# backend/src/agent.py
"""
Teach-the-Tutor (Geography) - Agent implementation

- Loads content from backend/src/shared-data/day4_tutor_content.json
- Exposes tools: select_topic, set_learning_mode, evaluate_teaching
- Uses Murf voices per mode: Matthew (learn), Alicia (quiz), Ken (teach_back)
- Speaks a TTS-friendly topic list (no underscores / markdown)
- Avoids double-reading by loading content in prewarm and reusing proc.userdata
"""

import os
import json
import logging
from dataclasses import dataclass
from typing import Optional, Literal
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
    function_tool,
    RunContext,
)
from livekit.plugins import murf, silero, google, deepgram, noise_cancellation
from livekit.plugins.turn_detector.multilingual import MultilingualModel

# ---------- Logging & env ----------
load_dotenv(".env.local")
LOG = logging.getLogger("teach_the_tutor")
LOG.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
LOG.addHandler(handler)

# ---------- Paths & voices ----------
# Ensure this resolves to: backend/src/shared-data/day4_tutor_content.json
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
CONTENT_PATH = os.path.join(BASE_DIR, "shared-data", "day4_tutor_content.json")

# TTS voice ids (adjust if your murf plugin expects different ids)
VOICE_MAP = {
    "learn": "en-US-matthew",
    "quiz": "en-US-alicia",
    "teach_back": "en-US-ken",
}

# ---------- Content loading ----------
def load_content_from_path(path: str):
    try:
        if not os.path.exists(path):
            LOG.error("Content file missing at %s. Please create it and add geography topics.", path)
            return []
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            filtered = [
                c for c in data
                if isinstance(c, dict) and all(k in c for k in ("id", "title", "summary", "sample_question"))
            ]
            LOG.info("Loaded %d concepts from %s", len(filtered), path)
            return filtered
    except Exception as e:
        LOG.exception("Failed to read content file: %s", e)
        return []

# Small helper to produce a TTS-friendly list (no underscores or punctuation)
def spoken_topic_list(topics):
    if not topics:
        return "I have no topics loaded."
    lines = []
    for i, t in enumerate(topics, start=1):
        spoken_id = t["id"].replace("_", " ")
        # Avoid reading long summaries in the topic list
        lines.append(f"{i}. {t['title']}")
    return "Here are the available topics: " + " ".join(lines)

# ---------- State objects ----------
@dataclass
class TutorState:
    current_topic_id: Optional[str] = None
    current_topic_data: Optional[dict] = None
    mode: Literal["learn", "quiz", "teach_back"] = "learn"

    def set_topic_by_id(self, topics: list, topic_id: str) -> bool:
        topic_id = (topic_id or "").strip().lower()
        for t in topics:
            if t.get("id","").lower() == topic_id:
                self.current_topic_id = t["id"]
                self.current_topic_data = t
                return True
        return False

    def set_topic_by_text(self, topics: list, text: str) -> bool:
        text = (text or "").strip().lower()
        for t in topics:
            if t.get("id","").lower() == text or t.get("title","").lower() == text:
                self.current_topic_id = t["id"]
                self.current_topic_data = t
                return True
        for t in topics:
            if text in (t.get("title","").lower() + " " + t.get("summary","").lower()):
                self.current_topic_id = t["id"]
                self.current_topic_data = t
                return True
        return False

@dataclass
class Userdata:
    tutor_state: TutorState
    agent_session: Optional[AgentSession] = None
    course_content: list = None
    mastery: dict = None

# ---------- Tools available to the LLM ----------
@function_tool
async def select_topic(ctx: RunContext[Userdata], topic_id: str):
    topics = ctx.userdata.course_content or []
    ok = ctx.userdata.tutor_state.set_topic_by_id(topics, topic_id)
    if ok:
        title = ctx.userdata.tutor_state.current_topic_data.get("title")
        return {"success": True, "message": f"Topic selected: {title}"}
    return {"success": False, "message": f"Topic id '{topic_id}' not found."}

@function_tool
async def set_learning_mode(ctx: RunContext[Userdata], mode: str):
    mode = (mode or "").strip().lower()
    if mode not in ("learn", "quiz", "teach_back"):
        return {"success": False, "message": f"Unknown mode: {mode}"}
    ctx.userdata.tutor_state.mode = mode

    session = ctx.userdata.agent_session
    if session:
        try:
            # Try to update options if supported
            if hasattr(session.tts, "update_options"):
                session.tts.update_options(voice=VOICE_MAP[mode], style="Conversation")
            else:
                # best-effort fallback assignment
                session.tts = murf.TTS(voice=VOICE_MAP[mode], style="Conversation")
            LOG.info("Requested voice switch to %s for mode %s", VOICE_MAP[mode], mode)
        except Exception as e:
            LOG.warning("Voice switch attempted but update failed: %s", e)

    topic = ctx.userdata.tutor_state.current_topic_data
    if topic:
        if mode == "learn":
            return {"success": True, "message": f"LEARN mode. Explaining: {topic['title']}. {topic['summary']}"}
        if mode == "quiz":
            return {"success": True, "message": f"QUIZ mode. Question: {topic['sample_question']}"}
        if mode == "teach_back":
            return {"success": True, "message": f"TEACH_BACK mode. Please explain: {topic['sample_question']}"}
    return {"success": True, "message": f"Switched to {mode}. No topic selected."}

@function_tool
async def evaluate_teaching(ctx: RunContext[Userdata], user_explanation: str):
    topics = ctx.userdata.course_content or []
    state = ctx.userdata.tutor_state
    tid = state.current_topic_id
    if not tid:
        return {"score": 0.0, "feedback": "No topic selected."}
    concept = next((c for c in topics if c["id"] == tid), None)
    if concept is None:
        return {"score": 0.0, "feedback": "Topic not found."}

    def tokenize(s):
        return set([w.strip(".,?!;:()[]\"'").lower() for w in (s or "").split() if w.strip()])

    summary_tokens = tokenize(concept.get("summary",""))
    answer_tokens = tokenize(user_explanation)
    if not summary_tokens:
        score = 0.0
    else:
        overlap = summary_tokens & answer_tokens
        score = round(len(overlap) / len(summary_tokens), 3)

    if score >= 0.75:
        feedback = "Excellent — you covered most key ideas."
    elif score >= 0.4:
        feedback = "Good — some key points present but missing details."
    else:
        feedback = "Please try again; focus on definitions and examples."

    # update in-memory mastery
    mastery = ctx.userdata.mastery or {}
    rec = mastery.get(tid, {"attempts": 0, "best_score": 0.0})
    rec["attempts"] = rec.get("attempts", 0) + 1
    if score > rec.get("best_score", 0.0):
        rec["best_score"] = score
    rec["last_practice"] = datetime.utcnow().isoformat() + "Z"
    mastery[tid] = rec
    ctx.userdata.mastery = mastery

    return {"score": score, "feedback": feedback, "mastery": rec}

# ---------- Agent class ----------
class TutorAgent(Agent):
    def __init__(self, topics_list):
        # present internal topic list in LLM-friendly form (keep ids exact)
        topic_list = ", ".join([f"{t['id']} ({t['title']})" for t in topics_list])
        instructions = (
            "You are Teach-the-Tutor, a geography tutor with three modes: learn, quiz, teach_back. "
            "When the user names a topic, call select_topic with the topic id. "
            "When the user requests a mode, call set_learning_mode with 'learn', 'quiz', or 'teach_back'. "
            "In teach_back mode, after the user speaks, call evaluate_teaching with the user's explanation. "
            f"Available topics: {topic_list}."
        )
        super().__init__(instructions=instructions, tools=[select_topic, set_learning_mode, evaluate_teaching])

# ---------- Lifecycle hooks ----------
def prewarm(proc: JobProcess):
    # load vad and load content once per process (prevents double reads)
    try:
        proc.userdata["vad"] = silero.VAD.load()
    except Exception:
        LOG.exception("Failed to load silero VAD in prewarm; continuing without it.")
        proc.userdata["vad"] = None

    # load content into proc.userdata only once
    if "course_content" not in proc.userdata:
        proc.userdata["course_content"] = load_content_from_path(CONTENT_PATH)
    if "mastery" not in proc.userdata:
        proc.userdata["mastery"] = {}

# ---------- Entrypoint ----------
async def entrypoint(ctx: JobContext):
    ctx.log_context_fields = {"room": ctx.room.name}
    LOG.info("Starting Teach-the-Tutor agent for room: %s", ctx.room.name)

    # reuse prewarmed content (avoid double reading)
    topics = ctx.proc.userdata.get("course_content", [])
    mastery = ctx.proc.userdata.get("mastery", {})

    # build userdata object
    userdata = Userdata(tutor_state=TutorState(), agent_session=None, course_content=topics, mastery=mastery)

    # create session
    session = AgentSession(
        stt=deepgram.STT(model="nova-3"),
        llm=google.LLM(model="gemini-2.5-flash"),
        tts=murf.TTS(voice=VOICE_MAP["learn"], style="Conversation", text_pacing=True),
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata.get("vad"),
        preemptive_generation=True,
        userdata=userdata,
    )

    # attach session into userdata so tools can access it
    userdata.agent_session = session

    # start the agent session
    await session.start(agent=TutorAgent(topics), room=ctx.room, room_input_options=RoomInputOptions(noise_cancellation=noise_cancellation.BVC()))
    await ctx.connect()

    # Proactive greeting: produce a TTS-friendly spoken topic list (no underscores/markdown)
    try:
        spoken = spoken_topic_list(topics)
        topic_count = len(topics)
        greeting = (
            f"Hello — welcome to Teach-the-Tutor (Geography). "
            f"I have {topic_count} topics loaded. {spoken} "
            "Which topic would you like to start with? You can say a topic name or its topic id."
        )
        await session.say(greeting)
    except Exception:
        LOG.exception("session.say failed for greeting; continuing.")

    # keep running until cancellation (LiveKit will cancel on shutdown)
    try:
        while True:
            await __import__("asyncio").sleep(1)
    except asyncio.CancelledError:
        LOG.info("Entrypoint cancelled; shutting down.")
    finally:
        try:
            await session.stop()
        except Exception:
            LOG.exception("Error stopping session.")

# ---------- CLI ----------
if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm))
