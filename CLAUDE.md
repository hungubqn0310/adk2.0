# Google ADK 2.0 — Reference

## Stability

**ADK 2.0.0 — GA (Generally Available) từ ngày 19/05/2026.** Verdict: **production-ready cho core features, còn nhiều thứ experimental.**

| Trạng thái | Ý nghĩa |
|------------|---------|
| ✅ Stable | Có thể dùng production |
| ⚠️ Experimental | API có thể thay đổi, cần enable flag |
| 🚧 WIP | Chưa dùng được |

Đếm theo decorator trong source code:
- `@experimental`: **~100 chỗ** (nhiều tools cloud, computer use, agent config)
- `@working_in_progress`: **1 chỗ** (environment simulation)
- `@stable`: không có decorator riêng — stable là mặc định

---

## Agent Types

| Agent | Stable? | Mô tả |
|-------|---------|-------|
| `LlmAgent` / `Agent` | ✅ | Agent LLM cơ bản, dùng Gemini hoặc model khác |
| `SequentialAgent` | ✅ | Chạy sub-agents **theo thứ tự** |
| `ParallelAgent` | ✅ | Chạy sub-agents **song song** |
| `LoopAgent` | ✅ | Lặp đến khi gặp `ExitLoopTool` |
| `RemoteA2AAgent` | ✅ | Gọi agent trên server khác qua A2A protocol |
| `LangGraphAgent` | ✅ | Wrap LangGraph graph thành ADK agent |

---

## Models hỗ trợ

| Model | Stable? | Cách dùng |
|-------|---------|-----------|
| Gemini (tất cả version) | ✅ | `model="gemini-2.0-flash"` — native, không cần extra |
| Anthropic (Claude) | ✅ | `model="anthropic/claude-..."` — cần `google-adk[extensions]` |
| LiteLLM (OpenAI, Mistral…) | ✅ | cần `pip install google-adk[extensions]` |
| Gemma (local) | ✅ | model local, self-hosted |

```bash
# Cài thêm để dùng non-Gemini models
pip install "google-adk[extensions]"
```

---

## Tools

### ✅ Stable — dùng ngay

| Tool | Mô tả |
|------|-------|
| `FunctionTool` | Wrap Python function thành tool |
| `LongRunningFunctionTool` | Tool async, dùng cho **human-in-the-loop** |
| `AgentTool` | Dùng một agent khác làm tool |
| `TransferToAgentTool` | Chuyển conversation sang agent khác |
| `ExitLoopTool` | Thoát khỏi `LoopAgent` |
| `GoogleSearchTool` | Tìm kiếm Google |
| `VertexAiSearchTool` | Tìm kiếm Vertex AI Search |
| `DiscoveryEngineSearchTool` | Discovery Engine |
| `LoadWebPage` | Load nội dung URL |
| `UrlContextTool` | Thêm URL vào context |
| `MCPTool` / `MCPToolset` | Tích hợp MCP servers |
| `LangChainTool` | Wrap LangChain tool |
| `CrewAITool` | Wrap CrewAI tool |
| `OpenApiToolset` | Tự generate tools từ OpenAPI spec |
| `BashTool` | Chạy shell command |
| `LoadArtifactsTool` | Load file/artifact vào context |
| `LoadMemoryTool` | Load memory vào context |
| `GoogleSearchAgentTool` | Built-in Gemini grounded search |
| `GoogleMapsTool` | Google Maps grounding |

### ⚠️ Experimental — cần enable

| Tool | Feature Flag | Mô tả |
|------|-------------|-------|
| `ToolConfirmation` | `TOOL_CONFIRMATION` | Human confirm trước khi tool chạy |
| `AuthenticatedFunctionTool` | `AUTHENTICATED_FUNCTION_TOOL` | Tool yêu cầu OAuth |
| `ComputerUse` | `COMPUTER_USE` | Điều khiển desktop/browser |
| `BigQueryToolset` | `BIG_QUERY_TOOLSET` | Truy vấn BigQuery |
| `SpannerToolset` | `SPANNER_TOOLSET` | Truy vấn Spanner |
| `BigtableToolset` | `BIGTABLE_TOOLSET` | Truy vấn Bigtable |
| `PubSubToolset` | `PUBSUB_TOOLSET` | Google Pub/Sub |
| `DataAgentToolset` | `DATA_AGENT_TOOLSET` | Data analysis agent |
| `SkillToolset` | `SKILL_TOOLSET` | Reusable skill packages |
| `GoogleTool` | `GOOGLE_TOOL` | Tất cả Google APIs qua discovery |

