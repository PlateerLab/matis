# Mantis

[![Python](https://img.shields.io/pypi/pyversions/mantis)](https://pypi.org/project/mantis/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Execution engine for AI agents.**

Runs the Think → Act → Observe loop as a library. Tools flow dynamically across generators, executors, and validators — create a tool mid-conversation and use it immediately on the next iteration.

## The Problem

AI agent execution code tends to collapse into a single function with no structure:

```python
# A real production file — 694 lines, one function, 9 concerns:
async def execute(workflow_data, user_input, ...):
    load_workflow()          # DB
    apply_file_selection()   # preprocessing
    create_executor()        # LangChain black box
    async for chunk in stream:
        parse_agent_event()  # regex on "[AGENT_EVENT]" tags
        sanitize_io()        # postprocessing
    save_to_db()             # persistence
    update_redis()           # state
```

| Problem | Status quo |
|---------|-----------|
| LangChain lock-in | Agent execution is a black box — can't detach |
| All 30 tools sent to LLM | Token waste + accuracy drop |
| Tools are static | Can't create tools mid-conversation |
| No sandbox as tool | Agent can't run code on demand |
| No approval workflow | Dangerous actions run unchecked |
| No failure recovery | Error = restart from scratch |
| Workflows need a canvas | Can't compose workflows in code |

**Mantis replaces all of this with a phase-based pipeline and a live tool registry.**

## Installation

```bash
pip install mantis              # core: Agent + @tool + LLM client
pip install mantis[search]      # + graph-tool-call retrieval
pip install mantis[sandbox]     # + Docker sandbox
pip install mantis[state]       # + PostgreSQL checkpointing
pip install mantis[all]         # everything
```

## Quick Start

```python
from mantis import Agent, tool, ToolRegistry
from mantis.llm.openai_provider import ModelClient

@tool(name="lookup_order", description="Look up order status by ID")
async def lookup_order(order_id: str) -> dict:
    return {"order_id": order_id, "status": "shipped"}

registry = ToolRegistry()
registry.register(lookup_order._tool_spec, source="builtin")

agent = Agent(
    name="order-bot",
    model_client=ModelClient(model="gpt-4o-mini", api_key="sk-..."),
    tool_registry=registry,
    system_prompt="You answer questions about orders.",
)

result = await agent.run("What's the status of order ABC-123?")
```

## Streaming

```python
async for event in agent.run_stream("Look up my order"):
    match event["type"]:
        case "thinking":    print(f"thinking... (iter {event['data']['iteration']})")
        case "tool_call":   print(f"calling: {event['data']['name']}")
        case "tool_result": print(f"result: {event['data']}")
        case "done":        print(f"done: {event['data']}")
```

## Live Tool Registry

The core design: **one ToolRegistry shared by all components**. Tools created mid-conversation are available on the next iteration.

```python
# Agent creates a tool → immediately usable
# Iteration 1: LLM calls create_tool("send slack message") → generated + registered
# Iteration 2: LLM calls send_slack(channel="#general", text="hello") → works
```

Session-scoped tools are isolated per conversation:

```python
registry = ToolRegistry()

# Global tool — visible to all sessions
registry.register(spec, source="builtin")

# Session tool — visible only in this session, cleaned up on end
registry.register(spec, source="generated", session_id="sess-123")

# Merge global + session tools for LLM
tools = registry.to_openai_tools(session_id="sess-123")
```

## Sandbox as a Tool

The sandbox isn't just infrastructure — it's a tool the agent can call directly:

```python
from mantis.sandbox.sandbox import DockerSandbox
from mantis.sandbox.tools import make_sandbox_tools

sandbox = DockerSandbox()
for spec in make_sandbox_tools(sandbox):
    registry.register(spec, source="sandbox")

# Now the agent can freely run code:
#   LLM → execute_code(code="import pandas; df = pd.read_csv(...)")
#   LLM → execute_code_with_test(code="...", test_code="assert ...")
```

## AI Tool Generation

Create tools at runtime — generate code, test in sandbox, register instantly:

```python
from mantis.generate.tool_generator import ToolGenerator, make_create_tool

generator = ToolGenerator(llm=client, registry=registry, sandbox=sandbox)
registry.register(make_create_tool(generator), source="builtin")

# Agent can now call create_tool("fetch weather data from OpenWeatherMap API")
# → LLM generates code → sandbox validates → registry registers → next iteration uses it
```

## Architecture

### 5-Phase Pipeline

```
PREPARE  →  RESOLVE  →  EXECUTE  →  STREAM  →  PERSIST
```

| Phase | Role | What it does |
|-------|------|-------------|
| **PREPARE** | Setup | Session init, DAG sort, context assembly |
| **RESOLVE** | Decide | Tool verification, graph search, RAG, memory load |
| **EXECUTE** | Run | Think→Act→Observe loop with live tool refresh each iteration |
| **STREAM** | Deliver | Execution events → SSE / JSON / workflow format |
| **PERSIST** | Save | DB write, trace flush, state update (runs even on error) |

### Tool Flow

```
Sources                      ToolRegistry                Consumers
────────                     ────────────                ─────────
@tool decorator    ──register──→                    ──→  Executor (refresh each iter)
make_sandbox_tools ──register──→  to_openai_tools   ──→  GraphToolManager (search)
make_create_tool   ──register──→  (session_id)      ──→  LLM (function calling)
MCP Bridge         ──register──→                    ──→  WorkflowEngine (node binding)
OpenAPI Loader     ──register──→
```

### Workflow Engine (planned)

Replace xgen-workflow's canvas executor with code-composable workflows:

```python
from mantis.workflow import WorkflowEngine, AgentNode, RouterNode, Edge

engine = WorkflowEngine(registry=registry)
engine.add_node(AgentNode(id="analyze", model="gpt-4o-mini"))
engine.add_node(RouterNode(id="check", conditions={
    "good": lambda s: s["confidence"] > 0.8,
    "retry": lambda s: s["confidence"] <= 0.8,
}))

engine.add_edge(Edge("analyze", "result", "check", "input"))
engine.add_edge(Edge("check", "retry", "analyze", "text"))  # loop back

# Or load from canvas JSON (xgen-workflow compatible):
engine = WorkflowEngine.from_canvas(workflow_json, registry)
```

## Package Structure

```
mantis/
├── __init__.py             # Public API: Agent, tool, ToolSpec, ToolRegistry
├── __main__.py             # CLI: python -m mantis
├── engine/
│   └── runner.py           # Agent — Think→Act→Observe with live tool refresh
├── tools/
│   ├── decorator.py        # @tool decorator + ToolSpec
│   └── registry.py         # ToolRegistry — session scope, source tracking
├── llm/
│   └── openai_provider.py  # OpenAI-compatible LLM client
├── pipeline/
│   ├── pipeline.py         # ExecutionPipeline — 5-phase orchestrator
│   ├── phases.py           # Phase implementations
│   └── models.py           # ExecutionContext, ExecutionEvent, etc.
├── context/
│   └── conversation.py     # Multi-turn conversation management
├── safety/
│   └── approval.py         # Pattern-based approval workflow
├── trace/
│   ├── collector.py        # Execution tracing
│   └── exporter.py         # Trace export (JSON, log)
├── search/                 # requires: mantis[search]
│   └── graph_search.py     # graph-tool-call retrieval + auto-correction
├── sandbox/                # requires: mantis[sandbox]
│   ├── sandbox.py          # Docker container isolation
│   ├── runner.py           # Legacy sandbox tools
│   └── tools.py            # make_sandbox_tools() factory
├── generate/
│   └── tool_generator.py   # AI tool generation → test → register + make_create_tool()
├── testing/
│   ├── tool_tester.py      # Tool quality gate (smoke/assert/pytest)
│   ├── dummy_args.py       # Type-based dummy argument generation
│   └── pytest_runner.py    # In-sandbox pytest execution
├── state/                  # requires: mantis[state]
│   └── store.py            # PostgreSQL checkpoint/resume
└── adapters/
    └── sse_adapter.py      # SSE event format conversion
```

## Features

### Core

| Feature | Description |
|---------|------------|
| `Agent` | Think→Act→Observe loop — refreshes tools from registry each iteration |
| `@tool` | Decorator that auto-generates OpenAI function calling schema |
| `ToolRegistry` | Session-scoped, source-tracked tool management with live refresh |
| `ModelClient` | OpenAI-compatible API client with streaming support |
| `ConversationContext` | Multi-turn message history management |
| `ApprovalManager` | Pattern-based dangerous action blocking |
| `TraceCollector` | Step-by-step execution recording |
| `ExecutionPipeline` | 5-phase pipeline orchestrator |

### Optional

| Feature | Install | Description |
|---------|---------|------------|
| `GraphToolManager` | `mantis[search]` | Graph-based tool retrieval + name auto-correction |
| `DockerSandbox` | `mantis[sandbox]` | Isolated code execution in Docker containers |
| `make_sandbox_tools` | `mantis[sandbox]` | Expose sandbox as agent-callable tools |
| `StateStore` | `mantis[state]` | PostgreSQL checkpointing and failure recovery |
| `ToolGenerator` | needs sandbox | AI generates code → tests in sandbox → auto-registers |
| `make_create_tool` | needs sandbox | Wrap ToolGenerator as an agent-callable tool |
| `ToolTester` | sandbox optional | Tool quality gate — smoke test, assert, pytest |

### Planned

| Feature | Description |
|---------|------------|
| `WorkflowEngine` | Code-composable DAG execution — replaces canvas-only workflows |
| `Tool Store` | Publish, verify, and install tools from Git or API registries |
| `RedisBackend` | Share ToolRegistry across multiple apps via Redis |

## CLI

```bash
python -m mantis --version
python -m mantis info
```

```
mantis 0.1.0

  [O] httpx 0.28.1 — required
  [O] graph-tool-call 0.13.1 — optional (pip install mantis[search])
  [X] docker — optional (pip install mantis[sandbox])
  [X] asyncpg — optional (pip install mantis[state])
```

## Design Principles

- **Live tool registry** — one shared registry, tools refresh each iteration, create and use in the same conversation
- **Phase-based pipeline** — PREPARE → RESOLVE → EXECUTE → STREAM → PERSIST, each independent
- **Sandbox is a tool** — agents run code directly, not just through generators
- **Single required dependency** — only `httpx`; optional deps behind import guards
- **Session isolation** — per-session tools with automatic cleanup
- **Structured events** — typed event objects instead of string tag parsing

## License

MIT
