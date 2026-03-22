<h1 align="center">Mantis</h1>

<p align="center">
  <a href="https://pypi.org/project/mantis/"><img src="https://img.shields.io/pypi/pyversions/mantis" alt="Python"></a>
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
</p>

<p><strong>Execution engine for AI agents.</strong></p>

The engine is just two things: a Generator and an Executor. Everything else is a tool.

```python
from mantis import Agent, tool, ToolRegistry
from mantis.llm import ModelClient

@tool(name="greet", description="Greet someone by name",
      parameters={"name": {"type": "string", "description": "Name to greet"}})
async def greet(name: str) -> dict:
    return {"message": f"Hello {name}"}

registry = ToolRegistry()
registry.register(greet._tool_spec)

agent = Agent(
    name="bot",
    model_client=ModelClient(base_url="https://api.openai.com/v1", model="gpt-4o-mini", api_key="sk-..."),
    tool_registry=registry,
)
result = await agent.run("Say hello to Alice")
```

## Installation

```bash
pip install mantis              # core: Agent + @tool + LLM client
pip install mantis[search]      # + graph-tool-call retrieval
pip install mantis[sandbox]     # + Docker sandbox
pip install mantis[state]       # + PostgreSQL checkpointing
pip install mantis[all]         # everything
```

Only required dependency is `httpx`.

## Core Design — Everything is a Tool

```
If it can be a tool → make it a tool.
Only what can't be a tool → is engine.
```

**Engine (3 components)**

| Component | Role |
|-----------|------|
| `Agent` | Think→Act→Observe loop. The entity that calls tools. |
| `ToolGenerator` | Creates tools. LLM code generation → sandbox validation → registry. |
| `WorkflowGenerator` | Creates workflows. LLM design → tool validation → store. |

**Everything else = tools**

| Tool | What it does |
|------|-------------|
| `execute_code` | Run Python code in a Docker container |
| `execute_code_with_test` | Run code + test together |
| `create_tool` | AI tool generation → validation → registration |
| `generate_workflow` | Auto-design a workflow from natural language |
| `create_workflow` | Manually define a workflow |
| `run_workflow` | Execute a saved workflow |
| `list_workflows` | List registered workflows |
| `search_tools` | Search for tools by query |
| `list_tools` | List all available tools |
| `manage_session` | Resume, delete, or list session checkpoints |

## Streaming

```python
async for event in agent.run_stream("Look up my order"):
    match event["type"]:
        case "thinking":    print(f"thinking... (iter {event['data']['iteration']})")
        case "tool_call":   print(f"calling: {event['data']['name']}")
        case "tool_result": print(f"result: {event['data']}")
        case "done":        print(f"done: {event['data']}")
```

## Live ToolRegistry

One registry shared by all components. Tools created mid-conversation are available on the next iteration.

```python
# Iteration 1: LLM → create_tool("send slack message") → generated + registered
# Iteration 2: LLM → send_slack(channel="#general", text="hello") → works immediately
```

Session-scoped tool isolation:

```python
registry = ToolRegistry()
registry.register(spec, source="builtin")                          # global
registry.register(spec, source="generated", session_id="sess-123") # session-only
tools = registry.to_openai_tools(session_id="sess-123")            # global + session merged
registry.cleanup_session("sess-123")                                # cleanup on session end
```

## LLMProvider Protocol

Plug in any LLM implementation:

```python
from mantis import Agent, LLMProvider
from mantis.llm import ModelClient

# Built-in: OpenAI-compatible
agent = Agent(name="bot", model_client=ModelClient(base_url="...", model="gpt-4o"), ...)

# Custom LLM — just match the Protocol
class MyLLM:
    async def generate(self, messages, tools=None, temperature=0.7):
        # call your own server
        return ModelResponse(text="...", tool_calls=[])

agent = Agent(name="bot", model_client=MyLLM(), ...)
```

## Middleware

Things that must run automatically (not chosen by the agent) = middleware.

```python
from mantis.middleware import (
    ApprovalMiddleware, TraceMiddleware,
    GraphSearchMiddleware, AutoCorrectMiddleware, StateMiddleware,
)

agent = Agent(
    name="bot",
    model_client=llm,
    tool_registry=registry,
    middlewares=[
        TraceMiddleware(),                                          # auto-record execution flow
        ApprovalMiddleware(patterns=["DELETE *", "send_slack"]),     # block dangerous actions
        GraphSearchMiddleware(threshold=15),                         # auto-filter when 15+ tools
        AutoCorrectMiddleware(),                                     # fix tool name typos
        StateMiddleware(store=StateStore()),                         # auto checkpoint/restore
    ],
)
```