```python
# Enable experimental feature
from google.adk.features import override_feature_enabled, FeatureName
override_feature_enabled(FeatureName.TOOL_CONFIRMATION, True)
```

---

## Human-in-the-Loop

ADK 2.0 có 3 cơ chế:

### 1. LongRunningFunctionTool — tool trả `None` → chờ human resume
```python
from google.adk.tools import LongRunningFunctionTool
from google.adk.tools.tool_context import ToolContext

def ask_approval(action: str, tool_context: ToolContext) -> str | None:
    tool_context.actions.skip_summarization = True
    return None  # dừng, chờ human gửi kết quả

approval_tool = LongRunningFunctionTool(func=ask_approval)
```

### 2. request_confirmation — yêu cầu confirm trước khi tool thực thi
```python
def delete_record(record_id: str, tool_context: ToolContext) -> dict:
    if not tool_context.tool_confirmation?.confirmed:
        tool_context.request_confirmation(
            hint=f"Xác nhận xóa record {record_id}?",
            payload={"record_id": record_id},
        )
        return {"status": "pending"}
    return {"status": "deleted", "id": record_id}
```

### 3. get_user_choice_tool — cho user chọn từ danh sách
```python
from google.adk.tools.get_user_choice_tool import get_user_choice_tool

agent = Agent(name="...", tools=[get_user_choice_tool])
```

---

## Sessions & State

| Service | Stable? | Mô tả |
|---------|---------|-------|
| `InMemorySessionService` | ✅ | RAM, mất sau khi restart |
| `SqliteSessionService` | ✅ | Local file, persist |
| `DatabaseSessionService` | ✅ | PostgreSQL/MySQL |
| `VertexAiSessionService` | ✅ | Google Cloud |

---

## Memory

| Service | Stable? | Mô tả |
|---------|---------|-------|
| `InMemoryMemoryService` | ✅ | RAM |
| `VertexAiMemoryBankService` | ✅ | GCP, persist cross-session |
| `VertexAiRagMemoryService` | ✅ | RAG trên Vertex AI |

---

## Code Execution

| Executor | Stable? | Mô tả |
|----------|---------|-------|
| `BuiltInCodeExecutor` | ✅ | Gemini native sandbox |
| `UnsafeLocalCodeExecutor` | ✅ | Chạy local (không sandbox) |
| `ContainerCodeExecutor` | ✅ | Docker container |
| `GkeCodeExecutor` | ✅ | Google Kubernetes Engine |

---

## Planners

| Planner | Stable? | Mô tả |
|---------|---------|-------|
| `BuiltInPlanner` | ✅ | Gemini native planning |
| `PlanReActPlanner` | ✅ | ReAct-style plan → act |

---

## Evaluation Framework

✅ Stable. Dùng để đánh giá chất lượng agent:

```python
from google.adk.evaluation import AgentEvaluator

evaluator = AgentEvaluator(agent=my_agent)
# Các metrics: final_response_match, hallucinations, tool_use_quality,
# trajectory_quality, safety, multi_turn_task_success
```

---

## A2A (Agent-to-Agent Protocol)

✅ Stable. Điểm nổi bật của 2.0 — agent giao tiếp với agent khác qua HTTP:

```python
from google.adk.agents import RemoteA2AAgent

remote = RemoteA2AAgent(
    name="remote_worker",
    agent_url="http://other-service:8080",
)
agent = Agent(name="orchestrator", sub_agents=[remote])
```

Tương thích với LangGraph, CrewAI, và bất kỳ framework nào implement A2A spec.

---

## Web UI & CLI

```bash
# Web UI tích hợp sẵn (không cần config gì thêm)
adk web                    # chạy tại http://localhost:8000

# Chạy agent qua CLI
adk run demo_agent/

# Chạy evaluation
adk eval demo_agent/ eval_set.json
```

---

## Verdict: Có nên dùng production chưa?

| Use case | Khuyến nghị |
|----------|------------|
| Agent đơn với Gemini + Python tools | ✅ Dùng được |
| Multi-agent (Sequential/Parallel) | ✅ Dùng được |
| A2A cross-framework | ✅ Dùng được |
| Human-in-the-loop cơ bản | ✅ Dùng được |
| Tool confirmation | ⚠️ Experimental, cẩn thận |
| BigQuery/Spanner/Bigtable tools | ⚠️ Experimental |
| Computer Use | ⚠️ Experimental |
| Non-Gemini models (Claude, OpenAI) | ⚠️ Cần `[extensions]`, ít test hơn |

