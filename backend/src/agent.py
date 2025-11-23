import logging
import json
from datetime import datetime
from pathlib import Path
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
    function_tool,
    RunContext
)
from livekit.plugins import murf, silero, google, deepgram, noise_cancellation
from livekit.plugins.turn_detector.multilingual import MultilingualModel

logger = logging.getLogger("agent")
load_dotenv(".env.local")

class CoffeeBaristaAgent(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions="""You are a friendly barista at "Brew Haven Coffee Shop". 
            Your job is to take coffee orders from customers in a warm, conversational manner.
            
            You need to collect the following information for each order:
            - Drink type (coffee, latte, cappuccino, espresso, americano, mocha, etc.)
            - Size (small, medium, or large)
            - Milk preference (whole milk, skim milk, almond milk, oat milk, soy milk, or no milk)
            - Extras (whipped cream, caramel drizzle, chocolate chips, extra shot, vanilla syrup, etc.)
            - Customer's name for the order
            
            Ask one question at a time in a natural, friendly way. Don't overwhelm the customer.
            Once you have all the information, confirm the complete order and use the save_order tool to save it.
            
            Your responses should be concise, friendly, and conversational without complex formatting.""",
        )
        
        # Initialize order state
        self.order_state = {
            "drinkType": None,
            "size": None,
            "milk": None,
            "extras": [],
            "name": None
        }
    
    @function_tool()
    async def save_order(self, context: RunContext) -> str:
        """Save the completed coffee order to a JSON file.
        Call this function only when all order details have been collected.
        """
        try:
            # Create orders directory if it doesn't exist
            orders_dir = Path("orders")
            orders_dir.mkdir(exist_ok=True)
            
            # Generate filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = orders_dir / f"order_{timestamp}.json"
            
            # Add timestamp to order
            order_summary = {
                **self.order_state,
                "timestamp": datetime.now().isoformat(),
                "status": "pending"
            }
            
            # Save to JSON file
            with open(filename, "w") as f:
                json.dump(order_summary, f, indent=2)
            
            logger.info(f"Order saved successfully: {filename}")
            
            # Reset order state for next customer
            self.order_state = {
                "drinkType": None,
                "size": None,
                "milk": None,
                "extras": [],
                "name": None
            }
            
            return f"Order saved successfully! Your order will be ready shortly, {order_summary['name']}. Thank you for visiting Brew Haven!"
            
        except Exception as e:
            logger.error(f"Error saving order: {e}")
            return f"I apologize, there was an error saving your order. Please try again."
    
    @function_tool()
    async def set_drink_type(self, context: RunContext, drink_type: str) -> str:
        """Set the drink type for the order.
        
        Args:
            drink_type: The type of drink (e.g., coffee, latte, cappuccino, espresso)
        """
        self.order_state["drinkType"] = drink_type
        logger.info(f"Drink type set to: {drink_type}")
        return f"Got it! One {drink_type}."
    
    @function_tool()
    async def set_size(self, context: RunContext, size: str) -> str:
        """Set the size for the order.
        
        Args:
            size: The size of the drink (small, medium, or large)
        """
        self.order_state["size"] = size
        logger.info(f"Size set to: {size}")
        return f"Perfect! {size} it is."
    
    @function_tool()
    async def set_milk(self, context: RunContext, milk_type: str) -> str:
        """Set the milk preference for the order.
        
        Args:
            milk_type: Type of milk (whole, skim, almond, oat, soy, or none)
        """
        self.order_state["milk"] = milk_type
        logger.info(f"Milk preference set to: {milk_type}")
        return f"Noted! {milk_type}."
    
    @function_tool()
    async def add_extra(self, context: RunContext, extra: str) -> str:
        """Add an extra item to the order.
        
        Args:
            extra: Extra item (whipped cream, caramel, chocolate chips, extra shot, vanilla, etc.)
        """
        if extra not in self.order_state["extras"]:
            self.order_state["extras"].append(extra)
        logger.info(f"Added extra: {extra}")
        return f"Added {extra}!"
    
    @function_tool()
    async def set_customer_name(self, context: RunContext, name: str) -> str:
        """Set the customer's name for the order.
        
        Args:
            name: Customer's name
        """
        self.order_state["name"] = name
        logger.info(f"Customer name set to: {name}")
        return f"Thank you, {name}!"
    
    @function_tool()
    async def check_order_status(self, context: RunContext) -> str:
        """Check which order details are still missing."""
        missing = []
        if not self.order_state["drinkType"]:
            missing.append("drink type")
        if not self.order_state["size"]:
            missing.append("size")
        if not self.order_state["milk"]:
            missing.append("milk preference")
        if not self.order_state["name"]:
            missing.append("name")
        
        if missing:
            return f"Still need: {', '.join(missing)}"
        else:
            return "Order is complete! All details collected."

def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()

async def entrypoint(ctx: JobContext):
    ctx.log_context_fields = {
        "room": ctx.room.name,
    }
    
    session = AgentSession(
        stt=deepgram.STT(model="nova-3"),
        llm=google.LLM(
            model="gemini-2.5-flash",
        ),
        tts=murf.TTS(
            voice="en-US-matthew", 
            style="Conversation",
            tokenizer=tokenize.basic.SentenceTokenizer(min_sentence_len=2),
            text_pacing=True
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
        logger.info(f"Usage: {summary}")
    
    ctx.add_shutdown_callback(log_usage)
    
    await session.start(
        agent=CoffeeBaristaAgent(),
        room=ctx.room,
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )
    
    await ctx.connect()

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm))


