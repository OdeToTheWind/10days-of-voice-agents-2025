# backend/src/agent.py
import os
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Annotated

from dotenv import load_dotenv
from pydantic import Field

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

logger = logging.getLogger("agent")
logging.basicConfig(level=logging.INFO)
load_dotenv(".env.local")

# ---------- Configuration ----------
BANK_NAME = "Demo Bank"
# This file will be created/initialized under the same folder as this agent file
FRAUD_DB_FILE = os.path.join(os.path.dirname(__file__), "shared-data", "fraud_cases.json")

# ---------- Sample data (safe/fake) ----------
SAMPLE_FRAUD_CASES = [
    {
        "id": 1,
        "userName": "John",
        "securityIdentifier": "12345",
        "cardEnding": "4242",
        "status": "pending_review",
        "transactionAmount": "₹4999",
        "transactionName": "ABC Electronics Ltd",
        "transactionTime": "2025-11-27 14:23:15",
        "transactionCategory": "e-commerce",
        "transactionSource": "alibaba.com",
        "transactionLocation": "Shenzhen, China",
        "securityQuestion": "What is your mother's maiden name?",
        "securityAnswer": "2345",
        "outcomeNote": ""
    }
]


# ---------- JSON DB helpers ----------
def ensure_data_folder():
    folder = os.path.dirname(FRAUD_DB_FILE)
    os.makedirs(folder, exist_ok=True)


def init_database():
    """Create JSON DB file and populate with sample cases if missing/empty."""
    ensure_data_folder()
    if not os.path.exists(FRAUD_DB_FILE):
        with open(FRAUD_DB_FILE, "w") as f:
            json.dump(SAMPLE_FRAUD_CASES, f, indent=2)
        logger.info(f"Initialized fraud DB with sample cases at {FRAUD_DB_FILE}")
        return

    # If file exists but is empty or invalid, populate sample
    try:
        with open(FRAUD_DB_FILE, "r") as f:
            data = json.load(f)
        if not isinstance(data, list) or len(data) == 0:
            with open(FRAUD_DB_FILE, "w") as f:
                json.dump(SAMPLE_FRAUD_CASES, f, indent=2)
            logger.info("Re-populated fraud DB with sample cases (was empty/invalid).")
    except Exception:
        with open(FRAUD_DB_FILE, "w") as f:
            json.dump(SAMPLE_FRAUD_CASES, f, indent=2)
        logger.info("Created/Reset fraud DB with sample cases (file was invalid).")