**Kết luận:** ADK 2.0 đã **GA chính thức (19/05/2026)**. Core pipeline production-ready.
Các cloud tools (BigQuery, Spanner…) và `ToolConfirmation` → vẫn `@experimental` trong code, API có thể đổi.

---

## Graph-based Workflows (mới trong 2.0)

Thay vì để LLM tự quyết "làm gì tiếp theo", bạn **vẽ luồng bằng code** — AI chỉ xử lý bước cần reasoning, còn lại là Python thuần.

### Workflow class

```python
from google.adk import Agent, Workflow, Event
```

### Sequential — chạy thẳng từng bước

```python
root_agent = Workflow(
    name="my_workflow",
    edges=[
        ("START", agent_1, python_function, agent_2, done_function)
    ],
)
```

### Conditional routing — rẽ nhánh theo kết quả

```python
def router(node_input: str):
    routes = node_input.split(",")
    return Event(route=[r.strip() for r in routes])

root_agent = Workflow(
    name="routing_workflow",
    edges=[
        ("START", classify_agent, router),
        (router, {
            "BUG": handle_bug,
            "SUPPORT": handle_support,
            "LOGISTICS": handle_logistics,
        }),
    ],
)
```

### Node types có thể dùng trong Workflow

| Node type | Mô tả |
| --------- | ------ |
| `Agent` | LLM reasoning |
| Python function | Logic xác định, không gọi LLM |
| `Tool` | Công cụ tích hợp |
| `Workflow` lồng nhau | Nested workflow |
| Human input | Điểm dừng chờ user |

### `Event` — object điều phối luồng

```python
# Kết thúc với message
return Event(message="Workflow done.")

# Rẽ nhánh
return Event(route=["BUG", "SUPPORT"])

# Kết hợp
return Event(message="Routed.", route=["BUG"])
```

### Khi nào dùng Workflow thay vì Agent thường?

| Dùng `Agent` | Dùng `Workflow` |
| ------------ | --------------- |
| Luồng linh hoạt, LLM tự quyết | Luồng cố định, cần đảm bảo thứ tự |
| Task đơn giản | Pipeline nhiều bước, rẽ nhánh rõ ràng |
| Không cần kiểm soát routing | Cần reliability cao, ít phụ thuộc LLM |

### Hạn chế Workflow

- ❌ Không hỗ trợ **Live Streaming**
- ❌ Một số 3rd-party integrations chưa tương thích

### Ví dụ đầy đủ — City Time Workflow

```python
from google.adk import Agent, Workflow, Event
from pydantic import BaseModel

city_agent = Agent(
    name="city_agent",
    model="gemini-2.0-flash",
    instruction="Return the name of a random city. Return only the name.",
    output_schema=str,
)

class CityTime(BaseModel):
    city: str
    time_info: str

def lookup_time(node_input: str) -> CityTime:
    return CityTime(city=node_input, time_info="10:10 AM")

report_agent = Agent(
    name="report_agent",
    model="gemini-2.0-flash",
    input_schema=CityTime,
    instruction="Output: It is {CityTime.time_info} in {CityTime.city} right now.",
    output_schema=str,
)

def done(node_input: str):
    return Event(message=f"{node_input}\nWORKFLOW COMPLETED.")

root_agent = Workflow(
    name="city_workflow",
    edges=[("START", city_agent, lookup_time, report_agent, done)],
)
```

---

## Collaboration Workflows — Multi-Agent

Coordinator agent phân công task cho các subagent chuyên biệt. Phù hợp cho "các quy trình ít cấu trúc hơn, đặc biệt là các task lặp lại có nhiều bước lớn."

### 3 chế độ hoạt động của subagent

| Mode | User interaction | Parallel? | Ghi chú |
| ---- | ---------------- | --------- | ------- |
| `chat` (default) | Đầy đủ | ❌ | Manual handoff qua `transfer_to_agent` |
| `task` | Chỉ hỏi làm rõ | ❌ | Tự trả về parent khi xong. Bị tắt trong Graph workflows |
| `single_turn` | Không có | ✅ | Hiệu quả nhất, hỗ trợ chạy song song |

### Cách dùng

```python
from google.adk import Agent

weather_agent = Agent(
    name="weather_checker",
    mode="single_turn",          # không cần user, chạy parallel được
    tools=[get_weather, geocode_address],
)

flight_agent = Agent(
    name="flight_booker",
    mode="task",                 # có thể hỏi user để làm rõ
    input_schema=FlightInput,
    output_schema=FlightResult,
    tools=[search_flights, book_flight],
)

root = Agent(
    name="travel_planner",
    sub_agents=[weather_agent, flight_agent],  # coordinator tự inject delegation tools
)
```

