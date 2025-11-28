# backend/src/agent.py
import logging
import os
import json
from datetime import datetime
from difflib import get_close_matches
from dotenv import load_dotenv

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
)
from livekit.plugins import murf, silero, google, deepgram, noise_cancellation
from livekit.plugins.turn_detector.multilingual import MultilingualModel

logger = logging.getLogger("grocery_agent")
logging.basicConfig(level=logging.INFO)
load_dotenv(".env.local")

# ---------- Config ----------
CATALOG_PATH = "src/shared-data/catalog.json"
ORDERS_DIR = "orders"
FUZZY_CUTOFF = 0.55  # lower = more permissive, higher = stricter
# map category names (as in catalog) to TTS voices
CATEGORY_VOICE_MAP = {
    "Fresh Vegetables": "en-US-matthew",
    "Fruits": "en-US-alicia",
    "Bakery Items": "en-US-ken",
    "Milk and Dairy Products": "en-US-matthew",
    "Snacks": "en-US-alicia",
    # fallback voice:
    "default": "en-US-matthew",
}
# Simple recipes mapping (dish -> list of item names that appear in catalog)
RECIPES = {
    "peanut butter sandwich": ["Whole Wheat Bread", "Peanut Butter (500g)"],
    "pasta for two": ["Pasta Penne 500g", "Tomato Pasta Sauce 400g", "Cheddar Cheese 200g"],
    "cheese toast": ["White Bread", "Cheddar Cheese 200g", "Butter 250g"],
}
# ---------------------------


def ensure_catalog_exists():
    if not os.path.exists(CATALOG_PATH):
        raise FileNotFoundError(f"catalog.json not found at: {CATALOG_PATH}")


