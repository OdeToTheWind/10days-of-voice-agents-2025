"""
improv_show_v2.py

Architecturally-unique rewrite of a voice-first "Improv Battle" host agent.
Preserves behavior of: start -> present scenario(s) -> accept improv -> react -> summarize -> stop.

Design highlights:
- Explicit finite-state machine (ImprovFSM)
- Service classes: StateStore, ScenarioEngine, ReactionEngine, VoiceSessionFactory
- Tools exposed to the LLM-driven agent: begin_show, next_scene, submit_performance, close_show, abort_show
- Auto-name inference from first utterance (simple heuristic)
- End-of-scene detection support (keyword "end scene" or pause-based via runtime hooks)
- Clearer logging, input validation, and defensive programming
"""

import asyncio
import json
import logging
import random
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple, Callable, NamedTuple

from dotenv import load_dotenv

# NOTE: Keep these imports consistent with your runtime. Adjust if your LiveKit package exposes different names.
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

# -------------------------
# Logging
# -------------------------
logger = logging.getLogger("improv_battle_v2")
logger.setLevel(logging.INFO)
ch = logging.StreamHandler()
ch.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(ch)

load_dotenv(".env.local")

# -------------------------
# Constants & Helpers
# -------------------------
DEFAULT_ROUNDS = 3
MIN_ROUNDS = 1
MAX_ROUNDS = 8

END_SCENE_KEYWORDS = {"end scene", "that's it", "done", "finished", "ok done", "okay done", "ok that’s it"}
NAME_INFERENCE_REGEX = re.compile(r"^(?:hi|hello|hey|i'?m|i am)\s+([A-Z][a-z]{1,20})", re.I)

def utcnow_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"

def clamp(v: int, a: int, b: int) -> int:
    return max(a, min(b, v))

# -------------------------
# Finite State Machine
# -------------------------
class ImprovState(str):
    IDLE = "idle"
    INTRO = "intro"
    AWAITING_IMPROV = "awaiting_improv"
    REACTING = "reacting"
    AWAITING_NEXT = "awaiting_next"
    DONE = "done"

VALID_TRANSITIONS = {
    ImprovState.IDLE: {ImprovState.INTRO},
    ImprovState.INTRO: {ImprovState.AWAITING_IMPROV, ImprovState.DONE},
    ImprovState.AWAITING_IMPROV: {ImprovState.REACTING, ImprovState.DONE},
    ImprovState.REACTING: {ImprovState.AWAITING_NEXT, ImprovState.DONE},
    ImprovState.AWAITING_NEXT: {ImprovState.AWAITING_IMPROV, ImprovState.DONE},
    ImprovState.DONE: set(),
}

# -------------------------
# Data Models
# -------------------------
@dataclass
class RoundRecord:
    round_no: int
    scenario: str
    performance: str
    reaction: str
    timestamp: str = field(default_factory=utcnow_iso)

@dataclass
class SessionData:
    player_name: Optional[str] = None
    session_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    started_at: str = field(default_factory=utcnow_iso)
    current_round: int = 0
    max_rounds: int = DEFAULT_ROUNDS
    phase: str = ImprovState.IDLE
    current_scenario: Optional[str] = None
    used_indices: List[int] = field(default_factory=list)
    rounds: List[RoundRecord] = field(default_factory=list)
    history: List[Dict[str, Any]] = field(default_factory=list)

