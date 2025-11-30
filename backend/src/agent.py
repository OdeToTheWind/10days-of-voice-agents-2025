import logging
import json
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
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
from livekit.plugins import deepgram, google, noise_cancellation
from livekit.plugins.turn_detector.multilingual import MultilingualModel

logger = logging.getLogger("ecommerce")
load_dotenv(".env.local")

# --------------------------------------------------
# Load product catalog
# --------------------------------------------------

CATALOG_PATH = Path(__file__).parent / "catalog.json"
if not CATALOG_PATH.exists():
    raise FileNotFoundError("catalog.json is missing! Please create the file before running the agent.")

with CATALOG_PATH.open("r", encoding="utf-8") as fh:
    PRODUCT_CATALOG = json.load(fh)


def _find_catalog_item(query: str) -> dict | None:
    """
    Find a product by id or name (case-insensitive, partial matches allowed)
    """
    query_lower = query.lower()
    for item in PRODUCT_CATALOG:
        if query_lower == item["id"].lower() or query_lower == item["name"].lower():
            return item
    # If exact match not found, try partial match
    for item in PRODUCT_CATALOG:
        if query_lower in item["id"].lower() or query_lower in item["name"].lower():
            return item
    return None


BASE_INSTRUCTIONS = """
You are an E-COMMERCE SHOPPING ASSISTANT AGENT.

Your job:
- Greet users with a short warm welcome.
- Help them add, remove, update, and view items in their shopping cart.
- Provide product descriptions based on the item catalog.
- Keep responses short and helpful.
- When modifying the cart, always call the respective tool method.

Tools available:
- add_item_to_cart(item_query: str, quantity: int = 1)
- remove_item_from_cart(item_query: str)
- update_cart_quantity(item_query: str, quantity: int)
- list_cart()
- describe_product(item_query: str)

If the user asks anything outside shopping, briefly redirect them back to shopping.
"""


class EcommerceAgent(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=BASE_INSTRUCTIONS)
        self.cart: list[dict] = []

    def _find_cart_entry(self, item_id: str) -> dict | None:
        for e in self.cart:
            if e["item_id"] == item_id:
                return e
        return None

    def _cart_summary(self) -> dict:
        total_items = sum(e["quantity"] for e in self.cart)
        items = [
            {"item_id": e["item_id"], "name": e["name"], "quantity": e["quantity"]}
            for e in self.cart
        ]
        return {"items": items, "total_items": total_items}

    # -----------------------------
    # Cart Management Tools
    # -----------------------------

    @function_tool
    async def add_item_to_cart(
        self, context: RunContext, item_query: str, quantity: int = 1
    ) -> dict:
        if quantity <= 0:
            quantity = 1

        item = _find_catalog_item(item_query)
        if not item:
            return {"ok": False, "message": f"Unknown product '{item_query}'."}

        entry = self._find_cart_entry(item["id"])
        if entry:
            entry["quantity"] += quantity
        else:
            self.cart.append({
                "item_id": item["id"],
                "name": item["name"],
                "quantity": quantity,
            })

        return {"ok": True, "cart": self._cart_summary()}

    @function_tool
    async def remove_item_from_cart(self, context: RunContext, item_query: str) -> dict:
        item = _find_catalog_item(item_query)
        if not item:
            return {"ok": False, "message": f"Unknown product '{item_query}'."}

        before = len(self.cart)
        self.cart = [c for c in self.cart if c["item_id"] != item["id"]]
        removed = len(self.cart) < before
        return {"ok": removed, "cart": self._cart_summary()}

    @function_tool
    async def update_cart_quantity(
        self, context: RunContext, item_query: str, quantity: int
    ) -> dict:
        item = _find_catalog_item(item_query)
        if not item:
            return {"ok": False, "message": f"Unknown product '{item_query}'."}

        if quantity <= 0:
            self.cart = [c for c in self.cart if c["item_id"] != item["id"]]
            return {"ok": True, "cart": self._cart_summary()}

        entry = self._find_cart_entry(item["id"])
        if entry:
            entry["quantity"] = quantity
        else:
            self.cart.append({
                "item_id": item["id"],
                "name": item["name"],
                "quantity": quantity,
            })

        return {"ok": True, "cart": self._cart_summary()}

    @function_tool
    async def list_cart(self, context: RunContext) -> dict:
        return self._cart_summary()

    @function_tool
    async def describe_product(self, context: RunContext, item_query: str) -> dict:
        item = _find_catalog_item(item_query)
        if not item:
            return {"ok": False, "message": f"Unknown product '{item_query}'."}
        return {"ok": True, "description": item.get("description", "No description available.")}


# --------------------------------------------------
# LiveKit Flow
# --------------------------------------------------

def prewarm(proc: JobProcess):
    pass  # E-commerce agent does not need VAD


async def entrypoint(ctx: JobContext):
    ctx.log_context_fields = {"room": ctx.room.name}
    await ctx.connect()

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

    await session.start(agent=EcommerceAgent(), room=ctx.room)

    await session.generate_reply(
        instructions=(
            "Give a short welcome message. Example:\n"
            "'Welcome to NovaCart. Say browse to explore products or say cart to manage your items.'"
        )
    )


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm))