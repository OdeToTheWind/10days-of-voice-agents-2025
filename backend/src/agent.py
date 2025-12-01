# src/agent.py
import logging
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any

from dotenv import load_dotenv
load_dotenv(".env.local")

from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    JobProcess,
    MetricsCollectedEvent,
    WorkerOptions,
    RoomInputOptions,
    metrics,
    function_tool,
    RunContext,
    cli,
)

# plugins
from livekit.plugins import deepgram, google, noise_cancellation
from livekit.plugins.turn_detector.multilingual import MultilingualModel

logger = logging.getLogger("ecommerce")
logger.setLevel(logging.INFO)

# -------------------------
# Load product catalog
# -------------------------
CATALOG_PATH = Path(__file__).parent / "catalog.json"
if not CATALOG_PATH.exists():
    raise FileNotFoundError("catalog.json is missing! Please create the file before running the agent.")

with CATALOG_PATH.open("r", encoding="utf-8") as fh:
    PRODUCT_CATALOG: List[Dict[str, Any]] = json.load(fh)


def _find_catalog_item(query: str) -> Optional[Dict[str, Any]]:
    """
    Find a product by id or name (case-insensitive, partial matches allowed)
    """
    if not query:
        return None
    q = query.lower().strip()
    # Exact id/name
    for item in PRODUCT_CATALOG:
        if q == item.get("id", "").lower() or q == item.get("name", "").lower():
            return item
    # Partial match
    for item in PRODUCT_CATALOG:
        if q in item.get("id", "").lower() or q in item.get("name", "").lower():
            return item
    # tags fallback
    for item in PRODUCT_CATALOG:
        tags = " ".join(item.get("tags", [])).lower()
        if q in tags or any(tok in tags for tok in q.split()):
            return item
    return None


# -------------------------
# Agent instructions
# -------------------------
BASE_INSTRUCTIONS = """
You are an E-COMMERCE SHOPPING ASSISTANT AGENT.

Your job:
- Greet users with a short warm welcome.
- Help them add, remove, update, and view items in their shopping cart.
- Provide product descriptions based on the item catalog.
- Keep responses short and helpful.
- When modifying the cart, always call the respective tool method.
- When asked about totals, compute from prices stored in the catalog.
"""

