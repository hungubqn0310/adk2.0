# ADK 2.0 Demo

google-adk **2.0.0** với Gemini 3.5 Flash, isolated hoàn toàn khỏi các project khác.

## Setup

```bash
cd .../adk2.0
cp .env.example .env
# Điền GOOGLE_API_KEY vào .env
```

## Chạy DOCKER
docker compose up -d

## Cấu trúc

```
adk2.0/
├── .venv/               # venv riêng, chỉ có google-adk==2.0.0
└── agent/
    ├── agent.py         # root_agent — ADK 2.0 entry point
    ├── tools.py         
    ├── prompts.py         
```

## Version

- google-adk: 2.2
- Model: gemini-3.5-flash
- Python: 3.12
# adk2.0
