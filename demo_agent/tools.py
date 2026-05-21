"""Demo tools for ADK 2.0 agent."""
import datetime
import random


def get_current_time() -> dict:
    """Trả về giờ hiện tại."""
    now = datetime.datetime.now()
    return {
        "time": now.strftime("%H:%M:%S"),
        "date": now.strftime("%Y-%m-%d"),
        "weekday": now.strftime("%A"),
    }


def roll_dice(sides: int = 6) -> dict:
    """Tung xúc xắc với số mặt tùy chọn.

    Args:
        sides: Số mặt của xúc xắc (mặc định 6).
    """
    if sides < 2:
        return {"error": "Xúc xắc cần ít nhất 2 mặt"}
    result = random.randint(1, sides)
    return {"result": result, "sides": sides}


def calculate(expression: str) -> dict:
    """Tính toán biểu thức toán học đơn giản.

    Args:
        expression: Biểu thức cần tính (vd: '2 + 3 * 4').
    """
    allowed = set("0123456789+-*/(). ")
    if not all(c in allowed for c in expression):
        return {"error": "Biểu thức chứa ký tự không hợp lệ"}
    try:
        result = eval(expression)  # noqa: S307 — sandbox: whitelist-only chars
        return {"result": result, "expression": expression}
    except Exception as e:
        return {"error": str(e)}