ADK tự động tạo tool `request_task_<subagent_name>` cho coordinator để gọi subagent — không cần viết thêm gì.

### Cách agents giao tiếp

- **Chat mode**: coordinator gọi `transfer_to_agent` → trao quyền cho subagent
- **Task / Single-turn**: coordinator gọi `request_task_<name>` → subagent tự `complete_task` → trả kết quả về coordinator

### State isolation

Mỗi task/single-turn agent chạy trong **session branch riêng biệt** — không thấy được activity của agent song song khác. Chỉ parent nhận kết quả tổng hợp sau khi tất cả branch hoàn tất.

### Hạn chế

- `task` mode agent phải là **leaf node** (không có subagent con)
- `task` mode **bị tắt** trong Graph-based Workflows (v2.0.0)
- `mode` chỉ áp dụng cho subagent, không áp dụng cho root agent

---

## Dynamic Workflows — Workflow bằng code Python thuần

Thay vì khai báo graph tĩnh với `edges`, Dynamic Workflows dùng **decorator `@node` + `ctx.run_node()`** — viết workflow bằng Python bình thường với loops, conditionals, recursion.

### Core components

| Thành phần | Mô tả |
| ---------- | ------ |
| `@node` | Decorator bọc function/agent thành một node |
| `Workflow` | Container orchestrate các node |
| `ctx.run_node()` | Gọi node từ trong workflow, trả về output trực tiếp |
| `rerun_on_resume` | `True` nếu node cần chạy lại khi resume sau human-in-the-loop |

### Ví dụ cơ bản

```python
from google.adk import node, Workflow, Context

@node(name="hello_node")
def my_node(node_input: str):
    return "Hello World"

@node(rerun_on_resume=True)
async def my_workflow(ctx: Context, node_input: str) -> str:
    result = await ctx.run_node(my_node, node_input="hello")
    return result
```

### Sequential — chạy tuần tự, pass output qua nhau

```python
@node(rerun_on_resume=True)
async def editorial_workflow(ctx: Context, user_request: str):
    raw_draft = await ctx.run_node(draft_agent, user_request)
    formatted = await ctx.run_node(format_function_node, raw_draft)
    return formatted
```

### Loop — lặp với Python while

```python
@node(rerun_on_resume=True)
async def code_review_loop(ctx: Context, spec: str):
    code = await ctx.run_node(write_code_agent, spec)
    while True:
        review = await ctx.run_node(review_agent, code)
        if review.approved:
            break
        code = await ctx.run_node(fix_agent, review.issues)
    return code
```

### Parallel — dùng `asyncio.gather()`

```python
import asyncio

@node(rerun_on_resume=True)
async def parallel_supervisor(ctx: Context, node_input: list):
    tasks = [ctx.run_node(worker_node, item) for item in node_input]
    results = await asyncio.gather(*tasks)
    return results
```

### Human-in-the-loop — dừng chờ user

```python
from google.adk import RequestInput

@node(rerun_on_resume=False)  # False vì node này chỉ yield, không chạy lại
async def get_user_approval(ctx: Context, node_input):
    yield RequestInput(message="Bạn có đồng ý không? (Yes/No)")
```

Parent node gọi node này phải có `rerun_on_resume=True`.

### Checkpointing tự động

ADK tự động skip các sub-node đã chạy thành công khi resume — không cần tự quản lý state.

### So sánh: Dynamic vs Graph-based Workflow

| | Graph-based Workflow | Dynamic Workflow |
| - | -------------------- | ---------------- |
| Khai báo | `edges=[...]` | Python code (`while`, `if`, `gather`) |
| Routing | `Event(route=...)` | Python control flow |
| Loop | Cần cấu trúc đặc biệt | `while True:` bình thường |
| Parallel | Hạn chế | `asyncio.gather()` |
| Checkpointing | ✅ | ✅ tự động |
| Dùng khi | Pipeline rõ ràng, ít nhánh | Logic phức tạp, vòng lặp, đệ quy |

### Lưu ý khi dùng

- Custom `run_id` không nên dùng — có thể phá vỡ retry logic
- Parent node cần `rerun_on_resume=True` khi gọi `ctx.run_node()`

---

## Setup nhanh

```bash
# Venv riêng tại /home/khanh/hungpq/adk2.0/.venv
# Activate:
source /home/khanh/hungpq/adk2.0/.venv/bin/activate

# Cài thêm non-Gemini support:
pip install "google-adk[extensions]"

# Chạy demo:
cd /home/khanh/hungpq/adk2.0/demo_agent
python main.py          # CLI
adk web                 # Web UI
```
