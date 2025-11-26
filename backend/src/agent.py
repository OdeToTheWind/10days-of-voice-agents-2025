# backend/src/agent.py
# Day 5: Simple FAQ SDR + Lead Capture - complete agent.py
# - Loads local FAQ JSON (razorpay_faq.json)
# - Uses Google Gemini Pro as LLM
# - Exposes find_faq, update_lead_field, save_lead function-tools
# - Slot-filling flow + end-of-call detection + JSONL lead persistence

import os
import json
import re
import logging
from datetime import datetime
from dotenv import load_dotenv

# LiveKit agents imports
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    JobProcess,
    MetricsCollectedEvent,
    RoomInputOptions,
    WorkerOptions,
    cli,
    metrics,
    tokenize,
    function_tool,
    RunContext,
)
from livekit.plugins import murf, silero, deepgram, noise_cancellation, google
from livekit.plugins.turn_detector.multilingual import MultilingualModel

DEFAULT_DOMAIN_KNOWLEDGE = {
  "international_payments": "Yes, Razorpay supports international payments for eligible businesses. You can enable it via dashboard verification.",
  "settlements": "Razorpay settlements typically happen in T+2 working days by default. Faster settlement options are available for some businesses.",
  "pricing": "Razorpay uses a pay-as-you-go pricing model. Pricing varies by payment instrument.",
}

# ---------- Logging & env ----------
logger = logging.getLogger("agent")
logging.basicConfig(level=logging.INFO)
load_dotenv(".env.local")

# ---------- Paths ----------
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
SHARED_DATA_DIR = os.path.join(BASE_DIR, "shared-data")
FAQ_FILENAME = os.path.join(SHARED_DATA_DIR, "razorpay_faq.json")
LEADS_FILENAME = os.path.join(SHARED_DATA_DIR, "leads.jsonl")

# Ensure shared-data exists
os.makedirs(SHARED_DATA_DIR, exist_ok=True)

# Print resolved paths to logs (very useful for worker processes)
print(f"[agent] BASE_DIR: {BASE_DIR}")
print(f"[agent] FAQ_FILENAME: {FAQ_FILENAME}")
print(f"[agent] LEADS_FILENAME: {LEADS_FILENAME}")

# ---------- Helpers ----------

def read_json_file(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning("JSON file not found: %s", path)
        return None
    except json.JSONDecodeError as e:
        logger.exception("Invalid JSON (%s): %s", path, e)
        return None
    except Exception as e:
        logger.exception("Failed to read JSON (%s): %s", path, e)
        return None

def persist_lead(lead: dict) -> bool:
    """Append lead as a single-line JSON record."""
    try:
        os.makedirs(os.path.dirname(LEADS_FILENAME), exist_ok=True)
        if "raw_timestamp" not in lead or not lead.get("raw_timestamp"):
            lead["raw_timestamp"] = datetime.utcnow().isoformat() + "Z"
        with open(LEADS_FILENAME, "a", encoding="utf-8") as f:
            f.write(json.dumps(lead, ensure_ascii=False) + "\n")
        logger.info("Persisted lead: name=%s email=%s company=%s", lead.get("name"), lead.get("email"), lead.get("company"))
        return True
    except Exception as e:
        logger.exception("Failed to persist lead: %s", e)
        return False

def is_call_end(text: str) -> bool:
    """Detect end-of-call phrases (simple heuristic)."""
    if not text:
        return False
    return bool(re.search(r"\b(thats all|that's all|i'm done|i am done|that is all|thanks|thank you|bye)\b", text.lower()))

def extract_email(text: str) -> str:
    m = re.search(r"([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)", text)
    return m.group(1) if m else ""

# ---------- SDR Agent ----------

class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are an SDR (Sales Development Representative) voice agent representing Razorpay.\n"
                "You must: Greet warmly, ask what brought the visitor here, discover the user's needs, answer only using the provided FAQ via the find_faq tool, and collect lead fields.\n\n"
                "Lead fields to collect: name, company, email, role, use_case, team_size, timeline.\n"
                "Use the update_lead_field tool to store fields while the call is active. When the conversation ends say a short summary and call save_lead (or let the system auto-save on turn end).\n"
                "When answering product/pricing/company questions call find_faq(query) and return the tool's response. Do not invent facts not in the FAQ.\n"
                "Keep replies short, friendly, and helpful. Ask clarifying questions when needed to complete lead fields."
            )
        )

    # --- function tools ---

    @function_tool
    async def find_faq(self, context: RunContext, query: str) -> str:
        """
        Simple keyword search over loaded FAQ (proc.userdata['company_faq']).
        Returns best matching FAQ answer or a company description fallback.
        """
        faq = context.userdata.get("company_faq")
        if not faq:
            return "Sorry, I cannot access the FAQ right now."
        q = (query or "").strip().lower()
        if not q:
            return faq.get("company", {}).get("description", "No company description available.")
        # Simple keyword scoring
        query_words = [w for w in re.findall(r"\w+", q) if len(w) > 1]
        best = None
        best_score = 0
        for entry in faq.get("faqs", []):
            combined = (entry.get("q", "") + " " + entry.get("a", "")).lower()
            score = sum(1 for w in query_words if w in combined)
            if q in combined:
                score += 1
            if score > best_score:
                best_score = score
                best = entry
        if best and best_score > 0:
            return best.get("a", "")
        # fallback description
        return faq.get("company", {}).get("description", "Sorry, I couldn't find that in the FAQ.")

    @function_tool
    async def update_lead_field(self, context: RunContext, field: str, value: str) -> dict:
        """
        Update an in-progress lead stored in proc.userdata['current_lead'].
        Returns the updated lead dict.
        """
        if "current_lead" not in context.userdata:
            context.userdata["current_lead"] = {}
        lead = context.userdata["current_lead"]
        # Basic normalization
        field = (field or "").strip().lower()
        if field in ("email",) and not extract_email(value):
            # if no clear email, attempt to extract
            possible = extract_email(value)
            if possible:
                value = possible
        lead[field] = value or ""
        context.userdata["current_lead"] = lead
        logger.info("Updated lead field: %s=%s", field, value)
        return {"lead": lead}

    @function_tool
    async def save_lead(
        self,
        context: RunContext,
        name: str = None,
        company: str = None,
        email: str = None,
        role: str = None,
        use_case: str = None,
        team_size: str = None,
        timeline: str = None,
    ) -> dict:
        """
        Save the lead to disk. Merge with in-progress lead if available.
        Returns {"saved": bool, "lead": lead}.
        """
        current = context.userdata.get("current_lead") or {}
        lead = {
            "name": name or current.get("name", ""),
            "company": company or current.get("company", ""),
            "email": email or current.get("email", ""),
            "role": role or current.get("role", ""),
            "use_case": use_case or current.get("use_case", ""),
            "team_size": team_size or current.get("team_size", ""),
            "timeline": timeline or current.get("timeline", ""),
            "raw_timestamp": datetime.utcnow().isoformat() + "Z",
        }
        ok = persist_lead(lead)
        if ok:
            # clear in-progress after save
            context.userdata["current_lead"] = {}
        return {"saved": ok, "lead": lead}

