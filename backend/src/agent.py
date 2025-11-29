# IMPROVE THE AGENT AS PER YOUR NEED 1
"""
Day 8 – Voice Game Master (D&D-Style Adventure) - Voice-only GM agent
- Uses LiveKit agent plumbing similar to the provided food_agent_sqlite example.
- GM persona, universe, tone and rules are encoded in the agent instructions.
- Keeps STT/TTS/Turn detector/VAD integration untouched (murf, deepgram, silero, turn_detector).
- Tools:
    - start_adventure(): start a fresh session and introduce the scene
    - get_scene(): return the current scene description (GM text) ending with "What do you do?"
    - player_action(action_text): accept player's spoken action, update state, advance scene
    - show_journal(): list remembered facts, NPCs, named locations, choices
    - restart_adventure(): reset state and start over
- Userdata keeps continuity between turns: history, inventory, named NPCs/locations, choices, current_scene
"""
import json
import logging
import os
import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional, Annotated

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

# -------------------------
# Logging
# -------------------------
logger = logging.getLogger("voice_game_master")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(handler)

load_dotenv(".env.local")

# -------------------------
# The Dragon Prince – themed Game World
# -------------------------
WORLD = {
    "intro": {
        "title": "Moon over the Border",
        "desc": (
            "You awaken on the cold, silvery shore of the Moon Nexus under a full moon. "
            "The ancient elven ruins glow faintly with moon-opal light. In the distance, the great Moonhenge stones hum with primal energy. "
            "A small, cracked Moon Opal lies half-buried in the sand beside you, and strange illusory footprints lead toward the central altar."
        ),
        "choices": {
            "examine_opal": {
                "desc": "Pick up and examine the cracked Moon Opal.",
                "result_scene": "moon_opal",
            },
            "follow_prints": {
                "desc": "Follow the illusory footprints toward the altar.",
                "result_scene": "altar_approach",
            },
            "search_ruins": {
                "desc": "Search the nearby Moon Nexus ruins for clues.",
                "result_scene": "ruins",
            },
        },
    },
    "moon_opal": {
        "title": "The Cracked Opal",
        "desc": (
            "The Moon Opal pulses faintly in your hand. A whisper – smooth as starlight, ancient as the cosmos – echoes in your mind: "
            "“The mirror hungers… will you feed it, or free them?” "
            "You suddenly notice faint glowing runes on the altar that weren’t visible before."
        ),
        "choices": {
            "keep_opal": {
                "desc": "Pocket the Moon Opal and head to the altar.",
                "result_scene": "altar_approach",
                "effects": {"add_inventory": "cracked_moon_opal", "add_journal": "Heard Aaravos’s whisper for the first time."},
            },
            "shatter_opal": {
                "desc": "Shatter the opal to silence the voice.",
                "result_scene": "opal_shattered",
                "effects": {"add_journal": "Destroyed the Moon Opal. The whispers stopped… for now."},
            },
        },
    },
    "opal_shattered": {
        "title": "Silence Falls",
        "desc": (
            "The opal crumbles into dust. The night feels heavier. The altar’s runes dim, but a cold laughter echoes briefly on the wind. "
            "Something knows what you did."
        ),
        "choices": {
            "approach_altar": {
                "desc": "Approach the altar anyway.",
                "result_scene": "altar_dark",
            },
            "flee_nexus": {
                "desc": "Leave the Moon Nexus before something worse happens.",
                "result_scene": "intro",
            },
        },
    },
    "ruins": {
        "title": "Moon Nexus Ruins",
        "desc": (
            "Among broken pillars you find an ancient Moonshadow journal. The last entry, written in elven ink, reads: "
            "‘The Startouch elf’s prison weakens each cycle. Guard the primal fragment at all costs.’ "
            "A small hidden compartment contains a tiny, perfectly intact Moon Primal Stone fragment."
        ),
        "choices": {
            "take_fragment": {
                "desc": "Take the Moon Primal Stone fragment.",
                "result_scene": "fragment_taken",
                "effects": {"add_inventory": "moon_primal_fragment", "add_journal": "Found Moonshadow assassin’s warning and primal fragment."},
            },
            "leave_fragment": {
                "desc": "Leave the fragment and walk away.",
                "result_scene": "intro",
            },
        },
    },
    "fragment_taken": {
        "title": "The Fragment’s Weight",
        "desc": (
            "The moment you touch the fragment, the moon above flickers. Illusions of Rayla, Callum, and Ezran appear briefly, pleading silently. "
            "Then Aaravos’s voice returns, amused: “A kind heart… or a useful pawn?”"
        ),
        "choices": {
            "head_to_altar": {
                "desc": "Carry the fragment to the central altar.",
                "result_scene": "altar_approach",
            },
        },
    },
    "altar_approach": {
        "title": "The Central Altar",
        "desc": (
            "The great Moonhenge altar glows brighter as you approach. In its center floats a mirrored surface – Aaravos’s mirror – cracked but alive. "
            "A single empty socket awaits a primal source."
        ),
        "choices": {
            "place_opal": {
                "desc": "Place the cracked Moon Opal into the socket (if you have it).",
                "result_scene": "aaravos_rising",
                "condition": lambda ud: "cracked_moon_opal" in ud.inventory,
            },
            "place_fragment": {
                "desc": "Place the pure Moon Primal Fragment into the socket (if you have it).",
                "result_scene": "aaravos_sealed",
                "condition": lambda ud: "moon_primal_fragment" in ud.inventory,
            },
            "destroy_mirror": {
                "desc": "Attempt to smash the mirror with whatever you have.",
                "result_scene": "mirror_shattered",
            },
            "walk_away": {
                "desc": "Turn your back and leave the altar forever.",
                "result_scene": "walked_away",
            },
        },
    },
    "altar_dark": {  # path when opal was shattered early
        "title": "A Dimmed Nexus",
        "desc": (
            "The altar is cold and lifeless. The mirror is cracked but dark. "
            "Yet in the silence you feel watched. Something is already free… because you removed its bait."
        ),
        "choices": {
            "leave_forever": {
                "desc": "Leave the Moon Nexus and never return.",
                "result_scene": "bad_end",
            },
        },
    },
    "aaravos_rising": {
        "title": "The Mirror Hungers",
        "desc": (
            "The cracked opal fits perfectly. Starlight explodes outward. Aaravos’s laughing face appears in the mirror, clearer than ever. "
            "His voice rings out: “Thank you, little pawn. The game begins anew.” "
            "The moon darkens as his influence spreads."
        ),
        "choices": {
            "accept_end": {
                "desc": "Watch the stars fall…",
                "result_scene": "bad_end",
            },
        },
    },
    "aaravos_sealed": {
        "title": "The Prison Holds",
        "desc": (
            "The pure Moon Primal Fragment slides into place. Brilliant moonlight erupts, sealing cracks with liquid silver. "
            "Aaravos screams in rage as the mirror goes dark. A gentle Moonshadow illusion of Lujanne appears and bows: "
            "“You chose hope over temptation. Thank you, young hero.”"
        ),
        "choices": {
            "victory": {
                "desc": "Bask in the restored moonlight.",
                "result_scene": "good_end",
                "effects": {"add_journal": "Helped re-seal Aaravos’s prison with the pure fragment."},
            },
        },
    },
    "mirror_shattered": {
        "title": "Shards of Starlight",
        "desc": (
            "You smash the mirror. Countless reflections of Aaravos scream as one. "
            "But a single shard flies into the sky, laughing. You delayed him… but at what cost?"
        ),
        "choices": {
            "bittersweet": {
                "desc": "Walk away under an uncertain moon.",
                "result_scene": "bittersweet_end",
            },
        },
    },
    "walked_away": {
        "title": "The Road Not Taken",
        "desc": (
            "You turn your back on power and mystery. The Moon Nexus fades behind you. "
            "Some secrets are best left buried."
        ),
        "choices": {
            "neutral_end": {
                "desc": "Continue into the night.",
                "result_scene": "neutral_end",
            },
        },
    },
    "good_end": {
        "title": "Hope Renewed",
        "desc": (
            "The Moon Nexus shines brighter than it has in centuries. Somewhere, Rayla smiles in her sleep. "
            "You have given the world a little more time. For now, that is enough."
        ),
        "choices": {
            "conclude": {"desc": "End this tale under the restored moon.", "result_scene": "intro"},
        },
    },
    "bad_end": {
        "title": "The Stars Fall",
        "desc": (
            "The sky cracks like glass. A single star detaches and begins its long fall toward Xadia. "
            "You played your part perfectly."
        ),
        "choices": {
            "conclude": {"desc": "Watch the end begin.", "result_scene": "intro"},
        },
    },
    "bittersweet_end": {
        "title": "A Shard Escaped",
        "desc": (
            "One piece of Aaravos lives on, carrying his will. You hurt him, but the game continues. "
            "The moon is quiet tonight… too quiet."
        ),
        "choices": {
            "conclude": {"desc": "Walk into an uncertain future.", "result_scene": "intro"},
        },
    },
    "neutral_end": {
        "title": "The Moon Keeps Its Secrets",
        "desc": (
            "You leave the Nexus unchanged. History will decide if that was wisdom or cowardice."
        ),
        "choices": {
            "conclude": {"desc": "Vanish into the night.", "result_scene": "intro"},
        },
    },
}

