# tools.py
# Defines all callable tools used by the agent (DB actions, utility functions, etc.)

import json
import os
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)


# -----------------------------
# Utility: Load JSON file safely
# -----------------------------
def _load_json(filename):
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}


# -----------------------------
# Utility: Save JSON file safely
# -----------------------------
def _save_json(filename, data):
    path = os.path.join(DATA_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ============================================================
# Tool 1 — Store User Interaction Logs
# ============================================================
def log_interaction(user_id: str, message: str):
    """Append timestamped interaction logs."""
    logs = _load_json("interaction_logs.json")

    if user_id not in logs:
        logs[user_id] = []

    logs[user_id].append({
        "timestamp": datetime.utcnow().isoformat(),
        "message": message
    })

    _save_json("interaction_logs.json", logs)

    return {"status": "ok", "message": "log saved"}


# ============================================================
# Tool 2 — Get Category Info (for DB usage)
# ============================================================
def get_category_info(category: str):
    """Returns description + items inside a category."""
    db = _load_json("categories.json")

    if category not in db:
        return {"error": "category_not_found"}

    return {
        "category": category,
        "items": db[category]
    }


# ============================================================
# Tool 3 — Save a User Preference
# ============================================================
def save_preference(user_id: str, key: str, value):
    prefs = _load_json("preferences.json")
    prefs.setdefault(user_id, {})
    prefs[user_id][key] = value

    _save_json("preferences.json", prefs)

    return {"status": "saved", "key": key, "value": value}


# ============================================================
# Tool 4 — Retrieve User Preference
# ============================================================
def get_preference(user_id: str, key: str):
    prefs = _load_json("preferences.json")

    if user_id not in prefs or key not in prefs[user_id]:
        return {"error": "preference_not_found"}

    return {"key": key, "value": prefs[user_id][key]}