Middleware hooks:

| Hook | When | Purpose |
|------|------|---------|
| `on_start` | Execution start | Session restore |
| `on_before_llm` | Before LLM call | Tool filtering |
| `on_before_tool` | Before tool call | Approval / auto-correction |
| `on_after_tool` | After tool call | Tracing / checkpointing |
| `on_end` | Execution end | State persistence |

## Sandbox as a Tool

The agent can freely run code:

```python
from mantis.sandbox import DockerSandbox, make_sandbox_tools

sandbox = DockerSandbox()
for spec in make_sandbox_tools(sandbox):
    registry.register(spec, source="sandbox")

# Agent calls:
#   execute_code(code="import pandas; df = pd.read_csv('data.csv'); print(df.describe())")
#   execute_code_with_test(code="def add(a,b): return a+b", test_code="assert add(1,2)==3; print('ALL_TESTS_PASSED')")
```

## AI Tool Generation

Create tools at runtime — generate code, test in sandbox, register instantly:

```python
from mantis.generate import ToolGenerator, make_create_tool

generator = ToolGenerator(model_client=llm, tool_registry=registry)
registry.register(make_create_tool(generator), source="builtin")

# Agent calls:
#   create_tool("fetch weather data from OpenWeatherMap API")
#   → LLM generates code → sandbox validates → registry registers → next iteration uses it
```

## Workflows

Auto-design and execute complex multi-step tasks:

```python
from mantis.workflow import WorkflowStore, WorkflowRunner, WorkflowGenerator, make_workflow_tools

wf_store = WorkflowStore()
wf_runner = WorkflowRunner(registry=registry, model_client=llm)
wf_generator = WorkflowGenerator(model_client=llm, registry=registry, store=wf_store)

for spec in make_workflow_tools(wf_store, wf_runner, wf_generator):
    registry.register(spec, source="builtin")

# Agent calls:
#   generate_workflow("analyze sales data and create a report")
#   → LLM sees available tools, designs optimal step sequence
#   → run_workflow("sales_analysis", input_data={"file": "sales.csv"})
```

Workflow step types:

| Type | What it does |
|------|-------------|
| `tool` | Call a tool from the registry directly |
| `condition` | Branch based on previous step results |
| `agent` | Delegate to a sub-agent |
| `parallel` | Run multiple steps concurrently |

## Full Example

```python
from mantis import Agent, tool, ToolRegistry
from mantis.llm import ModelClient
from mantis.sandbox import DockerSandbox, make_sandbox_tools
from mantis.generate import ToolGenerator, make_create_tool
from mantis.workflow import WorkflowStore, WorkflowRunner, WorkflowGenerator, make_workflow_tools
from mantis.tools.meta import make_registry_tools
from mantis.middleware import ApprovalMiddleware, TraceMiddleware

# Engine
registry = ToolRegistry()
llm = ModelClient(base_url="https://api.openai.com/v1", model="gpt-4o-mini", api_key="sk-...")

# User tools
@tool(name="lookup_order", description="Look up order status by ID",
      parameters={"order_id": {"type": "string", "description": "Order ID"}})
async def lookup_order(order_id: str) -> dict:
    return {"order_id": order_id, "status": "shipped"}
registry.register(lookup_order._tool_spec, source="builtin")

# Sandbox tools
sandbox = DockerSandbox()
for spec in make_sandbox_tools(sandbox):
    registry.register(spec, source="sandbox")

# Tool generator
generator = ToolGenerator(model_client=llm, tool_registry=registry)
registry.register(make_create_tool(generator), source="builtin")

# Workflow tools
wf_store = WorkflowStore()
wf_runner = WorkflowRunner(registry=registry, model_client=llm)
wf_gen = WorkflowGenerator(model_client=llm, registry=registry, store=wf_store)
for spec in make_workflow_tools(wf_store, wf_runner, wf_gen):
    registry.register(spec, source="builtin")

# Registry meta tools
for spec in make_registry_tools(registry):
    registry.register(spec, source="builtin")

# Agent + middleware
agent = Agent(
    name="full-agent",
    model_client=llm,
    tool_registry=registry,
    system_prompt="You are a helpful assistant.",
    middlewares=[
        TraceMiddleware(),
        ApprovalMiddleware(patterns=["DELETE *"]),
    ],
)

# Run
async for event in agent.run_stream("Build a sales analysis workflow and run it"):
    print(event)
```

