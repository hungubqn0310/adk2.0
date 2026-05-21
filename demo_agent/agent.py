"""ADK 2.0 demo agent — dùng Gemini native model."""
from google.adk.agents import Agent

from tools import calculate, get_current_time, roll_dice

root_agent = Agent(
    name="demo_agent",
    model="gemini-3.5-flash",
    description="Agent demo ADK 2.0 với Gemini 3.5 Flash.",
    instruction=(
        "Bạn là trợ lý AI thân thiện, sử dụng tiếng Việt. "
        "Bạn có thể: xem giờ hiện tại, tung xúc xắc, và tính toán biểu thức. "
        "Hãy dùng các tool khi người dùng yêu cầu thay vì tự trả lời."
    ),
    tools=[get_current_time, roll_dice, calculate],
)
