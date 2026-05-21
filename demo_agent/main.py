"""CLI runner để test agent qua terminal."""
import asyncio
import os

from dotenv import load_dotenv
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

load_dotenv()

from agent import root_agent  # noqa: E402 — load .env trước


async def run_cli() -> None:
    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name="demo_agent_app",
        user_id="user_01",
    )

    runner = Runner(
        agent=root_agent,
        app_name="demo_agent_app",
        session_service=session_service,
    )

    print("ADK 2.0 Demo Agent — gõ 'exit' để thoát\n")

    while True:
        user_input = input("You: ").strip()
        if not user_input or user_input.lower() in ("exit", "quit"):
            break

        message = Content(role="user", parts=[Part(text=user_input)])

        final_text = ""
        async for event in runner.run_async(
            user_id="user_01",
            session_id=session.id,
            new_message=message,
        ):
            if event.is_final_response() and event.content:
                for part in event.content.parts:
                    if part.text:
                        final_text += part.text

        print(f"Agent: {final_text}\n")


if __name__ == "__main__":
    asyncio.run(run_cli())