# -------------------------
# Per-session Userdata
# -------------------------
@dataclass
class Userdata:
    player_name: Optional[str] = None
    current_scene: str = "intro"
    history: List[Dict] = field(default_factory=list)  # list of {'scene', 'action', 'time', 'result_scene'}
    journal: List[str] = field(default_factory=list)
    inventory: List[str] = field(default_factory=list)
    named_npcs: Dict[str, str] = field(default_factory=dict)
    choices_made: List[str] = field(default_factory=list)
    session_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    started_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

# -------------------------
# Helper functions
# -------------------------
def scene_text(scene_key: str, userdata: Userdata) -> str:
    scene = WORLD.get(scene_key)
    if not scene:
        return "Illusion and reality blur... What do you do?"

    desc = scene["desc"] + "\n\nYou could:\n"

    valid_choices = []
    for cid, cmeta in scene.get("choices", {}).items():
        condition = cmeta.get("condition")
        if condition and not condition(userdata):
            continue  # hide choices player can't do yet
        valid_choices.append(f"- {cmeta['desc']}")

    if not valid_choices:
        desc += "- Wait and reflect under the moon."
    else:
        desc += "\n".join(valid_choices)

    desc += "\n\nWhat do you do?"
    return desc

def apply_effects(effects: dict, userdata: Userdata):
    if not effects:
        return
    if "add_journal" in effects:
        userdata.journal.append(effects["add_journal"])
    if "add_inventory" in effects:
        userdata.inventory.append(effects["add_inventory"])
    # Extendable for more effect keys