# ---------- Prewarm (VAD + FAQ load) ----------

def prewarm(proc: JobProcess):
    # VAD
    try:
        proc.userdata["vad"] = silero.VAD.load()
        logger.info("Loaded silero VAD")
    except Exception:
        logger.exception("Failed to load silero VAD; continuing without it")
        proc.userdata["vad"] = None

    # FAQ load
    faq = read_json_file(FAQ_FILENAME)
    if faq is None:
        logger.warning("FAQ not found at prewarm: %s", FAQ_FILENAME)
    proc.userdata["company_faq"] = faq
    proc.userdata["current_lead"] = {}

# ---------- Entrypoint: build session ----------

async def entrypoint(ctx: JobContext):
    ctx.log_context_fields = {"room": ctx.room.name}

    # Build session: STT, LLM (Gemini Pro), TTS
    session = AgentSession(
        stt=deepgram.STT(model="nova-3"),
        llm=google.LLM(model="gemini-2.5-pro"),
        tts=murf.TTS(
            voice="en-US-matthew",
            style="Conversation",
            tokenizer=tokenize.basic.SentenceTokenizer(min_sentence_len=2),
            text_pacing=True,
        ),
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata.get("vad"),
        preemptive_generation=True,
    )

    usage_collector = metrics.UsageCollector()

    @session.on("metrics_collected")
    def _on_metrics_collected(ev: MetricsCollectedEvent):
        metrics.log_metrics(ev.metrics)
        usage_collector.collect(ev.metrics)

    async def log_usage():
        summary = usage_collector.get_summary()
        logger.info("Usage summary: %s", summary)

    ctx.add_shutdown_callback(log_usage)

    # Best-effort: on turn_end, detect end-of-call and trigger save if needed
    try:
        @session.on("turn_end")
        async def _on_turn_end(turn_info):
            try:
                last_text = getattr(turn_info, "transcript", "") or getattr(turn_info, "last_transcript", "") or ""
                if last_text and is_call_end(last_text):
                    current = ctx.proc.userdata.get("current_lead", {})
                    if current:
                        # persist and log
                        ok = persist_lead(current)
                        if ok:
                            # Optionally: summarise via session (left as log to avoid double TTS calls)
                            logger.info("Auto-saved lead at turn_end: %s", {k: current.get(k) for k in ("name","email","company")})
            except Exception:
                logger.exception("Error in turn_end handler")
    except Exception:
        logger.info("Could not attach turn_end handler (SDK may not support it)")

    # Start session with our Assistant
    await session.start(
        agent=Assistant(),
        room=ctx.room,
        room_input_options=RoomInputOptions(noise_cancellation=noise_cancellation.BVC()),
    )

    await ctx.connect()

# ---------- CLI ----------

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm))