# import logging

# from dotenv import load_dotenv
# from livekit.agents import (
#     Agent,
#     AgentSession,
#     JobContext,
#     JobProcess,
#     MetricsCollectedEvent,
#     RoomInputOptions,
#     WorkerOptions,
#     cli,
#     metrics,
#     tokenize,
#     # function_tool,
#     # RunContext
# )
# from livekit.plugins import murf, silero, google, deepgram, noise_cancellation
# from livekit.plugins.turn_detector.multilingual import MultilingualModel

# logger = logging.getLogger("agent")

# load_dotenv(".env.local")


# class Assistant(Agent):
#     def __init__(self) -> None:
#         super().__init__(
#             instructions="""You are a helpful voice AI assistant. The user is interacting with you via voice, even if you perceive the conversation as text.
#             You eagerly assist users with their questions by providing information from your extensive knowledge.
#             Your responses are concise, to the point, and without any complex formatting or punctuation including emojis, asterisks, or other symbols.
#             You are curious, friendly, and have a sense of humor.""",
#         )

#     # To add tools, use the @function_tool decorator.
#     # Here's an example that adds a simple weather tool.
#     # You also have to add `from livekit.agents import function_tool, RunContext` to the top of this file
#     # @function_tool
#     # async def lookup_weather(self, context: RunContext, location: str):
#     #     """Use this tool to look up current weather information in the given location.
#     #
#     #     If the location is not supported by the weather service, the tool will indicate this. You must tell the user the location's weather is unavailable.
#     #
#     #     Args:
#     #         location: The location to look up weather information for (e.g. city name)
#     #     """
#     #
#     #     logger.info(f"Looking up weather for {location}")
#     #
#     #     return "sunny with a temperature of 70 degrees."


# def prewarm(proc: JobProcess):
#     proc.userdata["vad"] = silero.VAD.load()


# async def entrypoint(ctx: JobContext):
#     # Logging setup
#     # Add any other context you want in all log entries here
#     ctx.log_context_fields = {
#         "room": ctx.room.name,
#     }

#     # Set up a voice AI pipeline using OpenAI, Cartesia, AssemblyAI, and the LiveKit turn detector
#     session = AgentSession(
#         # Speech-to-text (STT) is your agent's ears, turning the user's speech into text that the LLM can understand
#         # See all available models at https://docs.livekit.io/agents/models/stt/
#         stt=deepgram.STT(model="nova-3"),
#         # A Large Language Model (LLM) is your agent's brain, processing user input and generating a response
#         # See all available models at https://docs.livekit.io/agents/models/llm/
#         llm=google.LLM(
#                 model="gemini-2.5-flash",
#             ),
#         # Text-to-speech (TTS) is your agent's voice, turning the LLM's text into speech that the user can hear
#         # See all available models as well as voice selections at https://docs.livekit.io/agents/models/tts/
#         tts=murf.TTS(
#                 voice="en-US-matthew", 
#                 style="Conversation",
#                 tokenizer=tokenize.basic.SentenceTokenizer(min_sentence_len=2),
#                 text_pacing=True
#             ),
#         # VAD and turn detection are used to determine when the user is speaking and when the agent should respond
#         # See more at https://docs.livekit.io/agents/build/turns
#         turn_detection=MultilingualModel(),
#         vad=ctx.proc.userdata["vad"],
#         # allow the LLM to generate a response while waiting for the end of turn
#         # See more at https://docs.livekit.io/agents/build/audio/#preemptive-generation
#         preemptive_generation=True,
#     )

#     # To use a realtime model instead of a voice pipeline, use the following session setup instead.
#     # (Note: This is for the OpenAI Realtime API. For other providers, see https://docs.livekit.io/agents/models/realtime/))
#     # 1. Install livekit-agents[openai]
#     # 2. Set OPENAI_API_KEY in .env.local
#     # 3. Add `from livekit.plugins import openai` to the top of this file
#     # 4. Use the following session setup instead of the version above
#     # session = AgentSession(
#     #     llm=openai.realtime.RealtimeModel(voice="marin")
#     # )

#     # Metrics collection, to measure pipeline performance
#     # For more information, see https://docs.livekit.io/agents/build/metrics/
#     usage_collector = metrics.UsageCollector()

#     @session.on("metrics_collected")
#     def _on_metrics_collected(ev: MetricsCollectedEvent):
#         metrics.log_metrics(ev.metrics)
#         usage_collector.collect(ev.metrics)

#     async def log_usage():
#         summary = usage_collector.get_summary()
#         logger.info(f"Usage: {summary}")

#     ctx.add_shutdown_callback(log_usage)

#     # # Add a virtual avatar to the session, if desired
#     # # For other providers, see https://docs.livekit.io/agents/models/avatar/
#     # avatar = hedra.AvatarSession(
#     #   avatar_id="...",  # See https://docs.livekit.io/agents/models/avatar/plugins/hedra
#     # )
#     # # Start the avatar and wait for it to join
#     # await avatar.start(session, room=ctx.room)

#     # Start the session, which initializes the voice pipeline and warms up the models
#     await session.start(
#         agent=Assistant(),
#         room=ctx.room,
#         room_input_options=RoomInputOptions(
#             # For telephony applications, use `BVCTelephony` for best results
#             noise_cancellation=noise_cancellation.BVC(),
#         ),
#     )

#     # Join the room and connect to the user
#     await ctx.connect()


# if __name__ == "__main__":
#     cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm))