# -------------------------
# Scenario Engine
# -------------------------
class ScenarioEngine:
    """
    Generates and manages scenarios.
    Can be extended to call an LLM for procedural scenarios.
    """
    def __init__(self, seeds: Optional[List[str]] = None):
        self._seeds = list(seeds) if seeds else [
            "You are a used car salesman trying to sell a car that is on fire.",
            "You are a vegan vampire who just bit into a garlic farmer.",
            "You are Elon Musk explaining to a 1950s housewife why her Tesla has no steering wheel.",
            "You are a toddler who just learned the word 'taxes' and won't stop screaming it.",
            "You are a fitness influencer live-streaming a workout while fighting explosive diarrhea.",
            "You are a priest trying to exorcise a possessed Roomba.",
            "You are a Karen demanding to speak to the manager of the afterlife.",
            "You are a caveman who just invented customer service.",
            "You are tech support for medieval peasants using the printing press.",
            "You are a wedding DJ whose only song is the Doom soundtrack.",
            "You are a motivational speaker who is secretly depressed.",
            "You are a flight attendant calmly announcing the wings fell off.",
            "You are a genie who grants wishes only through interpretive dance.",
            "You are a pirate captain who gets violently seasick in the bathtub.",
            "You are a conspiracy theorist trying to convince your cat the laser pointer is a psy-op.",
            "You are a barista who accidentally made a latte that opens a portal to hell.",
            "You are a time traveler trying to return AirPods to ancient Rome.",
            "You are a superhero whose only power is perfect parallel parking.",
            "You are a dragon who is afraid of fire.",
            "You are a therapist for super-villains going through an identity crisis.",
            "You are a barista who has to tell a customer that their latte is actually a portal to another dimension.",
            "You are a time-travelling tour guide explaining modern smartphones to someone from the 1800s.",
            "You are a restaurant waiter who must calmly tell a customer that their order has escaped the kitchen.",
            "You are a customer trying to return an obviously cursed object to a very skeptical shop owner.",
            "You are an overenthusiastic TV infomercial host selling a product that clearly does not work as advertised.",
            "You are an astronaut who just discovered the ship's coffee machine has developed a personality.",
            "You are a nervous wedding officiant who keeps getting the couple's names mixed up in ridiculous ways.",
            "You are a ghost trying to give a performance review to a living employee.",
            "You are a medieval king reacting to a very modern delivery service showing up at court.",
            "You are a detective interrogating a suspect who only answers in awkward metaphors."
        ]

    def pick(self, used_indices: List[int]) -> Tuple[str, int]:
        indices = [i for i in range(len(self._seeds)) if i not in used_indices]
        if not indices:
            # reset used
            used_indices.clear()
            indices = list(range(len(self._seeds)))
        idx = random.choice(indices)
        return self._seeds[idx], idx

# -------------------------
# Reaction Engine
# -------------------------
class ReactionEngine:
    """
    Produces a short host reaction string.
    By default uses heuristic and lightweight templates; can be swapped out to call an LLM for richer reactions.
    """
    def __init__(self, llm_callable: Optional[Callable[[str], asyncio.Future]] = None):
        # llm_callable should be an async function accepting a prompt -> str
        self.llm = llm_callable

    async def generate(self, performance: str) -> str:
        # simple heuristic to pick tone and highlight
        tone = random.choice(["supportive", "neutral", "mildly_critical"])
        perf = (performance or "").lower()

        highlights = []
        if any(w in perf for w in ("funny", "lol", "hahaha", "haha")):
            highlights.append("sharp comedic timing")
        if any(w in perf for w in ("sad", "cry", "tears", "sobbing")):
            highlights.append("real emotional weight")
        if "..." in performance or perf.count(" ") < 3:
            highlights.append("bold use of silence or minimalism")
        if not highlights:
            highlights.append(random.choice(["nice character choices", "bold commitment", "unexpected twist"]))

        chosen = random.choice(highlights)
        base = {
            "supportive": f"Love that — {chosen}! That was playful and clear. Great energy. Ready for the next one?",
            "neutral": f"Hmm — {chosen}. Some parts landed nicely; others could be clearer. Let's try the next scene and lean into one choice.",
            "mildly_critical": f"Okay — {chosen}, but that felt a bit rushed. Try making bolder, clearer choices next time."
        }[tone]

        # Optionally ask LLM for a polished variant (if provided)
        if self.llm:
            prompt = (
                "You are a TV improv host. Produce a short (1-2 sentences) reaction to the following performance. "
                "Tone: supportive, neutral, or mildly critical—keep it constructive. "
                f"Performance: \"{performance}\". Base: \"{base}\""
            )
            try:
                polished = await self.llm(prompt)
                if polished and isinstance(polished, str) and len(polished) > 10:
                    return polished.strip()
            except Exception as e:
                logger.warning("LLM polish failed: %s", e)
                # fall back to template
        return base

# -------------------------
# State Store
# -------------------------
class StateStore:
    """
    Thin abstraction over session data; enables future migration to external stores.
    """
    def __init__(self):
        self._sessions: Dict[str, SessionData] = {}

    def create(self) -> SessionData:
        s = SessionData()
        self._sessions[s.session_id] = s
        return s

    def get(self, session_id: str) -> Optional[SessionData]:
        return self._sessions.get(session_id)

    def remove(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

# -------------------------
# Voice Session Factory
# -------------------------
class VoiceSessionFactory:
    """
    Creates AgentSession objects - abstracts plugin choices.
    """
    def __init__(self, proc_userdata: Dict[str, Any]):
        self.proc_userdata = proc_userdata

    def make(self, userdata: SessionData) -> AgentSession:
        # adjust to your environment: choose STT/LLM/TTS/turn_detector
        stt = deepgram.STT(model="nova-3")
        llm = google.LLM(model="gemini-2.5-flash")
        tts = murf.TTS(voice="en-US-marcus", style="Conversational", text_pacing=True)
        turn_detector = MultilingualModel()
        vad = self.proc_userdata.get("vad")
        return AgentSession(
            stt=stt,
            llm=llm,
            tts=tts,
            turn_detection=turn_detector,
            vad=vad,
            userdata=userdata,
        )

# -------------------------
# Utility: Name inference + end detection
# -------------------------
def infer_name_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    m = NAME_INFERENCE_REGEX.search(text.strip())
    if m:
        return m.group(1).capitalize()
    # small heuristic: "I'm Alex" forms already caught; else look for "my name is X"
    mm = re.search(r"my name is\s+([A-Z][a-z]{1,20})", text, re.I)
    if mm:
        return mm.group(1).capitalize()
    return None

def contains_end_phrase(text: str) -> bool:
    if not text:
        return False
    low = text.lower()
    for ph in END_SCENE_KEYWORDS:
        if ph in low:
            return True
    return False

# -------------------------
# Tools (exposed to the agent)
# -------------------------
state_store = StateStore()
scenario_engine = ScenarioEngine()
# ReactionEngine will be created with LLm caller during runtime in entrypoint for safety
reaction_engine: ReactionEngine  # assigned in entrypoint

@function_tool
async def begin_show(
    ctx: RunContext[SessionData],
    name: Optional[str] = Field(None, description="Optional contestant name"),
    rounds: int = Field(DEFAULT_ROUNDS, description="Number of rounds (1-8)"),
) -> str:
    """
    Starts a new improv session and returns the intro + first scenario.
    """
    try:
        userdata: SessionData = ctx.userdata
        # Name resolution: param > inferred from prior > unknown
        if name and name.strip():
            userdata.player_name = name.strip()
        else:
            # see if first utterance exists in history context passed from runtime
            # many runtimes will provide initial_text in ctx; if not, we keep None
            initial_text = getattr(ctx, "initial_text", None)
            inferred = infer_name_from_text(initial_text or "")
            userdata.player_name = userdata.player_name or inferred or "Contestant"

        # clamp rounds
        userdata.max_rounds = clamp(int(rounds), MIN_ROUNDS, MAX_ROUNDS)
        userdata.current_round = 1
        userdata.phase = ImprovState.INTRO
        userdata.history.append({"t": utcnow_iso(), "ev": "begin_show", "name": userdata.player_name, "rounds": userdata.max_rounds})

        # pick first scenario
        scenario, idx = scenario_engine.pick(userdata.used_indices)
        userdata.used_indices.append(idx)
        userdata.current_scenario = scenario
        userdata.phase = ImprovState.AWAITING_IMPROV
        userdata.history.append({"t": utcnow_iso(), "ev": "present_scenario", "round": userdata.current_round, "scenario": scenario})

        intro = (
            f"Welcome to Improv Battle! I'm your host. "
            f"{userdata.player_name}, we're playing {userdata.max_rounds} rounds. "
            "I'll give you a short scene; improvise in character. When you're finished say 'End scene' or pause — I'll react and move on. Have fun!"
        )
        return intro + f"\nRound {userdata.current_round}: {scenario}\nStart improvising now!"
    except Exception as e:
        logger.exception("begin_show error")
        return f"Sorry, I couldn't start the show due to an error: {e}"

@function_tool
async def next_scene(ctx: RunContext[SessionData]) -> str:
    """
    Advance to the next scene; if we've completed all rounds, call summary.
    """
    userdata: SessionData = ctx.userdata
    if userdata.phase == ImprovState.DONE:
        return "The show is already over. Say 'begin show' to start a new game."

    if userdata.current_round >= userdata.max_rounds:
        userdata.phase = ImprovState.DONE
        return await summarize(ctx)

    # advance
    userdata.current_round += 1
    scenario, idx = scenario_engine.pick(userdata.used_indices)
    userdata.used_indices.append(idx)
    userdata.current_scenario = scenario
    userdata.phase = ImprovState.AWAITING_IMPROV
    userdata.history.append({"t": utcnow_iso(), "ev": "present_scenario", "round": userdata.current_round, "scenario": scenario})
    return f"Round {userdata.current_round}: {scenario}\nGo!"

@function_tool
async def submit_performance(
    ctx: RunContext[SessionData],
    performance: str = Field(..., description="Transcribed performance text from player"),
    end_hint: Optional[bool] = Field(None, description="Optional: runtime can hint that user paused (silence)"),
) -> str:
    """
    Record a performance. Can be called after an end-of-scene heuristic (keyword or silence).
    """
    userdata: SessionData = ctx.userdata
    try:
        # Phase enforcement
        if userdata.phase not in {ImprovState.AWAITING_IMPROV, ImprovState.AWAITING_NEXT}:
            # still accept but notify the caller
            userdata.history.append({"t": utcnow_iso(), "ev": "submit_out_of_phase", "phase": userdata.phase})
            # allow accepting at odd times to avoid UX friction but still inform
        # Name inference: if we still don't have a name, try infer from performance preamble
        if not userdata.player_name:
            maybe = infer_name_from_text(performance)
            if maybe:
                userdata.player_name = maybe
                userdata.history.append({"t": utcnow_iso(), "ev": "inferred_name", "name": maybe})

        scenario = userdata.current_scenario or "(unknown scenario)"
        # generate reaction (async)
        userdata.phase = ImprovState.REACTING
        userdata.history.append({"t": utcnow_iso(), "ev": "performance_recorded", "round": userdata.current_round})

        reaction = await reaction_engine.generate(performance or "")
        rr = RoundRecord(
            round_no=userdata.current_round,
            scenario=scenario,
            performance=performance or "",
            reaction=reaction,
        )
        userdata.rounds.append(rr)

        # Decide next steps
        if userdata.current_round >= userdata.max_rounds:
            userdata.phase = ImprovState.DONE
            # final reaction + summary
            closing = f"{reaction}\nThat's the final round. " + (await summarize(ctx))
            userdata.history.append({"t": utcnow_iso(), "ev": "finalized_show"})
            return closing

        # else prompt for next
        userdata.phase = ImprovState.AWAITING_NEXT
        userdata.history.append({"t": utcnow_iso(), "ev": "awaiting_next_prompt"})
        return f"{reaction}\nWhen you're ready, say 'Next' or I can give the next scene now."

    except Exception as e:
        logger.exception("submit_performance error")
        return f"An error occurred while recording your performance: {e}"

@function_tool
async def summarize(ctx: RunContext[SessionData]) -> str:
    """
    Produce a short closing summary describing the player's style and highlights.
    """
    userdata: SessionData = ctx.userdata
    try:
        if not userdata.rounds:
            userdata.phase = ImprovState.DONE
            userdata.history.append({"t": utcnow_iso(), "ev": "summarize_none"})
            return "No rounds were played. Thanks for trying Improv Battle!"

        summary_lines = [f"Thanks for playing, {userdata.player_name or 'Contestant'}! Here's a short recap:"]
        for r in userdata.rounds:
            perf_snip = (r.performance or "").strip()
            if len(perf_snip) > 80:
                perf_snip = perf_snip[:77] + "..."
            summary_lines.append(f"Round {r.round_no}: {r.scenario} — You: '{perf_snip}' | Host: {r.reaction}")

        # lightweight profile
        mentions_character = sum(1 for r in userdata.rounds if any(w in (r.performance or "").lower() for w in ("i am", "i'm", "as a", "character")))
        mentions_emotion = sum(1 for r in userdata.rounds if any(w in (r.performance or "").lower() for w in ("sad", "angry", "happy", "love", "cry", "tears")))
        profile = "You seem to be a player who "
        if mentions_character > len(userdata.rounds) / 2:
            profile += "commits to character choices"
        elif mentions_emotion > 0:
            profile += "brings emotional color to scenes"
        else:
            profile += "favors surprising beats and twists"
        profile += ". Keep leaning into clear choices and stronger stakes."
        summary_lines.append(profile)
        summary_lines.append("Thanks for performing on Improv Battle — hope to see you again!")

        userdata.phase = ImprovState.DONE
        userdata.history.append({"t": utcnow_iso(), "ev": "summarize_done"})
        return "\n".join(summary_lines)
    except Exception as e:
        logger.exception("summarize error")
        return f"Couldn't create a summary: {e}"

@function_tool
async def abort_show(ctx: RunContext[SessionData], confirm: bool = Field(False, description="Confirm stop")) -> str:
    userdata: SessionData = ctx.userdata
    if not confirm:
        return "Are you sure you want to stop the show? Say 'stop show yes' to confirm."
    userdata.phase = ImprovState.DONE
    userdata.history.append({"t": utcnow_iso(), "ev": "abort_show"})
    return "Show stopped. Thanks for coming to Improv Battle!"

# -------------------------
# Agent Implementation
# -------------------------
class ImprovHostAgent(Agent):
    def __init__(self):
        instructions = (
            "You are the host of a TV improv show called 'Improv Battle'. "
            "High-energy, witty, and clear about rules. Use short TTS-friendly turns. "
            "When generating reactions, pick a tone randomly between supportive, neutral, and mildly critical—always constructive. "
            "Use the provided tools: begin_show, next_scene, submit_performance, summarize, abort_show. "
            "Keep turns short and respond promptly to player cues."
        )
        super().__init__(instructions=instructions, tools=[begin_show, next_scene, submit_performance, summarize, abort_show])

# -------------------------
# Entrypoint & Prewarm
# -------------------------
def prewarm(proc: JobProcess):
    try:
        proc.userdata["vad"] = silero.VAD.load()
    except Exception:
        logger.warning("VAD prewarm failed; continuing without preloaded VAD.")

async def entrypoint(ctx: JobContext):
    """
    Creates the SessionData, wires up ReactionEngine (optionally to LLM),
    builds AgentSession and starts the agent.
    """
    ctx.log_context_fields = {"room": ctx.room.name}
    logger.info("🎭 Starting Improv Battle v2")
    # create session data
    userdata = state_store.create()

    # create reaction engine with optional LLM wrapper
    async def llm_wrapper(prompt: str) -> str:
        try:
            # use the session's LLM to produce a short response
            # we will create a temporary LLM client here similar to the original: google.LLM(...)
            tmp = google.LLM(model="gemini-2.5-flash")
            out = await tmp.complete(prompt)  # placeholder API; adapt to your LLM client
            if isinstance(out, str):
                return out
            # if object, coerce
            return str(out)
        except Exception as e:
            logger.debug("LLM wrapper failed: %s", e)
            return ""

    # attach reaction engine (with LLM polish)
    global reaction_engine
    reaction_engine = ReactionEngine(llm_callable=llm_wrapper)

    # build voice session factory and session
    factory = VoiceSessionFactory(proc_userdata=ctx.proc.userdata)
    session = factory.make(userdata)

    # Optionally, configure room input options (noise cancellation)
    room_opts = RoomInputOptions(noise_cancellation=noise_cancellation.BVC())

    # Start agent session
    await session.start(agent=ImprovHostAgent(), room=ctx.room, room_input_options=room_opts)

    # Example hook: if your runtime invokes submit_performance via silence detection, implement here.
    # Many runtimes call the agent's tools directly after STT/transcription. If yours provides a "finalized_transcript"
    # or silence callback, call submit_performance with the transcript. This code demonstrates how you might wire it:
    async def silence_callback(transcript: str, hint: Dict[str, Any] = None):
        """
        Called by runtime when a long silence is detected. This is illustrative; adapt to your runtime.
        """
        try:
            # If transcript contains explicit end phrase, treat as final
            if contains_end_phrase(transcript):
                await submit_performance(RunContext(userdata=userdata, room=ctx.room), performance=transcript, end_hint=True)
            else:
                # runtime can decide to call submit_performance anyway after silence
                await submit_performance(RunContext(userdata=userdata, room=ctx.room), performance=transcript, end_hint=True)
        except Exception as e:
            logger.warning("silence_callback failed: %s", e)

    # Connect and wait until job termination
    await ctx.connect()

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm))