def load_all_cases():
    ensure_data_folder()
    try:
        with open(FRAUD_DB_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []


def save_all_cases(cases):
    ensure_data_folder()
    with open(FRAUD_DB_FILE, "w") as f:
        json.dump(cases, f, indent=2)


def find_case_by_username(username: str):
    cases = load_all_cases()
    username_lower = username.strip().lower()
    for c in cases:
        if (c.get("userName") or "").strip().lower() == username_lower and c.get("status") == "pending_review":
            return c
    return None


# ---------- Userdata dataclasses ----------
@dataclass
class FraudCaseData:
    id: Optional[int] = None
    userName: Optional[str] = None
    securityIdentifier: Optional[str] = None
    cardEnding: Optional[str] = None
    status: Optional[str] = None
    transactionAmount: Optional[str] = None
    transactionName: Optional[str] = None
    transactionTime: Optional[str] = None
    transactionCategory: Optional[str] = None
    transactionSource: Optional[str] = None
    transactionLocation: Optional[str] = None
    securityQuestion: Optional[str] = None
    securityAnswer: Optional[str] = None
    outcomeNote: Optional[str] = None
    verification_passed: bool = False
    case_loaded: bool = False


@dataclass
class Userdata:
    fraud_case: FraudCaseData


# ---------- Tools (exposed to the LLM) ----------
@function_tool
async def load_fraud_case_for_user(
    ctx: RunContext[Userdata],
    user_name: Annotated[str, Field(description="The name provided by the user")]
) -> str:
    """
    Load a pending fraud case by username from JSON DB.
    Returns a short confirmation string for the agent to speak.
    """
    logger.info(f"Tool: load_fraud_case_for_user -> {user_name}")
    fc = ctx.userdata.fraud_case

    case = find_case_by_username(user_name)
    if not case:
        return f"I'm sorry, I couldn't find any pending fraud alerts for {user_name}. Could you please verify the spelling of your name?"

    # Load fields into userdata
    fc.id = case.get("id")
    fc.userName = case.get("userName")
    fc.securityIdentifier = case.get("securityIdentifier")
    fc.cardEnding = case.get("cardEnding")
    fc.status = case.get("status")
    fc.transactionAmount = case.get("transactionAmount")
    fc.transactionName = case.get("transactionName")
    fc.transactionTime = case.get("transactionTime")
    fc.transactionCategory = case.get("transactionCategory")
    fc.transactionSource = case.get("transactionSource")
    fc.transactionLocation = case.get("transactionLocation")
    fc.securityQuestion = case.get("securityQuestion")
    fc.securityAnswer = case.get("securityAnswer")
    fc.outcomeNote = case.get("outcomeNote") or ""
    fc.case_loaded = True

    logger.info(f"Loaded case ID {fc.id} for user {fc.userName}")
    return f"I found a pending alert for {fc.userName}. The card ends with {fc.cardEnding}. Before I share details, I need to verify your identity."


@function_tool
async def verify_customer_identity(
    ctx: RunContext[Userdata],
    security_answer: Annotated[str, Field(description="Answer to the security question")]
) -> str:
    """
    Verify security question answer. Sets verification_passed flag in userdata.
    """
    logger.info("Tool: verify_customer_identity")
    fc = ctx.userdata.fraud_case
    if not fc.case_loaded:
        return "I need to load your fraud case first. What name is on the account?"

    provided = (security_answer or "").strip().lower()
    expected = (fc.securityAnswer or "").strip().lower()

    if provided == expected and expected != "":
        fc.verification_passed = True
        logger.info("Verification passed")
        return "Thank you — identity verified. I can now discuss the suspicious transaction with you."
    else:
        fc.verification_passed = False
        logger.info("Verification failed")
        # Update status to verification_failed in JSON DB
        cases = load_all_cases()
        for c in cases:
            if c.get("id") == fc.id:
                c["status"] = "pending_review"
                c["outcomeNote"] = "Verification failed during call."
                c["updated_at"] = datetime.utcnow().isoformat()
        save_all_cases(cases)
        return "I'm sorry, but that answer doesn't match our records. For your security, I cannot proceed further. Please contact our customer service line for help."


@function_tool
async def mark_transaction_status(
    ctx: RunContext[Userdata],
    user_confirmed: Annotated[bool, Field(description="True if user confirms they made the transaction")],
    additional_notes: Annotated[str, Field(description="Optional notes from the conversation")] = ""
) -> str:
    """
    Mark the fraud case as confirmed_safe or confirmed_fraud in JSON DB.
    """
    logger.info("Tool: mark_transaction_status")
    fc = ctx.userdata.fraud_case
    if not fc.case_loaded or fc.id is None:
        return "No fraud case loaded — cannot update status."

    if not fc.verification_passed:
        return "I cannot update the case without successful identity verification."

    cases = load_all_cases()
    matched = False
    for c in cases:
        if c.get("id") == fc.id:
            matched = True
            if user_confirmed:
                c["status"] = "confirmed_safe"
                c["outcomeNote"] = f"Customer confirmed transaction as legitimate. {additional_notes}".strip()
            else:
                c["status"] = "confirmed_fraud"
                c["outcomeNote"] = f"Customer denied the transaction. Fraud reported. {additional_notes}".strip()
            c["updated_at"] = datetime.utcnow().isoformat()
            # Update userdata copy
            fc.status = c["status"]
            fc.outcomeNote = c["outcomeNote"]
            break

    if not matched:
        return "Could not find the case in the database to update."

    save_all_cases(cases)

    if user_confirmed:
        return (
            f"Thanks — I've marked the transaction as legitimate. "
            f"Card ending in {fc.cardEnding} remains active. Is there anything else I can help you with?"
        )
    else:
        return (
            f"I've marked this transaction as fraudulent and taken protective actions (mock). "
            f"Card ending in {fc.cardEnding} has been blocked and a dispute raised for {fc.transactionAmount}. "
            "You will receive a confirmation message shortly."
        )


@function_tool
async def end_fraud_call(
    ctx: RunContext[Userdata]
) -> str:
    """
    Produce a final summary to read to the customer.
    """
    logger.info("Tool: end_fraud_call")
    fc = ctx.userdata.fraud_case
    summary = (
        f"Thank you for your time. Summary for {fc.userName or 'customer'}:\n"
        f"- Transaction: {fc.transactionAmount} at {fc.transactionName}\n"
        f"- Case status: {fc.status}\n"
        f"- Action taken: {fc.outcomeNote}\n"
        f"At {BANK_NAME}, your security is our priority. If you need further assistance, please call our support line."
    )
    return summary


# ---------- Agent definition ----------
class FraudAlertAgent(Agent):
    def __init__(self):
        super().__init__(
            instructions=f"""
You are a calm, professional Fraud Alert Representative for {BANK_NAME}.
Follow the secure flow:
1) Greet the customer and ask who you're speaking with.
2) Use the tool `load_fraud_case_for_user` to load their pending fraud case.
3) Ask the security question and call `verify_customer_identity` with the answer.
4) If verified, disclose the suspicious transaction (merchant, amount, masked card ending, time).
5) Ask if they made the transaction. If yes, call `mark_transaction_status(user_confirmed=True)`.
   If no, call `mark_transaction_status(user_confirmed=False)` and explain mock protective actions.
6) When finished, call `end_fraud_call` to summarize and close.

Security: NEVER ask for full card numbers, PINs, OTPs, CVV, passwords, or other sensitive secrets.
Keep everything fictional and use only the database-provided security question for verification.
            """,
            tools=[load_fraud_case_for_user, verify_customer_identity, mark_transaction_status, end_fraud_call],
        )


# ---------- Prewarm & entrypoint ----------
def prewarm(proc: JobProcess):
    """Preload models / initialize DB before the agent starts"""
    proc.userdata["vad"] = silero.VAD.load()
    init_database()
    logger.info("Prewarm complete: VAD loaded and DB initialized")


async def entrypoint(ctx: JobContext):
    """Main entrypoint used by the LiveKit worker"""
    ctx.log_context_fields = {"room": ctx.room.name}
    userdata = Userdata(fraud_case=FraudCaseData())

    session = AgentSession(
        stt=deepgram.STT(model="nova-3"),
        llm=google.LLM(model="gemini-2.5-flash"),
        tts=murf.TTS(
            voice="en-US-terrell",
            style="Conversation",
            text_pacing=True,
        ),
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
        userdata=userdata,
    )

    # Start the session using the LLM-driven agent
    await session.start(
        agent=FraudAlertAgent(),
        room=ctx.room,
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )

    # Connect and let the agent handle the rest (LLM + tools will run)
    await ctx.connect()


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm))