def load_catalog() -> dict:
    ensure_catalog_exists()
    with open(CATALOG_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_order(order_obj: dict) -> str:
    os.makedirs(ORDERS_DIR, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    filename = f"order_{timestamp}.json"
    path = os.path.join(ORDERS_DIR, filename)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(order_obj, fh, indent=2, ensure_ascii=False)
    logger.info("Order saved to %s", path)
    return path


def _all_category_names(catalog: dict):
    return list(catalog.keys())


def _all_item_names(catalog: dict):
    names = []
    for items in catalog.values():
        for it in items:
            names.append(it.get("name"))
    return names


def fuzzy_match_category(query: str, catalog: dict) -> str | None:
    if not catalog:
        return None
    candidates = _all_category_names(catalog)
    matches = get_close_matches(query, candidates, n=1, cutoff=FUZZY_CUTOFF)
    if matches:
        return matches[0]
    # fallback: substring match
    q = query.strip().lower()
    for c in candidates:
        if q in c.lower():
            return c
    return None


def fuzzy_find_item(query: str, catalog: dict) -> dict | None:
    if not catalog:
        return None
    candidates = _all_item_names(catalog)
    matches = get_close_matches(query, candidates, n=1, cutoff=FUZZY_CUTOFF)
    if matches:
        best = matches[0]
        for items in catalog.values():
            for it in items:
                if it.get("name") == best:
                    return it
    # substring fallback
    q = query.strip().lower()
    for items in catalog.values():
        for it in items:
            if q in it.get("name", "").lower():
                return it
    return None


class OrderManager:
    """Per-session order/cart manager"""

    def __init__(self, catalog: dict):
        self.catalog = catalog
        self.cart: list[dict] = []
        self.last_category: str | None = None  # used to pick TTS voice

    def _find_in_cart(self, item_id: str):
        for i, entry in enumerate(self.cart):
            if entry.get("id") == item_id:
                return i, entry
        return None, None

    def add_item(self, item_obj: dict, qty: int = 1):
        if not item_obj or qty <= 0:
            return {"error": "invalid item or quantity"}
        item_id = item_obj.get("id")
        idx, existing = self._find_in_cart(item_id)
        unit_price = float(item_obj.get("price", 0))
        if existing:
            existing["ordered_quantity"] += qty
            existing["total_price"] = round(existing["unit_price"] * existing["ordered_quantity"], 2)
            self.cart[idx] = existing
            return {"status": "updated", "entry": existing}
        else:
            entry = {
                "id": item_id,
                "name": item_obj.get("name"),
                "base_quantity": item_obj.get("quantity"),
                "unit_price": unit_price,
                "ordered_quantity": qty,
                "total_price": round(unit_price * qty, 2),
                "category": self._category_of_item(item_id),
            }
            self.cart.append(entry)
            return {"status": "added", "entry": entry}

    def remove_item_by_name(self, item_name: str) -> dict:
        # fuzzy match within cart names
        cart_names = [c["name"] for c in self.cart]
        matches = get_close_matches(item_name, cart_names, n=1, cutoff=FUZZY_CUTOFF)
        if matches:
            name = matches[0]
            self.cart = [c for c in self.cart if c["name"] != name]
            return {"status": "removed", "name": name}
        # substring fallback
        q = item_name.lower()
        for i, c in enumerate(self.cart):
            if q in c["name"].lower():
                removed = self.cart.pop(i)
                return {"status": "removed", "name": removed["name"]}
        return {"error": f"'{item_name}' not found in cart."}

    def update_quantity(self, item_name: str, qty: int):
        if qty < 0:
            return {"error": "quantity must be >= 0"}
        cart_names = [c["name"] for c in self.cart]
        matches = get_close_matches(item_name, cart_names, n=1, cutoff=FUZZY_CUTOFF)
        target = None
        if matches:
            name = matches[0]
            target = next((c for c in self.cart if c["name"] == name), None)
        else:
            q = item_name.lower()
            target = next((c for c in self.cart if q in c["name"].lower()), None)
        if not target:
            return {"error": f"Item '{item_name}' not found in cart."}
        if qty == 0:
            self.cart = [c for c in self.cart if c["id"] != target["id"]]
            return {"status": "removed", "name": target["name"]}
        target["ordered_quantity"] = qty
        target["total_price"] = round(target["unit_price"] * qty, 2)
        return {"status": "updated", "entry": target}

    def list_cart(self):
        total = round(sum(float(c.get("total_price", 0)) for c in self.cart), 2)
        return {"cart": self.cart, "items_total": total, "count": len(self.cart)}

    def place_order(self, customer_name: str = "", address: str = "") -> dict:
        if not self.cart:
            return {"error": "cart_empty"}
        items_total = round(sum(float(c.get("total_price", 0)) for c in self.cart), 2)
        delivery = 20.0
        grand = round(items_total + delivery, 2)
        order = {
            "order_id": f"order_{datetime.utcnow().strftime('%Y%m%dT%H%M%S')}",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "customer": {"name": customer_name or "", "address": address or ""},
            "items": self.cart,
            "bill_summary": {
                "items_total": items_total,
                "delivery_charge": delivery,
                "grand_total": grand,
            },
            "status": "placed",
        }
        path = save_order(order)
        # clear cart
        self.cart = []
        return {"status": "placed", "order": order, "path": path}

    def add_recipe(self, recipe_key: str, servings: int = 1):
        recipe_key_lower = recipe_key.strip().lower()
        recipe_map_keys = list(RECIPES.keys())
        matches = get_close_matches(recipe_key_lower, recipe_map_keys, n=1, cutoff=FUZZY_CUTOFF)
        chosen = matches[0] if matches else None
        if not chosen:
            # substring fallback
            for k in recipe_map_keys:
                if recipe_key_lower in k.lower():
                    chosen = k
                    break
        if not chosen:
            return {"error": f"recipe '{recipe_key}' not found"}
        added = []
        for ingr in RECIPES[chosen]:
            found = fuzzy_find_item(ingr, self.catalog)
            if not found:
                added.append({"name": ingr, "status": "missing"})
                continue
            self.add_item(found, quantity=max(1, int(servings)))
            added.append({"name": found["name"], "status": "added"})
        return {"status": "ok", "recipe": chosen, "added": added}

    def _category_of_item(self, item_id: str) -> str | None:
        for cat, items in self.catalog.items():
            for it in items:
                if it.get("id") == item_id:
                    return cat
        return None


#
# --------------- Voice responder helper (tries structured payload then fallbacks) ---------------
#
def respond_fn_factory(sess, ctx_obj):
    """
    Returns an async function respond_fn(text, voice=None) that will:
    1) Try sending structured payload with tts override via sess.send_text or sess.publish_text
    2) Fallback to plain text via send_text / publish_text / room.send_data
    """
    async def respond_fn(reply_text: str, voice: str | None = None):
        sent = False
        # structured payload attempt
        if voice:
            payload = {"text": reply_text, "tts": {"voice": voice}}
            try:
                if hasattr(sess, "send_text") and callable(sess.send_text):
                    await sess.send_text(payload)
                    logger.debug("Sent structured send_text with voice %s", voice)
                    return
            except Exception as e:
                logger.debug("structured send_text failed: %s", e)
            try:
                if hasattr(sess, "publish_text") and callable(sess.publish_text):
                    await sess.publish_text(payload)
                    logger.debug("Sent structured publish_text with voice %s", voice)
                    return
            except Exception as e:
                logger.debug("structured publish_text failed: %s", e)

        # plain text fallback
        try:
            if hasattr(sess, "send_text") and callable(sess.send_text):
                await sess.send_text(reply_text)
                sent = True
        except Exception:
            logger.debug("send_text failed or not available")
        if not sent:
            try:
                if hasattr(sess, "publish_text") and callable(sess.publish_text):
                    await sess.publish_text(reply_text)
                    sent = True
            except Exception:
                logger.debug("publish_text failed or not available")
        if not sent:
            try:
                await ctx_obj.room.send_data(reply_text)
                sent = True
            except Exception:
                logger.exception("room.send_data failed")
        if not sent:
            logger.warning("Couldn't send reply via any channel")

    return respond_fn


#
# ---------------------- Agent class and entrypoint ----------------------
#
class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are Blink Basket, a friendly voice-first Food & Grocery ordering assistant. "
                "Help the user add items to cart, list cart, remove items, add ingredients for simple recipes, "
                "and place orders which will be saved. Ask clarifying questions when quantity/size/brand is ambiguous. "
                "Use short conversational replies suitable for speech."
            )
        )


