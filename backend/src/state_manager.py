# state_manager.py
"""
State Manager for the Voice Game Master.
Handles world state, player state, dynamic scenes, and story continuity.
This upgraded version supports:
- Cinematic scene generation
- Memory tracking
- Player class behaviors (Mage, Warrior, Rogue future support)
- Location-based narration seeds
"""

import json
import random


class StateManager:
    def __init__(self):
        # Core world + player state
        self.state = {
            "player": {
                "name": "Unnamed Mage",
                "class": "Mage",
                "inventory": [],
                "health": 100,
                "mana": 100,
            },
            "world": {
                "location": "dragon_scarred_pass",
                "visited_locations": [],
                "npcs": {},
                "threat_level": 1,
            },
            "story": {
                "chapter": 1,
                "choices": [],
                "tone": "dramatic",
                "universe": "high_fantasy",
                "voice": "epic_narrator",
            },
        }

    # ------------------------------------------------------------
    # Basic state methods
    # ------------------------------------------------------------
    def get_state(self):
        return self.state

    def set_location(self, loc: str):
        self.state["world"]["location"] = loc

    def add_choice(self, choice: str):
        self.state["story"]["choices"].append(choice)

    def add_item(self, item: str):
        self.state["player"]["inventory"].append(item)

    # ------------------------------------------------------------
    # Dynamic Opening Scene
    # ------------------------------------------------------------
    def generate_opening_scene(self):
        """
        Generates a dramatic cinematic opening scene based on:
        - Player class (Mage)
        - Starting location (dragon-scarred mountain pass)
        - Tone: dramatic & epic narrator
        """

        location = self.state["world"]["location"]

        if location == "dragon_scarred_pass":
            opening = (
                "High on the spine of Xadia’s ancient mountains, you tread the Dragon-Scarred Pass — "
                "a jagged corridor carved by battles older than memory. The wind carries the scent of "
                "embered stone and distant magic. Your mage robes flicker with arcane energy as runes "
                "pulse faintly beneath your fingertips.\n\n"
                "A colossal dragon’s skeleton lies half-buried in the cliffside ahead, its ribs forming "
                "a cathedral of bone. Strange blue fire dances in the hollow of its skull — something… awakened.\n\n"
                "The sky rumbles. A shadow passes overhead.\n\n"
                "You feel the presence before you hear it — ancient, watching, curious.\n\n"
                "What do you do?"
            )

        else:
            opening = "You stand at the beginning of your journey. What do you do?"

        return opening

    # ------------------------------------------------------------
    # Context Builder for Every Turn
    # ------------------------------------------------------------
    def generate_context(self):
        """
        Provides the model with continuity information.
        This context is prepended to each model prompt.
        """

        player = self.state["player"]
        story = self.state["story"]
        world = self.state["world"]

        context = {
            "player_class": player["class"],
            "inventory": player["inventory"],
            "health": player["health"],
            "mana": player["mana"],
            "location": world["location"],
            "visited_locations": world["visited_locations"],
            "npcs": world["npcs"],
            "choices": story["choices"],
            "chapter": story["chapter"],
            "tone": story["tone"],
            "universe": story["universe"],
        }

        return context

    # ------------------------------------------------------------
    # JSON serialization
    # ------------------------------------------------------------
    def to_json(self):
        return json.dumps(self.state, indent=2)