# -------------------------
# Agent implementation
# -------------------------
class EcommerceAgent(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=BASE_INSTRUCTIONS)
        # keep cart as instance variable per-process; session cart will be kept in RunContext.userdata (tools use that)
        self.cart: List[Dict[str, Any]] = []

    # helper to load session cart (preferred), fallback to agent-level cart
    def _get_session_cart(self, ctx: RunContext) -> List[Dict[str, Any]]:
        cart = ctx.proc.userdata.get("cart")
        if cart is None:
            # initialize from agent-level cart if available
            ctx.proc.userdata["cart"] = list(self.cart)  # shallow copy
            return ctx.proc.userdata["cart"]
        return cart

    def _save_session_cart(self, ctx: RunContext, cart: List[Dict[str, Any]]):
        ctx.proc.userdata["cart"] = cart
        # keep agent-level in sync
        self.cart = list(cart)

    def _cart_summary_struct(self, cart: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Return structured cart summary that includes per-item totals and grand total.
        Each cart item will have:
          - item_id, name, quantity, unit_price, item_total
        """
        items_out = []
        total = 0.0
        currency = None
        for entry in cart:
            unit = float(entry.get("unit_price", 0.0))
            qty = int(entry.get("quantity", 1))
            item_total = round(unit * qty, 2)
            total += item_total
            currency = entry.get("currency", currency) or currency
            items_out.append({
                "item_id": entry.get("item_id"),
                "name": entry.get("name"),
                "quantity": qty,
                "unit_price": round(unit, 2),
                "item_total": item_total
            })
        return {"items": items_out, "total": round(total, 2), "currency": currency or "USD", "total_items": sum(i["quantity"] for i in cart)}

    # -----------------------------
    # Cart Management Tools
    # -----------------------------
    @function_tool
    async def add_item_to_cart(self, context: RunContext, item_query: str, quantity: int = 1) -> Dict[str, Any]:
        if quantity <= 0:
            quantity = 1

        item = _find_catalog_item(item_query)
        if not item:
            return {"ok": False, "message": f"Unknown product '{item_query}'."}

        cart = self._get_session_cart(context)
        # find existing entry
        entry = next((c for c in cart if c["item_id"] == item["id"]), None)
        if entry:
            entry["quantity"] += quantity
        else:
            cart.append({
                "item_id": item["id"],
                "name": item["name"],
                "quantity": quantity,
                # include pricing info from catalog so we can compute totals later
                "unit_price": float(item.get("price", 0.0)),
                "currency": item.get("currency", "USD"),
            })
        self._save_session_cart(context, cart)
        return {"ok": True, "cart": self._cart_summary_struct(cart)}

    @function_tool
    async def remove_item_from_cart(self, context: RunContext, item_query: str) -> Dict[str, Any]:
        item = _find_catalog_item(item_query)
        if not item:
            return {"ok": False, "message": f"Unknown product '{item_query}'."}
        cart = self._get_session_cart(context)
        before = len(cart)
        cart = [c for c in cart if c["item_id"] != item["id"]]
        self._save_session_cart(context, cart)
        removed = len(cart) < before
        return {"ok": True, "removed": removed, "cart": self._cart_summary_struct(cart)}

    @function_tool
    async def update_cart_quantity(self, context: RunContext, item_query: str, quantity: int) -> Dict[str, Any]:
        item = _find_catalog_item(item_query)
        if not item:
            return {"ok": False, "message": f"Unknown product '{item_query}'."}
        cart = self._get_session_cart(context)
        if quantity <= 0:
            cart = [c for c in cart if c["item_id"] != item["id"]]
            self._save_session_cart(context, cart)
            return {"ok": True, "cart": self._cart_summary_struct(cart)}
        entry = next((c for c in cart if c["item_id"] == item["id"]), None)
        if entry:
            entry["quantity"] = int(quantity)
        else:
            # add new with price included
            cart.append({
                "item_id": item["id"],
                "name": item["name"],
                "quantity": int(quantity),
                "unit_price": float(item.get("price", 0.0)),
                "currency": item.get("currency", "USD"),
            })
        self._save_session_cart(context, cart)
        return {"ok": True, "cart": self._cart_summary_struct(cart)}

    @function_tool
    async def list_cart(self, context: RunContext) -> Dict[str, Any]:
        cart = self._get_session_cart(context)
        return {"ok": True, "cart": self._cart_summary_struct(cart)}

    @function_tool
    async def describe_product(self, context: RunContext, item_query: str) -> Dict[str, Any]:
        item = _find_catalog_item(item_query)
        if not item:
            return {"ok": False, "message": f"Unknown product '{item_query}'."}
        # include price in description
        return {
            "ok": True,
            "description": item.get("description", "No description available."),
            "price": item.get("price", None),
            "currency": item.get("currency", "USD"),
        }

    # optional convenience tool: compute total only
    @function_tool
    async def calculate_cart_total(self, context: RunContext) -> Dict[str, Any]:
        cart = self._get_session_cart(context)
        summary = self._cart_summary_struct(cart)
        return {"ok": True, "total": summary["total"], "currency": summary["currency"], "items": summary["items"]}


# --------------------------------------------------
# LiveKit Flow
# --------------------------------------------------
def prewarm(proc: JobProcess):
    pass  # no VAD required here


async def entrypoint(ctx: JobContext):
    ctx.log_context_fields = {"room": ctx.room.name}
    await ctx.connect()

    # choose your Gemini model (you used gemini-2.5-flash in the example)
    session = AgentSession(
        stt=deepgram.STT(model="nova-3"),
        llm=google.LLM(model="gemini-2.5-flash"),
        tts=google.beta.GeminiTTS(
            model="gemini-2.5-flash-preview-tts",
            voice_name="Zephyr",
            instructions=(
                "Speak like a friendly e-commerce assistant. "
                "Warm, concise, and helpful."
            ),
        ),
        turn_detection=MultilingualModel(),
        vad=None,
        preemptive_generation=False,
    )

    usage_collector = metrics.UsageCollector()

    @session.on("metrics_collected")
    def _on_metrics(ev: MetricsCollectedEvent):
        metrics.log_metrics(ev.metrics)
        usage_collector.collect(ev.metrics)

    async def log_usage():
        logger.info(f"Usage summary: {usage_collector.get_summary()}")

    ctx.add_shutdown_callback(log_usage)

    # start the session & agent
    await session.start(agent=EcommerceAgent(), room=ctx.room)

    # Send a welcome message: try generate_reply, else send_message, else send
    welcome_text = (
        "Welcome to NovaCart. Say 'browse' to explore products or 'cart' to manage your items."
    )
    # try multiple APIs to be compatible with SDK versions
    sent = False
    try:
        # newer API
        await session.generate_reply(instructions=welcome_text)
        sent = True
    except Exception:
        try:
            await session.send_message(text=welcome_text)
            sent = True
        except Exception:
            try:
                # very old API
                await session.send(text=welcome_text)
                sent = True
            except Exception:
                logger.warning("Could not send welcome via any API available.")

    # done
    logger.info("Agent started; welcome sent=%s", sent)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm))