def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()


async def entrypoint(ctx: JobContext):
    ctx.log_context_fields = {"room": ctx.room.name}

    # Build agent session (STT, LLM, TTS)
    session = AgentSession(
        stt=deepgram.STT(model="nova-3"),
        llm=google.LLM(model="gemini-2.5-flash"),
        tts=murf.TTS(
            voice="en-US-matthew",
            style="Conversation",
            tokenizer=tokenize.basic.SentenceTokenizer(min_sentence_len=2),
            text_pacing=True,
        ),
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
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

    # Start session with our Assistant
    await session.start(
        agent=Assistant(),
        room=ctx.room,
        room_input_options=RoomInputOptions(noise_cancellation=noise_cancellation.BVC()),
    )

    # Per-session managers
    order_managers: dict[str, OrderManager] = {}

    # helper to get per-session manager
    def get_order_mgr(session_id: str) -> OrderManager:
        if session_id not in order_managers:
            catalog = {}
            try:
                catalog = load_catalog()
            except Exception as e:
                logger.exception("Failed to load catalog: %s", e)
            order_managers[session_id] = OrderManager(catalog)
        return order_managers[session_id]

    # Unified incoming handler
    async def _handle_incoming_event(ev):
        try:
            # extract text/transcript
            text = None
            session_id = None
            if hasattr(ev, "text"):
                text = ev.text
            elif hasattr(ev, "transcript"):
                text = ev.transcript
            elif hasattr(ev, "alternatives") and ev.alternatives:
                alt0 = ev.alternatives[0]
                text = getattr(alt0, "transcript", None) or getattr(alt0, "text", None)
            elif isinstance(ev, dict):
                for key in ("text", "transcript", "message", "body"):
                    if key in ev:
                        candidate = ev[key]
                        if isinstance(candidate, dict):
                            text = candidate.get("text") or candidate.get("transcript")
                        else:
                            text = candidate
                        if text:
                            break
            # session id extraction
            try:
                if hasattr(ev, "participant") and ev.participant is not None:
                    session_id = getattr(ev.participant, "identity", None) or getattr(ev.participant, "sid", None)
            except Exception:
                session_id = None
            if not session_id:
                session_id = ctx.room.name or "default"
            if not text:
                logger.debug("No text in event; ignoring")
                return
            text_str = text.strip()
            logger.info("Incoming (session=%s): %s", session_id, text_str)

            # routing: all to grocery flow (single-purpose agent)
            respond_fn = respond_fn_factory(session, ctx)
            mgr = get_order_mgr(session_id)

            lower = text_str.lower()

            # Common commands detection
            if any(kw in lower for kw in ("what's in my cart", "what is in my cart", "show my cart", "list cart", "cart")):
                res = mgr.list_cart()
                if res.get("count", 0) == 0:
                    await respond_fn("Your cart is empty. You can say 'add tomatoes' or 'show me vegetables'.", CATEGORY_VOICE_MAP.get("default"))
                else:
                    lines = []
                    for it in res["cart"]:
                        lines.append(f"{it['ordered_quantity']} x {it['name']}")
                    await respond_fn(f"You have {', '.join(lines)}. Total {res['items_total']:.2f} rupees.", CATEGORY_VOICE_MAP.get(mgr.last_category, CATEGORY_VOICE_MAP["default"]))
                return

            if any(kw in lower for kw in ("place my order", "checkout", "i'm done", "that's all", "place order")):
                # optional: try to extract name/address roughly
                name = ""
                address = ""
                order_res = mgr.place_order(customer_name=name, address=address)
                if order_res.get("status") == "placed":
                    await respond_fn(f"Order placed. Your total is {order_res['order']['bill_summary']['grand_total']:.2f}. Saved to {order_res['path']}.", CATEGORY_VOICE_MAP.get(mgr.last_category, CATEGORY_VOICE_MAP["default"]))
                else:
                    await respond_fn("I couldn't place the order: your cart is empty.", CATEGORY_VOICE_MAP.get("default"))
                return

            # recipe style: "ingredients for X" or "get me ingredients for pasta"
            if ("ingredient" in lower and "for" in lower) or lower.startswith("ingredients for") or lower.startswith("get me ingredients for"):
                # extract dish
                parts = lower.split("for", 1)
                dish = parts[1].strip() if len(parts) > 1 else lower.replace("ingredients for", "").strip()
                add_result = mgr.add_recipe(dish, servings=1)
                if add_result.get("status") == "ok":
                    added_names = [a["name"] for a in add_result["added"] if a.get("status") == "added"]
                    if added_names:
                        # set last category to first added item's category (for voice)
                        first_added = add_result["added"][0]
                        # find actual item in catalog to get its category
                        if isinstance(first_added, dict) and first_added.get("name"):
                            found_item = fuzzy_find_item(first_added["name"], mgr.catalog)
                            if found_item:
                                mgr.last_category = mgr._category_of_item(found_item["id"])
                        await respond_fn(f"I've added {', '.join(added_names)} to your cart for {dish}.", CATEGORY_VOICE_MAP.get(mgr.last_category, CATEGORY_VOICE_MAP["default"]))
                    else:
                        await respond_fn(f"I added the recipe ingredients, but some items weren't found in the catalog.", CATEGORY_VOICE_MAP.get("default"))
                else:
                    await respond_fn(f"Sorry, I couldn't find a recipe for {dish}. You can ask me to add specific items.", CATEGORY_VOICE_MAP.get("default"))
                return

            # Add to cart detection: "add 2 tomatoes" / "add tomatoes" / "i want 1 kg potatoes"
            if any(kw in lower for kw in ("add ", "i want ", "put ", "add to cart", "please add")) or lower.split()[0].isdigit():
                # try to parse quantity (simplistic)
                qty = 1
                words = lower.replace(",", " ").split()
                # find first integer-like word
                for w in words:
                    try:
                        if w.isdigit():
                            qty = int(w)
                            break
                    except Exception:
                        pass
                # remove words like 'add', 'please', 'to', 'cart'
                cleanup = lower
                for prefix in ("add ", "please add ", "put ", "i want ", "add to cart ", "add the "):
                    if cleanup.startswith(prefix):
                        cleanup = cleanup[len(prefix):]
                # strip qty word if at start
                if cleanup.split()[0].isdigit():
                    cleanup = " ".join(cleanup.split()[1:])
                # attempt fuzzy find item
                found_item = fuzzy_find_item(cleanup.strip(), mgr.catalog)
                if not found_item:
                    # maybe user mentioned category and item together, try last word
                    words2 = cleanup.split()
                    if len(words2) > 1:
                        candidate = words2[-1]
                        found_item = fuzzy_find_item(candidate, mgr.catalog)
                if not found_item:
                    await respond_fn(f"Sorry, I couldn't find '{cleanup.strip()}' in the catalog. Try saying 'show fruits' or 'add apples'.", CATEGORY_VOICE_MAP.get("default"))
                    return
                # add to cart
                result = mgr.add_item(found_item, qty)
                # set last category for voice switching
                mgr.last_category = mgr._category_of_item(found_item.get("id"))
                if result.get("status") in ("added", "updated"):
                    await respond_fn(result.get("status") == "added" and f"Added {qty} x {found_item['name']} to your cart." or f"Updated {found_item['name']} to {result['entry']['ordered_quantity']}.", CATEGORY_VOICE_MAP.get(mgr.last_category, CATEGORY_VOICE_MAP["default"]))
                else:
                    await respond_fn("Could not add the item to cart.", CATEGORY_VOICE_MAP.get("default"))
                return

            # remove/update quantity: "remove tomatoes", "remove 2 tomatoes", "set tomatoes to 2"
            if any(kw in lower for kw in ("remove ", "delete ", "set ", "update ")):
                if "remove " in lower or "delete " in lower:
                    # extract target
                    target = lower.split("remove", 1)[1] if "remove" in lower else lower.split("delete", 1)[1]
                    target = target.strip()
                    if not target:
                        await respond_fn("Which item should I remove?", CATEGORY_VOICE_MAP.get("default"))
                        return
                    r = mgr.remove_item_by_name(target)
                    if r.get("status") == "removed":
                        await respond_fn(f"Removed {r['name']} from your cart.", CATEGORY_VOICE_MAP.get(mgr.last_category, CATEGORY_VOICE_MAP["default"]))
                    else:
                        await respond_fn(r.get("error") or "I couldn't remove that item.", CATEGORY_VOICE_MAP.get("default"))
                    return
                if lower.startswith("set ") or " to " in lower:
                    # e.g., "set tomatoes to 2"
                    if " to " in lower:
                        parts = lower.split(" to ", 1)
                        left = parts[0].replace("set ", "").strip()
                        try:
                            new_q = int(parts[1].split()[0])
                        except Exception:
                            new_q = None
                        if new_q is None:
                            await respond_fn("I didn't understand the quantity. Say 'set apples to 2'.", CATEGORY_VOICE_MAP.get("default"))
                            return
                        r = mgr.update_quantity(left, new_q)
                        if r.get("status") == "updated":
                            await respond_fn(f"Updated {r['entry']['name']} to {r['entry']['ordered_quantity']}.", CATEGORY_VOICE_MAP.get(mgr.last_category, CATEGORY_VOICE_MAP["default"]))
                        elif r.get("status") == "removed":
                            await respond_fn(f"Removed {r['name']} from your cart.", CATEGORY_VOICE_MAP.get(mgr.last_category, CATEGORY_VOICE_MAP["default"]))
                        else:
                            await respond_fn(r.get("error") or "Couldn't update quantity.", CATEGORY_VOICE_MAP.get("default"))
                        return

            # show catalog by category: "show vegetables", "show fruits"
            if any(kw in lower for kw in ("show ", "list ", "what do you have", "show me ")):
                # attempt to extract category after 'show' or 'list'
                if "show me " in lower:
                    cat_q = lower.split("show me ", 1)[1].strip()
                elif "show " in lower:
                    cat_q = lower.split("show ", 1)[1].strip()
                elif "list " in lower:
                    cat_q = lower.split("list ", 1)[1].strip()
                else:
                    cat_q = ""
                if not cat_q:
                    await respond_fn("You can say 'show fruits' or 'show bakery items'. Which category would you like?", CATEGORY_VOICE_MAP.get("default"))
                    return
                matched = fuzzy_match_category(cat_q, mgr.catalog)
                if not matched:
                    await respond_fn(f"Couldn't find a category matching '{cat_q}'. Try 'fruits' or 'fresh vegetables'.", CATEGORY_VOICE_MAP.get("default"))
                    return
                items = mgr.catalog.get(matched, [])
                if not items:
                    await respond_fn(f"There are no items in {matched}.", CATEGORY_VOICE_MAP.get("default"))
                    return
                # reply with short summary
                short_list = ", ".join(it["name"] for it in items[:6])
                # set last_category for voice switching
                mgr.last_category = matched
                await respond_fn(f"Here are items in {matched}: {short_list}.", CATEGORY_VOICE_MAP.get(mgr.last_category, CATEGORY_VOICE_MAP["default"]))
                return

            # fallback
            await respond_fn("Sorry, I didn't understand. You can say 'show fruits', 'add apples', 'what's in my cart', or 'place my order'.", CATEGORY_VOICE_MAP.get("default"))
        except Exception as e:
            logger.exception("Exception in incoming handler: %s", e)
            try:
                await respond_fn("Something went wrong processing your request. Try again.", CATEGORY_VOICE_MAP.get("default"))
            except Exception:
                logger.exception("Failed to send fallback error message")

    # Register session event listeners
    try:
        @session.on("transcript")
        async def _on_transcript(ev):
            await _handle_incoming_event(ev)
    except Exception:
        logger.debug("Failed to attach handler for 'transcript'")

    try:
        @session.on("transcription")
        async def _on_transcription(ev):
            await _handle_incoming_event(ev)
    except Exception:
        logger.debug("Failed to attach handler for 'transcription'")

    try:
        @session.on("message")
        async def _on_message(ev):
            await _handle_incoming_event(ev)
    except Exception:
        logger.debug("Failed to attach handler for 'message'")

    # Optionally notify room about previous saved order count (non-intrusive)
    try:
        if os.path.exists(ORDERS_DIR):
            recent = sorted([f for f in os.listdir(ORDERS_DIR) if f.endswith(".json")], reverse=True)[:1]
            if recent:
                msg = f"Welcome back — I can help you order groceries. Last saved order: {recent[0]}."
                try:
                    if hasattr(session, "send_text"):
                        await session.send_text(msg)
                    else:
                        await ctx.room.send_data(msg)
                except Exception:
                    logger.debug("Could not send welcome message to room")
    except Exception:
        pass

    await ctx.connect()


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm))