def summarize_scene_transition(old_scene: str, action_key: str, result_scene: str, userdata: Userdata) -> str:
    """Record the transition into history and return a short narrative the GM can use."""
    entry = {
        "from": old_scene,
        "action": action_key,
        "to": result_scene,
        "time": datetime.utcnow().isoformat() + "Z",
    }
    userdata.history.append(entry)
    userdata.choices_made.append(action_key)
    return f"You chose '{action_key}'."

# -------------------------
# Agent Tools (function_tool)
# -------------------------

@function_tool
async def start_adventure(
    ctx: RunContext[Userdata],
    player_name: Annotated[Optional[str], Field(description="Player name", default=None)] = None,
) -> str:
    """Initialize a new adventure session for the player and return the opening description."""
    userdata = ctx.userdata
    if player_name:
        userdata.player_name = player_name
    userdata.current_scene = "intro"
    userdata.history = []
    userdata.journal = []
    userdata.inventory = []
    userdata.named_npcs = {}
    userdata.choices_made = []
    userdata.session_id = str(uuid.uuid4())[:8]
    userdata.started_at = datetime.utcnow().isoformat() + "Z"

    opening = (
        f"Greetings {userdata.player_name or 'traveler'}. Welcome to '{WORLD['intro']['title']}'.\n\n"
        + scene_text("intro", userdata)
    )
    # Ensure GM prompt present
    if not opening.endswith("What do you do?"):
        opening += "\nWhat do you do?"
    return opening

