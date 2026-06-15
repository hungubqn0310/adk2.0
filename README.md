# ADK 2.0 Demo

google-adk **2.0.0** với Gemini 3.5 Flash, isolated hoàn toàn khỏi các project khác.

## Setup

```bash
cd .../adk2.0/demo_agent
cp .env.example .env
# Điền GOOGLE_API_KEY vào .env
```

## Chạy CLI

```bash
.../adk2.0/.venv/bin/python main.py
```

## Chạy Web UI (ADK built-in)

```bash
cd .../adk2.0/demo_agent
.../adk2.0/.venv/bin/adk web
# Mở http://localhost:8000
```

## Cấu trúc

```
adk2.0/
├── .venv/               # venv riêng, chỉ có google-adk==2.0.0
└── demo_agent/
    ├── agent.py         # root_agent — ADK 2.0 entry point
    ├── tools.py         # 3 tools: time, dice, calculator
    ├── main.py          # CLI runner
    └── .env.example
```

## Tools có sẵn

| Tool | Mô tả |
|------|--------|
| `get_current_time` | Trả về giờ/ngày hiện tại |
| `roll_dice(sides)` | Tung xúc xắc N mặt |
| `calculate(expr)` | Tính biểu thức toán học |

## Version

- google-adk: 2.0.0
- Model: gemini-3.5-flash
- Python: 3.12
# adk2.0