## Standalone Usage

Every infrastructure module works independently without the Agent:

```python
# Sandbox only
from mantis.sandbox import DockerSandbox
result = await DockerSandbox().execute("print(sum(range(1, 101)))")

# Tool search only (pip install mantis[search])
from mantis.search import GraphToolManager
graph = GraphToolManager()
graph.ingest_from_registry(registry)
results = graph.retrieve("order lookup", top_k=5)
```

## Package Structure

```
mantis/
├── __init__.py                  # Agent, tool, ToolRegistry, LLMProvider, exceptions
├── exceptions.py                # MantisError hierarchy
│
├── engine/
│   └── runner.py                # Agent — Think→Act→Observe + middleware chain
│
├── tools/
│   ├── decorator.py             # @tool, ToolSpec
│   ├── registry.py              # ToolRegistry (session scope, source tracking)
│   └── meta.py                  # make_registry_tools() — search_tools, list_tools
│
├── llm/
│   ├── protocol.py              # LLMProvider Protocol, ModelResponse, ToolCall
│   └── openai_provider.py       # ModelClient (OpenAI-compatible, default impl)
│
├── middleware/
│   ├── base.py                  # Middleware Protocol, RunContext, BaseMiddleware
│   ├── approval.py              # ApprovalMiddleware
│   ├── trace.py                 # TraceMiddleware
│   ├── graph_search.py          # GraphSearchMiddleware, AutoCorrectMiddleware
│   └── state.py                 # StateMiddleware
│
├── generate/
│   └── tool_generator.py        # ToolGenerator + make_create_tool()
│
├── sandbox/                     # pip install mantis[sandbox]
│   ├── sandbox.py               # DockerSandbox
│   └── tools.py                 # make_sandbox_tools()
│
├── workflow/
│   ├── models.py                # WorkflowDef, WorkflowStep, StepExecutor Protocol
│   ├── store.py                 # WorkflowStore (in-memory)
│   ├── runner.py                # WorkflowRunner (tool/condition/agent/parallel)
│   ├── generator.py             # WorkflowGenerator (LLM auto-design)
│   └── tools.py                 # make_workflow_tools() — 4 workflow tools
│
├── search/                      # pip install mantis[search]
│   └── graph_search.py          # GraphToolManager (graph-tool-call integration)
│
├── context/
│   └── conversation.py          # ConversationContext
│
├── safety/
│   └── approval.py              # ApprovalManager
│
├── state/                       # pip install mantis[state]
│   ├── store.py                 # StateStore (PostgreSQL)
│   └── tools.py                 # make_state_tools() — manage_session
│
├── trace/
│   ├── collector.py             # TraceCollector
│   └── exporter.py              # TraceExporter
│
├── testing/
│   ├── tool_tester.py           # ToolTester (smoke/assert/pytest)
│   ├── dummy_args.py            # Dummy argument generation
│   └── pytest_runner.py         # In-sandbox pytest execution
│
└── adapters/
    ├── sse_adapter.py           # SSE event format conversion
    └── canvas_adapter.py        # xgen-workflow canvas JSON → WorkflowDef
```

## Design Principles

- **Everything is a Tool** — Workflows, sandbox, search are all tools. Engine is just loop + generators + storage.
- **Middleware for cross-cutting concerns** — Approval, tracing, state persistence hook into the agent loop automatically.
- **LLMProvider Protocol** — Plug in any LLM implementation. OpenAI-compatible client included.
- **`make_*_tools()` pattern** — Consistent factory pattern to convert infrastructure into tools: `make_sandbox_tools()`, `make_create_tool()`, `make_workflow_tools()`, `make_registry_tools()`, `make_state_tools()`.
- **Live Registry** — Create tools mid-conversation, use them immediately. Session-scoped isolation.
- **Single required dependency** — Only `httpx`. Everything else is optional.
- **Standalone modules** — Use sandbox, search, or any module independently without the Agent.

## License

MIT