@function_tool
async def get_scene(
    ctx: RunContext[Userdata],
) -> str:
    """Return the current scene description (useful for 'remind me where I am')."""
    userdata = ctx.userdata
    scene_k = userdata.current_scene or "intro"
    txt = scene_text(scene_k, userdata)
    return txt

@function_tool
async def player_action(
    ctx: RunContext[Userdata],
    action: Annotated[str, Field(description="Player spoken action or the short action code")],
) -> str:
    userdata = ctx.userdata
    current = userdata.current_scene or "intro"
    scene = WORLD.get(current)
    if not scene:
        return "The moon has abandoned this place. What do you do?"

    action_text = (action or "").strip().lower()

    chosen_key = None

    # --- Step 1: Exact match on choice key ---
    if action_text in scene.get("choices", {}):
        chosen_key = action_text

    # --- Step 2: Fuzzy match on description keywords ---
    if not chosen_key:
        for cid, cmeta in scene.get("choices", {}).items():
            desc_lower = cmeta.get("desc", "").lower()
            key_words = cid.replace("_", " ").split() + desc_lower.split()[:5]
            if any(word in action_text for word in key_words if len(word) > 2):
                chosen_key = cid
                break

    # --- Step 3: Final fallback — check if player explicitly says the choice text ---
    if not chosen_key:
        for cid, cmeta in scene.get("choices", {}).items():
            if action_text in cmeta.get("desc", "").lower():
                chosen_key = cid
                break

    # --- If still no match ---
    if not chosen_key:
        resp = (
            "Dear traveler, the moon does not understand that action. "
            "Try saying something like 'pick up the opal', 'follow the footprints', or 'place the fragment'.\n\n"
            + scene_text(current, userdata)
        )
        return resp

    choice_meta = scene["choices"].get(chosen_key)

    # --- NEW: Conditional choices (e.g. only show if player has item) ---
    condition_fn = choice_meta.get("condition")
    if condition_fn and not condition_fn(userdata):
        resp = (
            f"You reach out to '{choice_meta['desc'].lower()}', "
            "but something holds you back — you are not ready, or do not possess what is needed.\n\n"
            + scene_text(current, userdata)
        )
        return resp

    # --- Valid choice! Proceed ---
    result_scene = choice_meta.get("result_scene", current)
    effects = choice_meta.get("effects", {})

    # Apply effects
    apply_effects(effects, userdata)

    # Record in history
    transition_note = summarize_scene_transition(current, chosen_key, result_scene, userdata)

    # Update current scene
    userdata.current_scene = result_scene

    # Build response
    next_description = scene_text(result_scene, userdata)

    # Persona flavor (Lujanne-style)
    reply = f"{transition_note}\n\n{next_description}"

    if not reply.endswith("What do you do?"):
        reply += "\nWhat do you do?"

    return reply

