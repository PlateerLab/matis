# Mantis

[![Python](https://img.shields.io/pypi/pyversions/mantis)](https://pypi.org/project/mantis/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Execution engine for AI agents.**

Runs the Think → Act → Observe loop as a library. Compose tool search, code sandbox, approval workflows, and state checkpointing — with only `httpx` as a required dependency.

## The Problem

AI agent execution code tends to collapse into a single function with no structure:

```python
# A real production file — 694 lines, one function, 9 concerns:
async def execute(workflow_data, user_input, ...):
    load_workflow()          # DB
    apply_file_selection()   # preprocessing
    apply_bypass()           # preprocessing
    create_executor()        # LangChain black box
    async for chunk in stream:
        parse_agent_event()  # regex on "[AGENT_EVENT]" tags
        parse_agent_status() # regex on "[AGENT_STATUS]" tags
        sanitize_io()        # postprocessing
    save_to_db()             # persistence
    update_redis()           # state
    flush_trace()            # observability
```

Without structural phases, extending means wedging code between if-elif branches.

| Problem | Status quo |
|---------|-----------|
| LangChain lock-in | Agent execution is a black box — can't detach |
| All 30 tools sent to LLM | Token waste + accuracy drop |
| No approval workflow | Dangerous actions (DELETE, send_email) run unchecked |
| No sandbox | Can't isolate code execution |
| No failure recovery | Error = restart from scratch |
| Can't debug | 5 layers of callbacks — stack traces are useless |

**Mantis replaces all of this with a 5-phase pipeline.**

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
registry.register(lookup_order)

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

## Standalone Modules

Every module works independently — use only what you need:

```python
# Tool search only
from mantis.search.graph_search import GraphToolManager
manager = GraphToolManager()
manager.ingest_from_registry(my_registry)
results = manager.retrieve("order lookup", max_results=5)

# Sandbox only
from mantis.sandbox.sandbox import DockerSandbox
sandbox = DockerSandbox()
result = await sandbox.execute("print(sum(range(1, 101)))")

# Approval only
from mantis.safety.approval import ApprovalManager
approval = ApprovalManager(patterns=["DELETE *", "send_email *"])
approval.requires_approval("DELETE", {"table": "users"})  # True
```

## Architecture

Mantis structures execution into 5 sequential phases:

```
PREPARE  →  RESOLVE  →  EXECUTE  →  STREAM  →  PERSIST
```

| Phase | Role | What it does |
|-------|------|-------------|
| **PREPARE** | Setup | Session init, DAG sort, context assembly |
| **RESOLVE** | Decide | Tool verification, graph search, RAG, memory load |
| **EXECUTE** | Run | Think→Act→Observe loop, approval, name correction, checkpoint |
| **STREAM** | Deliver | Execution events → SSE / JSON / workflow format |
| **PERSIST** | Save | DB write, trace flush, state update (runs even on error) |

Each phase is independent — add graph search to RESOLVE or approval to EXECUTE without touching other phases.

```python
from mantis.pipeline import build_pipeline

pipeline = build_pipeline(
    model_client=client,
    tool_registry=registry,
    search=graph_manager,       # optional
    sandbox=sandbox,            # optional
)

async for event in pipeline.run(request):
    yield event.to_sse()
```

## Package Structure

```
mantis/
├── __init__.py             # Public API: Agent, tool, ToolSpec, ToolRegistry
├── __main__.py             # CLI: python -m mantis
├── engine/
│   └── runner.py           # Agent — Think→Act→Observe master loop
├── tools/
│   ├── decorator.py        # @tool decorator + ToolSpec
│   └── registry.py         # ToolRegistry — register, discover, execute
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
│   └── runner.py           # Built-in sandbox tools for agents
├── generate/
│   └── tool_generator.py   # AI tool generation → test → register
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
| `Agent` | Think→Act→Observe execution loop with streaming |
| `@tool` | Decorator that auto-generates OpenAI function calling schema |
| `ToolRegistry` | Register, discover, execute tools — load from modules, files, or directories |
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
| `StateStore` | `mantis[state]` | PostgreSQL checkpointing and failure recovery |
| `ToolGenerator` | needs sandbox | AI generates code → tests in sandbox → auto-registers |
| `ToolTester` | sandbox optional | Tool quality gate — smoke test, assert, pytest |

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

- **Phase-based pipeline** — PREPARE → RESOLVE → EXECUTE → STREAM → PERSIST, each phase independent
- **Single required dependency** — only `httpx`; optional deps isolated behind import guards
- **Protocol-based interfaces** — swap LLM providers, trace exporters, stream adapters
- **Modular composition** — use search, sandbox, or approval standalone
- **Structured events** — typed event objects instead of string tag parsing

## License

MIT