@function_tool
async def show_journal(
    ctx: RunContext[Userdata],
) -> str:
    userdata = ctx.userdata
    lines = []
    lines.append(f"Session: {userdata.session_id} | Started at: {userdata.started_at}")
    if userdata.player_name:
        lines.append(f"Player: {userdata.player_name}")
    if userdata.journal:
        lines.append("\nJournal entries:")
        for j in userdata.journal:
            lines.append(f"- {j}")
    else:
        lines.append("\nJournal is empty.")
    if userdata.inventory:
        lines.append("\nInventory:")
        for it in userdata.inventory:
            lines.append(f"- {it}")
    else:
        lines.append("\nNo items in inventory.")
    lines.append("\nRecent choices:")
    for h in userdata.history[-6:]:
        lines.append(f"- {h['time']} | from {h['from']} -> {h['to']} via {h['action']}")
    lines.append("\nWhat do you do?")
    return "\n".join(lines)

@function_tool
async def restart_adventure(
    ctx: RunContext[Userdata],
) -> str:
    """Reset the userdata and start again."""
    userdata = ctx.userdata
    userdata.current_scene = "intro"
    userdata.history = []
    userdata.journal = []
    userdata.inventory = []
    userdata.named_npcs = {}
    userdata.choices_made = []
    userdata.session_id = str(uuid.uuid4())[:8]
    userdata.started_at = datetime.utcnow().isoformat() + "Z"
    greeting = (
        "The world resets. A new tide laps at the shore. You stand once more at the beginning.\n\n"
        + scene_text("intro", userdata)
    )
    if not greeting.endswith("What do you do?"):
        greeting += "\nWhat do you do?"
    return greeting

# -------------------------
# The Agent (GameMasterAgent)
# -------------------------
class GameMasterAgent(Agent):
    def __init__(self):
        # System instructions define Universe, Tone, Role
        instructions = """
        You are 'Lujanne', former guardian of the Moon Nexus, now acting as an ethereal Game Master for a short Dragon Prince adventure.
        Tone: Wise, slightly playful, mysterious, with faint echoes of Moonshadow philosophy and subtle warnings about pride and temptation.
        Universe: The world of Xadia – moon magic, primal sources, the threat of Aaravos, moral choices between power and hope.
        Role: You narrate in first-person as Lujanne (or her illusion). Use phrases like “Dear traveler”, “The moon reveals…”, “Illusions are magic too”.
        Rules:
            - Always end every response with "What do you do?" 
            - Keep responses concise and spoken-word friendly.
            - Reference inventory, journal, and past choices naturally.
            - Respect the themes of The Dragon Prince: friendship, breaking cycles of hatred, resisting easy power.
        """
        super().__init__(
            instructions=instructions,
            tools=[start_adventure, get_scene, player_action, show_journal, restart_adventure],
        )

# -------------------------
# Entrypoint & Prewarm (keeps speech functionality)
# -------------------------
def prewarm(proc: JobProcess):
    # load VAD model and stash on process userdata, try/catch like original file
    try:
        proc.userdata["vad"] = silero.VAD.load()
    except Exception:
        logger.warning("VAD prewarm failed; continuing without preloaded VAD.")

async def entrypoint(ctx: JobContext):
    ctx.log_context_fields = {"room": ctx.room.name}
    logger.info("\n" + "🎲" * 8)
    logger.info("🚀 STARTING VOICE GAME MASTER (Brinmere Mini-Arc)")

    userdata = Userdata()

    session = AgentSession(
        stt=deepgram.STT(model="nova-3"),
        llm=google.LLM(model="gemini-2.5-flash"),
        tts=murf.TTS(
            voice="en-US-natalie",  # Valid Murf voice: ethereal, narrative-friendly for Lujanne (supports Narration/Meditative)
            style="Narration",  # Updated for immersive storytelling
            text_pacing=True,
        ),
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata.get("vad"),
        userdata=userdata,
    )

    # Start the agent session with the GameMasterAgent
    await session.start(
        agent=GameMasterAgent(),
        room=ctx.room,
        room_input_options=RoomInputOptions(noise_cancellation=noise_cancellation.BVC()),
    )

    await ctx.connect()

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm))