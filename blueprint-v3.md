# Mantis v3 — 설계 청사진

## 한줄 요약

> **엔진은 Generator + Executor 두 개뿐. 나머지는 전부 도구다.**
> 워크플로우 생성/실행도 도구. 검색도 도구. 샌드박스도 도구.
> Agent의 Think→Act→Observe 루프 하나가 곧 파이프라인이다.

---

## 0. v2까지의 구조 (회고)

### v1: Phase 파이프라인

```
PREPARE → RESOLVE → EXECUTE → STREAM → PERSIST
  5개 Phase 클래스, 각각 별도 코드
  워크플로우는 Phase 파이프라인 위에 별도 계층
```

### v2: Live Registry + Sandbox/ToolGen 도구화

```
ToolRegistry (공유)
  │
  ├─ Executor (Agent 루프)
  ├─ ToolGenerator (도구 생성)
  ├─ WorkflowEngine (캔버스 실행)   ← 별도 엔진
  └─ GraphToolSearch                 ← 별도 모듈
```

### v2의 남은 문제

| 문제 | 상태 |
|------|------|
| WorkflowEngine이 별도 엔진 | Agent와 독립적으로 존재 → 중복 실행 로직 |
| Phase 파이프라인이 Agent와 분리 | Agent.run()이 이미 전부 하는데 Phase 클래스가 또 있음 |
| GraphToolSearch가 내부 모듈 | Agent만 쓸 수 있고, Agent가 "도구 검색해줘"라고 쓸 수 없음 |
| 기능 추가 = 모듈 추가 | 새 기능마다 모듈 + Phase 연동 + Agent 연동 필요 |

**근본 원인: "도구로 만들 수 있는 것을 엔진으로 만들었다."**

---

## 1. v3 핵심 철학 — Everything is a Tool

### 원칙

```
도구로 만들 수 있으면 → 도구로 만든다.
도구로 만들 수 없는 것만 → 엔진이다.
```

### 도구로 만들 수 없는 것 = 엔진

```
1. Executor (Agent)
   → Think→Act→Observe 루프 자체는 도구가 아니다.
   → 도구를 "호출하는 주체"이므로 도구가 될 수 없다.

2. Generator (ToolGenerator)
   → 도구를 "만드는 주체"이므로 엔진이다.
   → 단, Agent가 호출하는 인터페이스는 create_tool 도구로 노출.

3. ToolRegistry
   → 도구를 "보관하는 저장소"이므로 엔진의 일부.
```

### 나머지 전부 = 도구

```
v2에서 이미 도구화:
  ✅ execute_code          (Sandbox → 도구)
  ✅ execute_code_with_test (Sandbox → 도구)
  ✅ create_tool            (ToolGenerator → 도구)

v3에서 새로 도구화:
  🆕 generate_workflow      (LLM이 도구 목록 보고 워크플로우 자동 설계)
  🆕 create_workflow        (수동으로 워크플로우 정의)
  🆕 run_workflow           (워크플로우 실행)
  🆕 search_tools           (GraphToolSearch → 도구)
  🆕 list_tools             (ToolRegistry 조회 → 도구)
  🆕 manage_session         (StateStore → 도구)
```

### 한 문장 정리

```
Mantis v3 = Agent(루프) + Generator 2개(도구/워크플로우) + Registry/Store(저장소) + 도구들
```

---

## 2. 아키텍처 비교

### v2

```
┌─────────────────────────────────────────────────────┐
│                    Mantis v2                         │
│                                                     │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │  Agent    │  │ToolGenerator │  │WorkflowEngine│  │
│  │ (Executor)│  │ (Generator)  │  │ (별도 엔진)  │  │
│  └────┬─────┘  └──────┬───────┘  └──────┬───────┘  │
│       │               │                 │           │
│  ┌────▼───────────────▼─────────────────▼────────┐  │
│  │              ToolRegistry                      │  │
│  └────────────────────┬──────────────────────────┘  │
│                       │                              │
│  ┌────────────────────▼──────────────────────────┐  │
│  │         Phase Pipeline (5개 Phase)             │  │
│  └───────────────────────────────────────────────┘  │
│                                                     │
│  GraphToolSearch    StateStore    TraceCollector     │
│  (별도 모듈)        (별도 모듈)    (별도 모듈)        │
└─────────────────────────────────────────────────────┘
```

### v3

```
┌─────────────────────────────────────────────────────┐
│                    Mantis v3                         │
│                                                     │
│  ┌──────────────────────────────────────────────┐   │
│  │                 ENGINE                        │   │
│  │                                               │   │
│  │  Agent          ToolGenerator    Workflow      │   │
│  │  (Think→Act     (설명→코드      Generator     │   │
│  │   →Observe)      →검증→등록)    (설명→WF      │   │
│  │       │              │           →검증→저장)   │   │
│  │  ┌────▼──────────────▼───────────────▼────┐   │   │
│  │  │     ToolRegistry     WorkflowStore      │   │   │
│  │  └────────────────────────────────────────┘   │   │
│  └──────────────────────────────────────────────┘   │
│                          │                           │
│                          │ 전부 도구                  │
│                          ▼                           │
│  ┌──────────────────────────────────────────────┐   │
│  │                 TOOLS                         │   │
│  │                                               │   │
│  │  execute_code         create_tool             │   │
│  │  execute_code_with_   search_tools            │   │
│  │    test               list_tools              │   │
│  │  create_workflow      run_workflow            │   │
│  │  manage_session       ... (사용자 도구)        │   │
│  └──────────────────────────────────────────────┘   │
│                                                     │
│  ┌──────────────────────────────────────────────┐   │
│  │              MIDDLEWARE (횡단 관심사)           │   │
│  │  Approval · Trace · ToolValidation            │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

---

## 3. Phase 파이프라인 → Agent 루프로 흡수

### v2: Phase가 Agent와 별도로 존재

```
PreparePhase     → 세션 초기화, 워크플로우 파싱
ResolvePhase     → 도구 검색, 스키마 결정
ExecutePhase     → Think→Act→Observe 루프
StreamPhase      → 이벤트 변환
PersistPhase     → DB 저장, Trace flush
```

### v3: Agent 루프가 곧 파이프라인

```
Agent.run():
  1. 세션 초기화                   (기존 PREPARE → Agent 내부)
  2. 매 iteration:
     a. 도구 스키마 조회            (기존 RESOLVE → registry.to_openai_tools())
     b. LLM 호출 (Think)           (기존 EXECUTE)
     c. 도구 실행 (Act)            (기존 EXECUTE)
     d. 결과 피드백 (Observe)      (기존 EXECUTE)
     e. 이벤트 yield               (기존 STREAM → yield 그대로)
  3. 세션 상태 저장                 (기존 PERSIST → 미들웨어)
```

**Phase 클래스 5개 → Agent.run() 하나로 통합.**
Phase 개념 자체를 버리는 게 아니라, Agent 루프 안의 단계(step)로 자연스럽게 녹인다.

### 왜 괜찮은가

```
v1 Phase의 존재 이유:
  "한 함수 안에 9가지 관심사가 뒤섞여 있으니 분리하자"

v3에서 이미 해결된 이유:
  - 워크플로우 로드/DAG 정렬 → create_workflow / run_workflow 도구가 처리
  - 도구 검색/검증           → search_tools 도구 + 미들웨어가 처리
  - 코드 실행/격리           → execute_code 도구가 처리
  - SSE 변환                 → Agent.run_stream()이 직접 yield
  - DB 저장                  → manage_session 도구 + 미들웨어가 처리

→ Agent 루프에 9가지 관심사가 다시 모이는 게 아니라,
  각 관심사가 "도구"로 Agent 바깥에 존재하고,
  Agent는 LLM 판단에 따라 호출만 한다.
```

---

## 4. 워크플로우 도구화

### v2 WorkflowEngine (별도 엔진)

```python
# 별도 클래스, Agent와 독립
engine = WorkflowEngine.from_canvas(workflow_data, registry)
async for event in engine.run({"text": user_input}):
    yield event
```

문제:
- Agent가 워크플로우를 만들거나 실행할 수 없음
- 워크플로우 실행 로직이 Agent 루프와 중복 (둘 다 노드 순회 + 도구 호출)
- 캔버스 JSON 파싱이 엔진 레벨에 박혀 있음

### v3: 워크플로우 = 도구 3개

```python
@tool(name="create_workflow")
async def create_workflow(
    name: str,
    description: str,
    steps: list[dict],
) -> dict:
    """워크플로우를 정의한다.

    steps 예시:
    [
        {"id": "분석", "tool": "execute_code", "args": {"code": "..."}},
        {"id": "판단", "type": "condition", "if": "result.confidence > 0.8",
         "then": "리포트", "else": "분석"},
        {"id": "리포트", "tool": "create_report", "args_from": "분석.result"},
    ]
    """
    ...

@tool(name="run_workflow")
async def run_workflow(
    workflow_name: str,
    input_data: dict,
) -> dict:
    """저장된 워크플로우를 실행한다."""
    ...

@tool(name="list_workflows")
async def list_workflows() -> dict:
    """등록된 워크플로우 목록을 반환한다."""
    ...
```

### 동작 시나리오 — generate_workflow (AI 자동 설계)

```
사용자: "매출 데이터 분석해서 리포트 만들어줘"

Agent Think (iteration 1):
  "복잡한 작업이다. 워크플로우를 자동 생성하자."

Agent Act:
  generate_workflow(
    description="매출 CSV 데이터를 분석하고, 신뢰도가 높으면 리포트 생성, 낮으면 재분석",
  )

내부 동작 (generate_workflow 도구 내부):
  1. registry에서 사용 가능한 도구 목록 조회
     → [execute_code, create_tool, lookup_order, ...]
  2. LLM에게 전용 프롬프트 전달:
     "사용 가능한 도구: [...]
      요청: 매출 CSV 데이터를 분석하고, 신뢰도가 높으면 리포트 생성, 낮으면 재분석
      → 워크플로우 JSON으로 설계해줘"
  3. LLM 응답 → WorkflowDef 파싱 → WorkflowStore에 저장

Agent Observe:
  → {"status": "created", "name": "sales_analysis", "step_count": 4,
     "steps_summary": ["데이터 로드 (execute_code)", "분석 (execute_code)",
                       "신뢰도 판단 (condition)", "리포트 생성 (execute_code)"]}

Agent Think (iteration 2):
  "워크플로우가 만들어졌다. 실행하자."

Agent Act:
  run_workflow(workflow_name="sales_analysis", input_data={"file": "sales.csv"})

Agent Observe:
  → 워크플로우 실행 결과 수신
  → "분석 결과: 매출 15% 증가, 리포트를 생성했습니다."
```

### 동작 시나리오 — create_workflow (수동 정의)

```
Agent가 직접 step을 설계해야 할 때 (세밀한 제어가 필요한 경우):

Agent Act:
  create_workflow(
    name="sales_analysis",
    description="매출 분석 파이프라인",
    steps=[
      {"id": "분석", "tool": "execute_code", "args": {"code": "import pandas as pd\n..."}},
      {"id": "판단", "type": "condition", "condition": "result['confidence'] > 0.8",
       "then_step": "리포트", "else_step": "재분석"},
      {"id": "재분석", "tool": "execute_code", "args_from": "분석"},
      {"id": "리포트", "tool": "execute_code", "args": {"code": "def create_report(data):\n..."}},
    ]
  )
```

### generate_workflow vs create_workflow 역할 분담

```
generate_workflow:
  입력: 자연어 설명 ("매출 분석해서 리포트 만들어줘")
  내부: LLM이 도구 목록을 보고 워크플로우 구조를 자동 설계
  출력: 완성된 WorkflowDef가 Store에 저장됨
  → 사용자가 "~해줘"라고 하면 Agent가 이걸 호출
  → ToolGenerator가 도구를 만드는 것처럼, WorkflowGenerator가 워크플로우를 만듦

create_workflow:
  입력: 구체적인 step 정의 (JSON)
  내부: 검증 후 그대로 Store에 저장
  출력: WorkflowDef 저장됨
  → Agent가 이미 설계를 마친 상태에서 직접 정의할 때
  → 또는 캔버스 어댑터가 캔버스 JSON을 변환해서 호출할 때
```

### 캔버스 호환

캔버스 JSON → create_workflow 도구 호출로 변환하는 어댑터만 있으면 된다.

```python
# xgen-workflow 이식 시
def canvas_to_workflow_call(workflow_data: dict) -> dict:
    """캔버스 JSON을 create_workflow 파라미터로 변환."""
    steps = []
    for node in workflow_data["nodes"]:
        if node["type"] == "agent":
            steps.append({"id": node["id"], "type": "agent",
                          "prompt": node["params"]["prompt"]})
        elif node["type"] == "router":
            steps.append({"id": node["id"], "type": "condition",
                          "conditions": node["params"]["conditions"]})
        elif node["type"] == "api_tool":
            steps.append({"id": node["id"], "tool": node["params"]["tool_name"],
                          "args": node["params"]})
    return {"name": "canvas_workflow", "steps": steps,
            "edges": workflow_data["edges"]}
```

---

## 5. 검색/조회 도구화

### v2: GraphToolSearch는 Agent 내부 모듈

```python
# Agent.__init__에 주입
agent = Agent(graph_tool_manager=GraphToolManager())
# Agent._resolve_tools_schema() 내부에서만 사용
```

### v3: 검색 = 도구 + 내부 최적화 이중 경로

```
경로 1 — 자동 (미들웨어, Agent 내부):
  도구 수 > 임계값이면 자동으로 graph 검색 적용
  LLM에 전달되는 도구 목록을 자동 필터링
  → v2와 동일, Agent 사용자는 신경 안 써도 됨

경로 2 — 명시적 (도구):
  Agent가 "어떤 도구가 있는지 찾아보자"라고 판단하면
  search_tools, list_tools 도구를 호출
  → Agent가 도구 생태계를 스스로 탐색할 수 있음
```

```python
def make_registry_tools(registry: ToolRegistry) -> list[ToolSpec]:
    """ToolRegistry를 Agent가 조회/검색할 수 있는 도구로 변환."""

    @tool(
        name="search_tools",
        description="등록된 도구 중 쿼리와 관련된 도구를 검색한다. "
                    "어떤 도구를 써야 할지 모를 때 사용.",
        parameters={
            "query": {"type": "string", "description": "검색 쿼리 (자연어)"},
            "top_k": {"type": "integer", "description": "반환할 최대 도구 수 (기본 5)",
                      "optional": True},
        },
    )
    async def search_tools(query: str, top_k: int = 5) -> dict:
        ...

    @tool(
        name="list_tools",
        description="현재 사용 가능한 모든 도구의 이름과 설명을 반환한다.",
        parameters={},
    )
    async def list_tools() -> dict:
        tools = registry.list_tools()
        return {
            "count": len(tools),
            "tools": [{"name": t.name, "description": t.description} for t in tools],
        }

    return [search_tools._tool_spec, list_tools._tool_spec]
```

---

## 6. 엔진 구조 상세

### Agent (Executor) — 유일한 실행 루프

```python
class Agent:
    """v3: 순수한 Think→Act→Observe 루프.

    미들웨어 체인으로 횡단 관심사를 처리.
    도구 스키마는 매 iteration마다 ToolRegistry에서 조회.

    v2 대비 변경:
    - graph_tool_manager 파라미터 제거 → 미들웨어 또는 도구
    - state_store 파라미터 제거 → 미들웨어
    - approval_patterns 파라미터 유지 (미들웨어이므로)
    - middlewares 파라미터 추가
    """

    def __init__(
        self,
        name: str,
        model_client: ModelClient,
        tool_registry: ToolRegistry,
        system_prompt: str = "",
        middlewares: list[Middleware] | None = None,
    ):
        self.name = name
        self.model_client = model_client
        self.tool_registry = tool_registry
        self.system_prompt = system_prompt
        self.middlewares = middlewares or []

    async def run(self, user_input: str, session_id: str | None = None) -> str:
        session_id = session_id or str(uuid.uuid4())
        ctx = RunContext(session_id=session_id, agent=self)

        # 미들웨어: on_start
        for mw in self.middlewares:
            await mw.on_start(ctx)

        conversation = ConversationContext(system_prompt=self.system_prompt)
        conversation.add_user(user_input)

        for iteration in range(MAX_ITERATIONS):
            # 매 iteration: 최신 도구 조회
            tools_schema = self.tool_registry.to_openai_tools(session_id=session_id)

            # 미들웨어: on_before_llm (도구 필터링 등)
            for mw in self.middlewares:
                tools_schema = await mw.on_before_llm(ctx, tools_schema)

            # Think
            response = await self.model_client.generate(
                messages=conversation.to_messages(),
                tools=tools_schema or None,
            )

            if not response.has_tool_calls:
                conversation.add_assistant(content=response.text)
                # 미들웨어: on_end
                for mw in self.middlewares:
                    await mw.on_end(ctx, response.text)
                return response.text or ""

            # Act
            conversation.add_assistant(
                content=response.text,
                tool_calls=[tc.to_dict() for tc in response.tool_calls],
            )

            for tc in response.tool_calls:
                # 미들웨어: on_before_tool (승인, 교정 등)
                tc = await self._apply_before_tool(ctx, tc)
                if tc.blocked:
                    conversation.add_tool_result(tc.id, tc.name, tc.block_reason)
                    continue

                # 도구 실행
                result = await self.tool_registry.execute(
                    {"name": tc.name, "arguments": tc.arguments},
                    session_id=session_id,
                )

                result_str = json.dumps(
                    result.get("result", result.get("error", "")),
                    ensure_ascii=False, default=str,
                )
                conversation.add_tool_result(tc.id, tc.name, result_str)

                # 미들웨어: on_after_tool (트레이싱 등)
                for mw in self.middlewares:
                    await mw.on_after_tool(ctx, tc, result)

        return "[오류] 최대 실행 횟수를 초과했습니다."
```

### ToolGenerator (Generator) — 유일한 생성기

v2와 동일. Agent가 `create_tool` 도구를 통해 호출.

```
ToolGenerator의 역할:
  1. LLM에게 코드 생성 요청
  2. Sandbox에서 검증 (문법 + 기능 테스트)
  3. ToolRegistry에 등록
  → 다음 iteration에서 Agent가 즉시 사용

ToolGenerator는 엔진이지만 Agent가 호출하는 인터페이스는 create_tool 도구.
이 구조는 v2에서 이미 완성됨. v3에서 변경 없음.
```

### ToolRegistry — 모든 것의 허브

v2와 동일. 세션 스코프 + 소스 추적.

---

## 7. 미들웨어 — 횡단 관심사

### 왜 미들웨어인가

```
도구: Agent가 "선택"해서 호출하는 것 (LLM이 판단)
미들웨어: 매 실행마다 "자동으로" 끼어드는 것 (무조건 실행)

승인 → 미들웨어 (Agent가 "승인 확인하자"고 선택하면 안 됨. 자동이어야 함)
트레이싱 → 미들웨어 (Agent가 "트레이스 남기자"고 선택하면 안 됨. 자동이어야 함)
도구 필터링 → 미들웨어 (도구 수가 많을 때 자동 적용)
상태 저장 → 미들웨어 (매 iteration 자동 체크포인트)

검색 → 도구 (Agent가 "어떤 도구가 있나 찾아보자"고 판단)
코드 실행 → 도구 (Agent가 "이 코드 돌려보자"고 판단)
워크플로우 → 도구 (Agent가 "이건 워크플로우로 만들자"고 판단)
```

### Middleware Protocol

```python
class Middleware(Protocol):
    """Agent 루프의 각 단계에 끼어드는 횡단 관심사."""

    async def on_start(self, ctx: RunContext) -> None:
        """실행 시작 시. 세션 복구 등."""
        ...

    async def on_before_llm(self, ctx: RunContext, tools: list[dict]) -> list[dict]:
        """LLM 호출 전. 도구 필터링, 스키마 수정 등.
        수정된 tools를 반환."""
        return tools

    async def on_before_tool(self, ctx: RunContext, tc: ToolCall) -> ToolCall:
        """도구 호출 전. 승인 체크, 이름 교정 등.
        tc.blocked = True로 차단 가능."""
        return tc

    async def on_after_tool(self, ctx: RunContext, tc: ToolCall, result: dict) -> None:
        """도구 호출 후. 트레이싱, 이력 기록 등."""
        ...

    async def on_end(self, ctx: RunContext, output: str) -> None:
        """실행 종료 시. 상태 저장, Trace flush 등."""
        ...
```

### 기본 미들웨어

```python
class ApprovalMiddleware:
    """위험 도구 호출 시 승인 요청."""

    def __init__(self, patterns: list[str]):
        self.manager = ApprovalManager(patterns=patterns)

    async def on_before_tool(self, ctx, tc):
        if self.manager.requires_approval(tc.name, tc.arguments):
            approval = await self.manager.request_and_wait(
                session_id=ctx.session_id,
                tool_name=tc.name,
                arguments=tc.arguments,
            )
            if not approval.approved:
                tc.blocked = True
                tc.block_reason = f"승인 거절: {approval.reason}"
        return tc


class TraceMiddleware:
    """실행 흐름 자동 기록."""

    def __init__(self, collector: TraceCollector | None = None):
        self.trace = collector or TraceCollector()

    async def on_start(self, ctx):
        ctx.trace_id = self.trace.start_trace(
            session_id=ctx.session_id, agent_name=ctx.agent.name,
        )

    async def on_after_tool(self, ctx, tc, result):
        self.trace.add_step(ctx.trace_id, StepType.TOOL_CALL, {
            "tool": tc.name, "result": result,
        })

    async def on_end(self, ctx, output):
        self.trace.end_trace(ctx.trace_id)


class GraphSearchMiddleware:
    """도구 수가 많을 때 자동으로 graph-tool-call 검색 적용."""

    def __init__(self, graph_manager: GraphToolManager, threshold: int = 15):
        self.graph = graph_manager
        self.threshold = threshold

    async def on_before_llm(self, ctx, tools):
        if len(tools) < self.threshold:
            return tools
        # 최근 사용자 메시지로 검색
        query = ctx.last_user_message
        filtered = self.graph.retrieve_as_openai_tools(query)
        return filtered if filtered else tools


class AutoCorrectMiddleware:
    """도구 이름 오타 자동 교정."""

    def __init__(self, graph_manager: GraphToolManager):
        self.graph = graph_manager

    async def on_before_tool(self, ctx, tc):
        validation = self.graph.validate_call(tc.name, tc.arguments)
        if validation.get("corrected_name"):
            tc.name = validation["corrected_name"]
        if validation.get("corrected_arguments"):
            tc.arguments = validation["corrected_arguments"]
        return tc


class StateMiddleware:
    """세션 상태 자동 저장/복구."""

    def __init__(self, store: StateStore):
        self.store = store

    async def on_start(self, ctx):
        state = await self.store.resume(ctx.session_id)
        if state:
            ctx.restore_conversation(state)

    async def on_after_tool(self, ctx, tc, result):
        await self.store.checkpoint(ctx.session_id, ctx.conversation_state())

    async def on_end(self, ctx, output):
        await self.store.checkpoint(ctx.session_id, ctx.conversation_state())
```

---

## 8. 워크플로우 도구 상세 설계

### WorkflowStore — 워크플로우 저장소

```python
class WorkflowStore:
    """워크플로우 정의를 저장/조회하는 저장소.

    인메모리 (기본) 또는 DB 백엔드.
    워크플로우 = step 리스트 + edge 정의.
    """

    _workflows: dict[str, WorkflowDef]

    def save(self, name: str, definition: WorkflowDef) -> None: ...
    def get(self, name: str) -> WorkflowDef | None: ...
    def list_all(self) -> list[WorkflowSummary]: ...
    def delete(self, name: str) -> bool: ...
```

### WorkflowDef — 워크플로우 정의

```python
@dataclass
class WorkflowDef:
    """워크플로우 정의. 도구 호출 시퀀스 + 조건 분기."""

    name: str
    description: str
    steps: list[WorkflowStep]
    edges: list[WorkflowEdge] | None = None  # None이면 steps 순서대로

@dataclass
class WorkflowStep:
    """워크플로우의 한 단계."""

    id: str
    type: str = "tool"           # "tool" | "condition" | "agent" | "parallel"

    # type="tool"
    tool: str | None = None      # 호출할 도구 이름
    args: dict | None = None     # 고정 인자
    args_from: str | None = None # 이전 step의 결과를 인자로 ("step_id.key")

    # type="condition"
    condition: str | None = None # 조건 표현식
    then_step: str | None = None # 참일 때 다음 step id
    else_step: str | None = None # 거짓일 때 다음 step id

    # type="agent"
    prompt: str | None = None    # Agent에게 위임할 프롬프트
    tools: list[str] | None = None  # 이 step에서 사용할 도구 제한

    # type="parallel"
    parallel_steps: list[str] | None = None  # 병렬 실행할 step id들

@dataclass
class WorkflowEdge:
    """step 간 데이터 흐름."""
    source_step: str
    source_key: str
    target_step: str
    target_key: str
```

### 워크플로우 실행기

```python
class WorkflowRunner:
    """워크플로우 정의를 실행하는 내부 엔진.

    Agent가 run_workflow 도구를 호출하면 이 클래스가 실행.
    각 step을 순서대로 실행하고, 조건 분기/병렬 처리를 처리.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        model_client: ModelClient | None = None,
    ):
        self.registry = registry
        self.model_client = model_client

    async def run(
        self,
        workflow: WorkflowDef,
        input_data: dict,
        session_id: str | None = None,
    ) -> dict:
        step_results: dict[str, Any] = {"input": input_data}
        execution_order = self._resolve_order(workflow)

        for step_id in execution_order:
            step = self._get_step(workflow, step_id)

            if step.type == "tool":
                # 도구 직접 호출
                args = self._resolve_args(step, step_results)
                result = await self.registry.execute(
                    {"name": step.tool, "arguments": args},
                    session_id=session_id,
                )
                step_results[step_id] = result.get("result", result)

            elif step.type == "condition":
                # 조건 분기 — 이전 결과를 기반으로 판단
                condition_met = self._evaluate_condition(
                    step.condition, step_results,
                )
                next_step = step.then_step if condition_met else step.else_step
                execution_order = self._reorder_from(execution_order, next_step)

            elif step.type == "agent":
                # Sub-Agent 위임
                sub_agent = Agent(
                    name=f"workflow_{step.id}",
                    model_client=self.model_client,
                    tool_registry=self.registry,
                    system_prompt=step.prompt or "",
                )
                # 도구 제한
                context = self._build_agent_input(step, step_results)
                result = await sub_agent.run(context, session_id=session_id)
                step_results[step_id] = result

            elif step.type == "parallel":
                # 병렬 실행
                tasks = [
                    self._run_step(self._get_step(workflow, sid), step_results, session_id)
                    for sid in step.parallel_steps
                ]
                results = await asyncio.gather(*tasks)
                for sid, res in zip(step.parallel_steps, results):
                    step_results[sid] = res

        return step_results
```

### WorkflowGenerator — 워크플로우 자동 설계기

```python
class WorkflowGenerator:
    """자연어 설명 → 워크플로우 자동 설계.

    ToolGenerator가 "코드 → 도구"를 만드는 것처럼,
    WorkflowGenerator는 "설명 → 워크플로우"를 만든다.

    내부 동작:
    1. ToolRegistry에서 사용 가능한 도구 목록 조회
    2. LLM에게 도구 목록 + 요청을 전달
    3. LLM이 워크플로우 JSON 설계
    4. 파싱 → 검증 → WorkflowStore에 저장
    """

    def __init__(
        self,
        model_client: ModelClient,
        registry: ToolRegistry,
        store: WorkflowStore,
    ):
        self.model_client = model_client
        self.registry = registry
        self.store = store

    async def generate(
        self,
        description: str,
        session_id: str | None = None,
    ) -> WorkflowDef:
        """자연어 설명으로 워크플로우 자동 생성.

        Returns:
            생성된 WorkflowDef (이미 Store에 저장된 상태)
        """
        # 1. 사용 가능한 도구 목록 수집
        available_tools = self.registry.list_tools(session_id=session_id)
        tools_desc = "\n".join(
            f"- {t.name}: {t.description} (params: {list(t.parameters.keys())})"
            for t in available_tools
        )

        # 2. LLM에게 워크플로우 설계 요청
        prompt = WORKFLOW_GENERATION_PROMPT.format(
            tools=tools_desc,
            description=description,
        )
        response = await self.model_client.generate(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )

        # 3. JSON 파싱 → WorkflowDef
        workflow_json = self._extract_json(response.text)
        workflow_def = WorkflowDef(
            name=workflow_json["name"],
            description=workflow_json.get("description", description),
            steps=[WorkflowStep(**s) for s in workflow_json["steps"]],
            edges=[WorkflowEdge(**e) for e in workflow_json.get("edges", [])],
        )

        # 4. 검증 — step에서 참조하는 도구가 실제로 존재하는지
        errors = self._validate(workflow_def, available_tools)
        if errors:
            raise WorkflowGenerationError(errors)

        # 5. Store에 저장
        self.store.save(workflow_def.name, workflow_def)
        return workflow_def

    def _validate(self, workflow: WorkflowDef, tools: list[ToolSpec]) -> list[str]:
        """워크플로우 검증 — 존재하지 않는 도구 참조 등."""
        tool_names = {t.name for t in tools}
        errors = []
        for step in workflow.steps:
            if step.type == "tool" and step.tool not in tool_names:
                errors.append(f"step '{step.id}'가 참조하는 도구 '{step.tool}'이 존재하지 않음")
        return errors

    def _extract_json(self, text: str) -> dict:
        """LLM 응답에서 JSON 블록 추출."""
        ...


WORKFLOW_GENERATION_PROMPT = """\
사용 가능한 도구 목록을 참고하여 워크플로우를 설계해줘.

**사용 가능한 도구:**
{tools}

**워크플로우 요청:**
{description}

**규칙:**
1. step의 type은 "tool", "condition", "agent", "parallel" 중 하나
2. type="tool"인 step은 반드시 위 도구 목록에 있는 도구만 참조
3. type="condition"은 이전 step 결과를 기반으로 분기
4. type="agent"는 LLM에게 자유 판단을 위임할 때 사용
5. type="parallel"은 독립적인 step들을 병렬 실행할 때 사용
6. 워크플로우 이름은 snake_case

**출력 형식 (JSON):**
```json
{{
  "name": "workflow_name",
  "description": "워크플로우 설명",
  "steps": [
    {{"id": "step1", "type": "tool", "tool": "도구명", "args": {{}}}},
    {{"id": "step2", "type": "condition", "condition": "조건식",
      "then_step": "step3", "else_step": "step1"}},
    {{"id": "step3", "type": "tool", "tool": "도구명", "args_from": "step1.result"}}
  ]
}}
```
"""
```

### 워크플로우 도구 팩토리

```python
def make_workflow_tools(
    store: WorkflowStore,
    runner: WorkflowRunner,
    generator: WorkflowGenerator,
) -> list[ToolSpec]:
    """워크플로우를 Agent가 호출할 수 있는 도구 4개로 변환."""

    @tool(
        name="generate_workflow",
        description=(
            "자연어 설명으로 워크플로우를 자동 설계한다. "
            "사용 가능한 도구 목록을 참고하여 LLM이 최적의 step 구성을 만든다. "
            "복잡한 작업을 자동으로 분해하고 싶을 때 사용. "
            "생성 후 run_workflow로 바로 실행 가능."
        ),
        parameters={
            "description": {
                "type": "string",
                "description": "만들 워크플로우의 자연어 설명",
            },
        },
    )
    async def generate_workflow_tool(description: str) -> dict:
        try:
            workflow = await generator.generate(description)
            return {
                "status": "created",
                "name": workflow.name,
                "description": workflow.description,
                "step_count": len(workflow.steps),
                "steps_summary": [
                    f"{s.id} ({s.type}: {s.tool or s.prompt or s.condition})"
                    for s in workflow.steps
                ],
            }
        except WorkflowGenerationError as e:
            return {"status": "failed", "errors": e.errors}

    @tool(
        name="create_workflow",
        description=(
            "워크플로우를 직접 정의한다. step 구조를 수동으로 지정할 때 사용. "
            "자동 설계가 필요하면 generate_workflow를 대신 사용."
        ),
        parameters={
            "name": {"type": "string", "description": "워크플로우 이름"},
            "description": {"type": "string", "description": "워크플로우 설명"},
            "steps": {
                "type": "array",
                "items": {"type": "object"},
                "description": (
                    "실행 단계 목록. 각 단계는 "
                    "{id, type, tool, args, args_from, condition, then_step, else_step, "
                    "prompt, parallel_steps} 필드를 가짐."
                ),
            },
            "edges": {
                "type": "array",
                "items": {"type": "object"},
                "description": "step 간 데이터 흐름 (선택, 없으면 순서대로)",
                "optional": True,
            },
        },
    )
    async def create_workflow(
        name: str, description: str, steps: list[dict],
        edges: list[dict] | None = None,
    ) -> dict:
        workflow_def = WorkflowDef(
            name=name,
            description=description,
            steps=[WorkflowStep(**s) for s in steps],
            edges=[WorkflowEdge(**e) for e in edges] if edges else None,
        )
        store.save(name, workflow_def)
        return {"status": "created", "name": name, "step_count": len(steps)}

    @tool(
        name="run_workflow",
        description=(
            "저장된 워크플로우를 실행한다. "
            "input_data로 초기 입력을 전달하면 각 단계가 순서대로 실행된다."
        ),
        parameters={
            "workflow_name": {"type": "string", "description": "실행할 워크플로우 이름"},
            "input_data": {
                "type": "object",
                "description": "워크플로우 초기 입력 데이터",
                "optional": True,
            },
        },
    )
    async def run_workflow(
        workflow_name: str, input_data: dict | None = None,
    ) -> dict:
        workflow = store.get(workflow_name)
        if not workflow:
            return {"error": f"워크플로우 '{workflow_name}'을 찾을 수 없음"}
        result = await runner.run(workflow, input_data or {})
        return {"status": "completed", "result": result}

    @tool(
        name="list_workflows",
        description="등록된 워크플로우 목록을 반환한다.",
        parameters={},
    )
    async def list_workflows() -> dict:
        workflows = store.list_all()
        return {
            "count": len(workflows),
            "workflows": [{"name": w.name, "description": w.description} for w in workflows],
        }

    return [
        generate_workflow_tool._tool_spec,
        create_workflow._tool_spec,
        run_workflow._tool_spec,
        list_workflows._tool_spec,
    ]
```

---

## 9. 전체 도구 목록

### 엔진이 기본 제공하는 도구 (Built-in)

| 도구 | 팩토리 함수 | 하는 일 |
|------|------------|---------|
| `create_tool` | `make_create_tool(generator)` | AI 도구 생성→검증→등록 |
| `execute_code` | `make_sandbox_tools(sandbox)` | Docker에서 Python 코드 실행 |
| `execute_code_with_test` | `make_sandbox_tools(sandbox)` | 코드+테스트 함께 실행 |
| `generate_workflow` | `make_workflow_tools(store, runner, gen)` | AI 워크플로우 자동 설계 |
| `create_workflow` | `make_workflow_tools(store, runner, gen)` | 워크플로우 수동 정의 |
| `run_workflow` | `make_workflow_tools(store, runner, gen)` | 워크플로우 실행 |
| `list_workflows` | `make_workflow_tools(store, runner, gen)` | 워크플로우 조회 |
| `search_tools` | `make_registry_tools(registry)` | 도구 검색 |
| `list_tools` | `make_registry_tools(registry)` | 전체 도구 목록 |

### 사용자 도구 (@tool)

```python
@tool(name="lookup_order", ...)
async def lookup_order(order_id: str) -> dict: ...

@tool(name="send_slack", ...)
async def send_slack(channel: str, text: str) -> dict: ...
```

### 패턴: make_*_tools() 팩토리

```
모든 도구화된 인프라는 같은 패턴을 따른다:

  인프라 인스턴스 → make_*_tools(instance) → list[ToolSpec] → registry.register()

  DockerSandbox      → make_sandbox_tools()   → [execute_code, execute_code_with_test]
  ToolGenerator      → make_create_tool()     → [create_tool]
  WorkflowGenerator  → make_workflow_tools()  → [generate_workflow, create_workflow,
                                                  run_workflow, list_workflows]
  ToolRegistry       → make_registry_tools()  → [search_tools, list_tools]
```

### Generator 패턴 정리

```
엔진에 Generator가 2개:

  ToolGenerator      — "설명 → 도구 코드 → 검증 → Registry 등록"
  WorkflowGenerator  — "설명 → 워크플로우 JSON → 검증 → Store 저장"

둘 다 같은 패턴:
  1. LLM에게 생성 요청 (전용 프롬프트)
  2. 결과 파싱
  3. 검증 (도구: Sandbox 테스트, 워크플로우: 도구 존재 확인)
  4. 저장 (도구: Registry, 워크플로우: Store)
  5. Agent에겐 도구로 노출 (create_tool, generate_workflow)
```

---

## 10. 사용 이미지

### 가장 간단한 사용법

```python
from mantis import Agent, tool, ToolRegistry
from mantis.llm import ModelClient

@tool(name="greet", description="인사한다",
      parameters={"name": {"type": "string", "description": "이름"}})
async def greet(name: str) -> dict:
    return {"message": f"안녕 {name}"}

registry = ToolRegistry()
registry.register(greet._tool_spec)

agent = Agent(
    name="bot",
    model_client=ModelClient(model="gpt-4o-mini", api_key="sk-..."),
    tool_registry=registry,
)
result = await agent.run("인사해줘")
```

### 풀옵션 (v3)

```python
from mantis import Agent, tool, ToolRegistry
from mantis.llm import ModelClient
from mantis.sandbox import DockerSandbox
from mantis.sandbox.tools import make_sandbox_tools
from mantis.generate import ToolGenerator, make_create_tool
from mantis.workflow import WorkflowStore, WorkflowRunner, make_workflow_tools
from mantis.tools.meta import make_registry_tools
from mantis.middleware import (
    ApprovalMiddleware, TraceMiddleware,
    GraphSearchMiddleware, AutoCorrectMiddleware, StateMiddleware,
)

# ─── 엔진 ───
registry = ToolRegistry()
llm = ModelClient(model="gpt-4o-mini", api_key="sk-...")

# ─── 도구: 샌드박스 ───
sandbox = DockerSandbox()
for spec in make_sandbox_tools(sandbox):
    registry.register(spec, source="sandbox")

# ─── 도구: 도구 생성기 ───
generator = ToolGenerator(model_client=llm, tool_registry=registry)
registry.register(make_create_tool(generator), source="builtin")

# ─── 도구: 워크플로우 ───
wf_store = WorkflowStore()
wf_runner = WorkflowRunner(registry=registry, model_client=llm)
wf_generator = WorkflowGenerator(model_client=llm, registry=registry, store=wf_store)
for spec in make_workflow_tools(wf_store, wf_runner, wf_generator):
    registry.register(spec, source="builtin")

# ─── 도구: 레지스트리 조회 ───
for spec in make_registry_tools(registry):
    registry.register(spec, source="builtin")

# ─── 사용자 도구 ───
@tool(name="lookup_order", description="주문 조회",
      parameters={"order_id": {"type": "string", "description": "주문 ID"}})
async def lookup_order(order_id: str) -> dict:
    return {"order_id": order_id, "status": "shipped"}
registry.register(lookup_order._tool_spec, source="builtin")

# ─── 미들웨어 ───
middlewares = [
    TraceMiddleware(),
    ApprovalMiddleware(patterns=["DELETE *", "send_slack"]),
    GraphSearchMiddleware(graph_manager=GraphToolManager(), threshold=15),
    AutoCorrectMiddleware(graph_manager=GraphToolManager()),
    StateMiddleware(store=StateStore()),
]

# ─── Agent ───
agent = Agent(
    name="full-agent",
    model_client=llm,
    tool_registry=registry,
    system_prompt="너는 만능 비서다.",
    middlewares=middlewares,
)

# 실행 — Agent가 알아서 도구 생성, 코드 실행, 워크플로우 구성
async for event in agent.run_stream("매출 데이터 분석 워크플로우 만들어서 돌려줘"):
    print(event)
```

### v2와의 하위 호환

```python
# v2 코드가 그대로 동작함 — graph_tool_manager, state_store 파라미터는
# deprecated warning 후 내부적으로 미들웨어로 변환
agent = Agent(
    name="bot",
    model_client=llm,
    tool_registry=registry,
    graph_tool_manager=GraphToolManager(),  # → GraphSearchMiddleware로 변환
    state_store=StateStore(),               # → StateMiddleware로 변환
    approval_patterns=["DELETE *"],         # → ApprovalMiddleware로 변환
)
```

---

## 11. v3 패키지 구조

```
mantis/
├── __init__.py                  ← Agent, tool, ToolRegistry
├── __main__.py
│
├── engine/                      ← ★ 핵심 엔진 (이것만 엔진)
│   ├── runner.py                ← Agent (Think→Act→Observe 루프)
│   └── context.py               ← RunContext
│
├── tools/                       ← 도구 시스템
│   ├── decorator.py             ← @tool, ToolSpec
│   ├── registry.py              ← ToolRegistry
│   └── meta.py                  ← ★ v3 신규: make_registry_tools()
│
├── llm/                         ← LLM 추상화
│   ├── protocol.py              ← LLMProvider (Protocol)
│   └── openai_provider.py       ← ModelClient
│
├── middleware/                   ← ★ v3 신규: 횡단 관심사
│   ├── __init__.py              ← Middleware Protocol
│   ├── approval.py              ← ApprovalMiddleware
│   ├── trace.py                 ← TraceMiddleware
│   ├── graph_search.py          ← GraphSearchMiddleware + AutoCorrectMiddleware
│   └── state.py                 ← StateMiddleware
│
├── generate/                    ← 도구 생성기 (엔진)
│   └── tool_generator.py       ← ToolGenerator + make_create_tool()
│
├── sandbox/                     ← 샌드박스 인프라 + 도구
│   ├── sandbox.py               ← DockerSandbox
│   ├── runner.py                ← SandboxRunner
│   └── tools.py                 ← make_sandbox_tools()
│
├── workflow/                    ← ★ v3 신규: 워크플로우 도구
│   ├── __init__.py
│   ├── models.py                ← WorkflowDef, WorkflowStep, WorkflowEdge
│   ├── store.py                 ← WorkflowStore
│   ├── runner.py                ← WorkflowRunner
│   ├── generator.py             ← ★ WorkflowGenerator (LLM 자동 설계)
│   └── tools.py                 ← make_workflow_tools() (generate/create/run/list)
│
├── search/                      ← graph-tool-call (미들웨어가 사용)
│   └── graph_search.py          ← GraphToolManager
│
├── context/                     ← 대화 컨텍스트
│   └── conversation.py          ← ConversationContext
│
├── safety/                      ← 승인 로직 (미들웨어가 사용)
│   └── approval.py              ← ApprovalManager
│
├── state/                       ← 상태 저장 (미들웨어가 사용)
│   └── store.py                 ← StateStore
│
├── trace/                       ← 트레이싱 (미들웨어가 사용)
│   ├── collector.py             ← TraceCollector
│   └── exporter.py              ← TraceExporter
│
├── testing/                     ← 도구 품질 검증
│   ├── tool_tester.py           ← ToolTester
│   ├── dummy_args.py            ← 더미 값 생성
│   └── pytest_runner.py         ← pytest 실행
│
└── adapters/                    ← 이식 레이어
    ├── sse_adapter.py           ← SSE 변환
    └── canvas_adapter.py        ← ★ v3: 캔버스 JSON → create_workflow 호출 변환
```

### v2 대비 변경 요약 (구현 완료)

```
삭제:
  pipeline/pipeline.py          ← ExecutionPipeline 제거 ✅
  pipeline/phases.py            ← 5개 Phase 클래스 제거 ✅
  pipeline/models.py            ← Phase 모델 제거 ✅
  pipeline/__init__.py          ← 패키지 자체 삭제 ✅

신규:
  exceptions.py                 ← MantisError 예외 계층 ✅
  llm/protocol.py               ← LLMProvider Protocol + ModelResponse/ToolCall ✅
  middleware/__init__.py         ← 미들웨어 패키지 ✅
  middleware/base.py             ← Middleware Protocol + RunContext + BaseMiddleware ✅
  middleware/approval.py         ← ApprovalMiddleware ✅
  middleware/trace.py            ← TraceMiddleware ✅
  middleware/graph_search.py     ← GraphSearchMiddleware + AutoCorrectMiddleware ✅
  middleware/state.py            ← StateMiddleware ✅
  workflow/__init__.py           ← 워크플로우 패키지 ✅
  workflow/models.py             ← WorkflowDef, WorkflowStep, StepExecutor Protocol ✅
  workflow/store.py              ← WorkflowStore (인메모리) ✅
  workflow/runner.py             ← WorkflowRunner (tool/condition/agent/parallel) ✅
  workflow/generator.py          ← WorkflowGenerator (LLM 자동 설계) ✅
  workflow/tools.py              ← make_workflow_tools() (4개 도구) ✅
  tools/meta.py                  ← make_registry_tools() (search_tools, list_tools) ✅
  adapters/canvas_adapter.py     ← 캔버스 JSON → WorkflowDef 변환 ✅

변경:
  engine/runner.py               ← Agent: 미들웨어 체인, LLMProvider Protocol, 순수 루프 ✅
  tools/decorator.py             ← 전역 _tool_registry 제거 ✅
  tools/registry.py              ← 모듈 스캔 방식 전환 (_collect_tools_from_module) ✅
  generate/tool_generator.py     ← ModelClient → LLMProvider 전환 ✅
  llm/openai_provider.py         ← ModelResponse/ToolCall을 protocol.py에서 import ✅
  __init__.py (전체)              ← 공개 API export 정비, 버전 0.3.0 ✅

유지 (미들웨어가 래핑):
  safety/approval.py             ← ApprovalManager 그대로, ApprovalMiddleware가 사용
  search/graph_search.py         ← GraphToolManager 그대로, GraphSearchMiddleware가 사용
  state/store.py                 ← StateStore 그대로, StateMiddleware가 사용
  trace/collector.py             ← TraceCollector 그대로, TraceMiddleware가 사용
```

---

## 12. v1 → v2 → v3 비교

| | v1 | v2 | v3 |
|---|---|---|---|
| **구조** | 5 Phase 파이프라인 | Phase + Live Registry | Agent 루프 + 도구 + 미들웨어 |
| **워크플로우** | Phase에서 처리 | WorkflowEngine (별도) | 도구 (create/run_workflow) |
| **샌드박스** | 인프라 | 도구 | 도구 (v2 동일) |
| **도구 생성** | 없음 | 도구 (create_tool) | 도구 (v2 동일) |
| **도구 검색** | 없음 | Agent 내부 모듈 | 미들웨어 + 도구 이중 경로 |
| **승인** | 없음 | Agent 파라미터 | 미들웨어 |
| **트레이싱** | 없음 | Agent 내부 | 미들웨어 |
| **상태 저장** | 없음 | Agent 파라미터 | 미들웨어 |
| **확장** | Phase에 코드 추가 | 모듈 + Phase 연동 | 도구 만들면 끝 |
| **Agent 복잡도** | Phase와 분리 | 파라미터 10개+ | 3개 (llm, registry, middlewares) |

---

## 13. 실행 계획

### Phase A: 미들웨어 시스템 ✅ 완료

- `mantis/middleware/` 패키지 생성 — base, approval, trace, graph_search, state
- Middleware Protocol + BaseMiddleware 정의
- 기존 Agent 내부 로직을 미들웨어로 추출
- Agent.run()에 미들웨어 체인 적용 (on_start → on_before_llm → on_before_tool → on_after_tool → on_end)
- 기존 파라미터 (graph_tool_manager, state_store 등) → deprecated, 내부 자동 변환

### Phase B: 워크플로우 도구화 ✅ 완료

- `mantis/workflow/` 패키지 생성 — models, store, runner, generator, tools
- WorkflowDef, WorkflowStep, WorkflowEdge 모델 + StepExecutor Protocol
- WorkflowStore (인메모리), WorkflowRunner (tool/condition/agent/parallel step 지원)
- WorkflowGenerator (LLM으로 워크플로우 자동 설계, 도구 존재 검증)
- make_workflow_tools() → generate_workflow, create_workflow, run_workflow, list_workflows

### Phase C: 레지스트리 도구화 ✅ 완료

- `mantis/tools/meta.py` 생성
- make_registry_tools() → search_tools (graph 시맨틱 + 키워드 폴백), list_tools
- GraphToolManager 연동 (선택, 없으면 키워드 매칭)

### Phase D: 기반 정비 + 파이프라인 제거 ✅ 완료

- `mantis/llm/protocol.py` — LLMProvider Protocol, ModelResponse/ToolCall 분리
- `mantis/exceptions.py` — MantisError 예외 계층
- `decorator.py` 전역 상태 제거 (_tool_registry → _tool_spec 부착만)
- `registry.py` 모듈 스캔 방식으로 전환 (get_registered_tools 제거)
- `generate/tool_generator.py` ModelClient → LLMProvider 전환
- `mantis/pipeline/` 패키지 삭제
- `adapters/canvas_adapter.py` 추가 (캔버스 JSON → WorkflowDef 변환)
- 전체 `__init__.py` 공개 API 정비 (버전 0.3.0)

### Phase E: xgen-workflow 이식 (미진행)

- canvas_adapter로 캔버스 JSON 변환
- execution_core.py → Agent + 미들웨어로 교체
- agent_core.py → 삭제 (Agent가 대체)
- async_workflow_executor.py → run_workflow 도구가 대체

---

## 14. 라이브러리화 검수

v3 설계를 `pip install mantis`로 쓰는 외부 개발자 관점에서 검수한 결과.

### 문제 1: LLMProvider Protocol이 없다

```
현재:
  ModelClient (구체 클래스, OpenAI 호환만)
  → ToolGenerator(model_client=ModelClient(...))
  → WorkflowGenerator(model_client=ModelClient(...))
  → Agent(model_client=ModelClient(...))

문제:
  라이브러리 사용자가 자체 LLM 서버, Claude API, Ollama 등을 쓰려면?
  ModelClient를 직접 상속해야 함 → 강결합

해결:
  LLMProvider Protocol을 정의하고, ModelClient는 그 구현체 중 하나로.
```

```python
# mantis/llm/protocol.py
class LLMProvider(Protocol):
    """LLM 호출 인터페이스. 라이브러리 사용자가 자기 구현을 끼울 수 있음."""

    async def generate(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
    ) -> ModelResponse: ...

# ModelClient는 LLMProvider의 기본 구현
class ModelClient(LLMProvider):
    """OpenAI 호환 API 클라이언트. 기본 제공."""
    ...

# 사용자가 자기 LLM 연결
class MyLLM:
    async def generate(self, messages, tools=None, temperature=0.7):
        # 자체 LLM 서버 호출
        return ModelResponse(text="...", tool_calls=[])

agent = Agent(model_client=MyLLM())  # Protocol 기반이라 아무 구현이나 가능
```

### 문제 2: 전역 상태 오염 (_tool_registry)

```
현재 (decorator.py):
  _tool_registry: list[ToolSpec] = []   ← 모듈 레벨 전역

  @tool(name="greet", ...)
  async def greet(): ...
  → _tool_registry에 자동 추가됨

문제:
  Registry A = ToolRegistry()
  Registry B = ToolRegistry()
  Registry A.load_from_file("tools.py")
  → @tool이 _tool_registry(전역)에 등록됨
  → Registry B.load_from_file("other.py")
  → get_registered_tools()가 A의 도구까지 반환
  → Registry 간 오염

  라이브러리에서 전역 상태는 독립적인 인스턴스를 만들 수 없게 함.

해결:
  @tool은 함수에 _tool_spec만 부착 (현재도 함).
  전역 _tool_registry 제거.
  load_from_file()에서 모듈의 함수를 스캔해서 _tool_spec 있는 것만 수집.
```

```python
# 변경 전 (전역 상태)
_tool_registry: list[ToolSpec] = []

def tool(...):
    def decorator(fn):
        spec = ToolSpec(...)
        _tool_registry.append(spec)  # ← 전역 오염
        fn._tool_spec = spec
        return fn
    return decorator

# 변경 후 (전역 상태 제거)
def tool(...):
    def decorator(fn):
        spec = ToolSpec(...)
        fn._tool_spec = spec  # 함수에만 부착, 전역 등록 안 함
        return fn
    return decorator

# Registry가 직접 모듈에서 수집
class ToolRegistry:
    def load_from_file(self, path):
        module = import_module(path)
        for obj in vars(module).values():
            if hasattr(obj, '_tool_spec'):
                self.register(obj._tool_spec)
```

### 문제 3: Sandbox 의존성 불일치

```
pyproject.toml:
  sandbox = ["docker>=7.0"]   ← Python Docker SDK

실제 코드 (sandbox.py):
  await asyncio.create_subprocess_exec("docker", "run", ...)  ← Docker CLI 직접 호출

문제:
  pip install mantis[sandbox] 해도 Python Docker SDK만 설치됨
  실제로는 Docker CLI가 시스템에 설치되어 있어야 동작
  SDK를 안 쓰면서 SDK를 의존성에 넣은 불일치

해결 — 둘 중 하나:
  A. Docker SDK 사용으로 전환 (import docker; client = docker.from_env())
     → 더 안정적, 에러 핸들링 좋음
  B. pyproject.toml에서 docker SDK 제거, 문서에 "Docker CLI 필요" 명시
     → 외부 의존성 최소화

  권장: A (SDK 사용). 라이브러리가 시스템 CLI 존재를 가정하면 안 됨.
```

### 문제 4: Generator들의 LLM 강결합

```
현재:
  ToolGenerator.__init__(model_client: ModelClient, ...)
  WorkflowGenerator.__init__(model_client: ModelClient, ...)

문제:
  구체 클래스 ModelClient를 타입 힌트로 직접 사용
  → 사용자가 다른 LLM 구현을 넣을 수 없음

해결:
  Protocol 기반으로 변경
  ToolGenerator.__init__(llm: LLMProvider, ...)
  WorkflowGenerator.__init__(llm: LLMProvider, ...)
```

### 문제 5: 모듈별 독립 사용 보장

```
v1 설계 원칙:
  "각 모듈 독립 사용 가능 (검색만, 샌드박스만 등)"

v3에서 도구화하면서:
  execute_code → make_sandbox_tools(sandbox) → registry.register()
  → ToolRegistry가 필수가 됨

문제:
  "샌드박스만 쓰고 싶은데 Registry까지 만들어야 하나?"

해결:
  인프라 클래스는 독립 사용 가능하게 유지.
  도구화(make_*_tools)는 Agent와 함께 쓸 때만.
```

```python
# 독립 사용 — Registry 없이 직접
from mantis.sandbox import DockerSandbox
result = await DockerSandbox().execute("print(1+1)")

# 도구 검색만
from mantis.search import GraphToolManager
graph = GraphToolManager()
results = graph.retrieve("주문 조회", tools_list)

# Agent와 함께 쓸 때만 도구화
from mantis.sandbox.tools import make_sandbox_tools
for spec in make_sandbox_tools(sandbox):
    registry.register(spec)
```

### 문제 6: WorkflowRunner의 Agent 순환 의존

```
현재 설계:
  WorkflowRunner → type="agent" step → Agent 생성
  Agent → run_workflow 도구 → WorkflowRunner

  Agent ←→ WorkflowRunner 상호 참조

문제:
  import 순환 위험
  Agent 없이 WorkflowRunner를 쓸 수 없음

해결:
  WorkflowRunner는 "agent" step 실행 시 Protocol로 위임.
  Agent가 아닌 다른 executor도 끼울 수 있게.
```

```python
class StepExecutor(Protocol):
    """워크플로우 step 실행기. Agent 이외의 구현도 가능."""
    async def execute(self, prompt: str, tools: list[str] | None) -> str: ...

class WorkflowRunner:
    def __init__(
        self,
        registry: ToolRegistry,
        agent_executor: StepExecutor | None = None,  # Protocol
    ):
        self.agent_executor = agent_executor

    async def _run_agent_step(self, step, context):
        if not self.agent_executor:
            raise WorkflowError("agent step을 실행하려면 agent_executor가 필요합니다")
        return await self.agent_executor.execute(step.prompt, step.tools)

# Agent가 StepExecutor를 구현
class AgentStepExecutor:
    def __init__(self, model_client: LLMProvider, registry: ToolRegistry):
        ...
    async def execute(self, prompt: str, tools: list[str] | None) -> str:
        agent = Agent(name="sub", model_client=self.model_client, ...)
        return await agent.run(prompt)
```

### 문제 7: 예외 계층이 없다

```
현재:
  모든 에러가 Exception 또는 dict로 반환
  → 라이브러리 사용자가 에러 타입별로 처리할 수 없음

해결:
  mantis 전용 예외 계층 정의
```

```python
# mantis/exceptions.py
class MantisError(Exception):
    """mantis 기본 예외."""

class ToolError(MantisError):
    """도구 실행 실패."""

class ToolNotFoundError(ToolError):
    """도구를 찾을 수 없음."""

class ToolExecutionError(ToolError):
    """도구 실행 중 에러."""

class GenerationError(MantisError):
    """AI 생성 실패."""

class ToolGenerationError(GenerationError):
    """도구 코드 생성/검증 실패."""

class WorkflowGenerationError(GenerationError):
    """워크플로우 설계 실패."""

class WorkflowError(MantisError):
    """워크플로우 실행 실패."""

class SandboxError(MantisError):
    """샌드박스 실행 실패."""

class LLMError(MantisError):
    """LLM 호출 실패."""
```

### 문제 8: 공개 API 경계가 모호하다

```
현재:
  __init__.py에서 Agent, tool, ToolRegistry만 export
  나머지는 from mantis.sandbox.sandbox import DockerSandbox 처럼 내부 경로 직접 import

문제:
  라이브러리 내부 구조가 변경되면 사용자 코드가 깨짐
  어디까지가 공개 API이고 어디까지가 내부 구현인지 불명확

해결:
  각 패키지의 __init__.py에서 공개 API를 명시적으로 export.
  내부 모듈은 _ prefix 또는 __all__로 구분.
```

```python
# mantis/__init__.py — 최상위 공개 API
from mantis.engine.runner import Agent
from mantis.tools.decorator import tool, ToolSpec
from mantis.tools.registry import ToolRegistry
from mantis.exceptions import MantisError, ToolError, ...

# mantis/sandbox/__init__.py — 샌드박스 공개 API
from mantis.sandbox.sandbox import DockerSandbox, SandboxConfig, SandboxResult
from mantis.sandbox.tools import make_sandbox_tools

# mantis/generate/__init__.py — 생성기 공개 API
from mantis.generate.tool_generator import ToolGenerator, make_create_tool

# mantis/workflow/__init__.py — 워크플로우 공개 API
from mantis.workflow.store import WorkflowStore
from mantis.workflow.runner import WorkflowRunner
from mantis.workflow.generator import WorkflowGenerator
from mantis.workflow.tools import make_workflow_tools
from mantis.workflow.models import WorkflowDef, WorkflowStep

# mantis/llm/__init__.py — LLM 공개 API
from mantis.llm.protocol import LLMProvider
from mantis.llm.openai_provider import ModelClient

# mantis/middleware/__init__.py — 미들웨어 공개 API
from mantis.middleware.base import Middleware
from mantis.middleware.approval import ApprovalMiddleware
from mantis.middleware.trace import TraceMiddleware
from mantis.middleware.graph_search import GraphSearchMiddleware, AutoCorrectMiddleware
from mantis.middleware.state import StateMiddleware
```

### 검수 요약

| # | 문제 | 심각도 | 상태 | 해결 내용 |
|---|------|--------|------|-----------|
| 1 | LLMProvider Protocol 없음 | **높음** | ✅ 해결 | `llm/protocol.py` — Protocol 정의, ModelClient는 기본 구현 |
| 2 | 전역 상태 (_tool_registry) | **높음** | ✅ 해결 | `decorator.py` — 전역 list 제거, _tool_spec 부착만 유지 |
| 3 | Sandbox 의존성 불일치 | 중간 | ⏳ 미해결 | Docker CLI vs SDK — 추후 SDK 전환 검토 |
| 4 | Generator LLM 강결합 | **높음** | ✅ 해결 | `tool_generator.py` — ModelClient → LLMProvider 전환 |
| 5 | 모듈 독립 사용 | 중간 | ✅ 해결 | 인프라(DockerSandbox 등) 독립 사용 가능, 도구화는 선택 |
| 6 | WorkflowRunner ↔ Agent 순환 | **높음** | ✅ 해결 | `workflow/models.py` — StepExecutor Protocol로 분리 |
| 7 | 예외 계층 없음 | 중간 | ✅ 해결 | `exceptions.py` — MantisError 계층 정의 |
| 8 | 공개 API 경계 모호 | 중간 | ✅ 해결 | 전체 `__init__.py` export 정비 |

---

## 15. 한 장 그림

```
╔══════════════════════════════════════════════════════════════════════════════════╗
║                              Mantis v3                                          ║
║   "엔진은 Generator + Executor 두 개뿐. 나머지는 전부 도구다."                    ║
╠══════════════════════════════════════════════════════════════════════════════════╣
║                                                                                  ║
║  ┌────────────────────────────── ENGINE ──────────────────────────────────────┐  ║
║  │                                                                            │  ║
║  │   Agent (Executor)                                                         │  ║
║  │   ┌──────────────────────────────────────────────────────────────────┐     │  ║
║  │   │  for iteration in range(MAX_ITERATIONS):                         │     │  ║
║  │   │                                                                  │     │  ║
║  │   │    ① tools = registry.to_openai_tools(session_id)  ← 매번 최신  │     │  ║
║  │   │    ② tools = middleware.on_before_llm(tools)        ← 자동 필터  │     │  ║
║  │   │    ③ response = llm.generate(messages, tools)       ← Think     │     │  ║
║  │   │    ④ if no tool_calls: return response.text         ← 종료      │     │  ║
║  │   │    ⑤ for tc in tool_calls:                                       │     │  ║
║  │   │        tc = middleware.on_before_tool(tc)            ← 승인/교정 │     │  ║
║  │   │        result = registry.execute(tc)                 ← Act      │     │  ║
║  │   │        middleware.on_after_tool(tc, result)           ← 트레이스 │     │  ║
║  │   │        context.add_tool_result(tc, result)           ← Observe  │     │  ║
║  │   └──────────────────────────────────────────────────────────────────┘     │  ║
║  │                                                                            │  ║
║  │   ToolGenerator              WorkflowGenerator                             │  ║
║  │   ┌─────────────────────┐    ┌─────────────────────────┐                   │  ║
║  │   │ 설명                │    │ 설명                    │                   │  ║
║  │   │  → LLM 코드생성     │    │  → LLM 워크플로우 설계   │                   │  ║
║  │   │  → Sandbox 문법검증  │    │  → 도구 존재 검증        │                   │  ║
║  │   │  → Sandbox 기능테스트│    │  → WorkflowStore 저장    │                   │  ║
║  │   │  → Registry 등록    │    │                          │                   │  ║
║  │   └────────┬────────────┘    └────────────┬────────────┘                   │  ║
║  │            │                               │                               │  ║
║  │   ┌────────▼───────────────────────────────▼────────────────────────────┐  │  ║
║  │   │                                                                     │  │  ║
║  │   │   ToolRegistry                       WorkflowStore                  │  │  ║
║  │   │   (세션 스코프 + 소스 추적)             (워크플로우 정의 저장)          │  │  ║
║  │   │                                                                     │  │  ║
║  │   │   글로벌 도구: @tool, load_from_dir    워크플로우: generate/create로  │  │  ║
║  │   │   세션 도구: create_tool로 생성된 것     생성된 WorkflowDef들          │  │  ║
║  │   │   소스: builtin│generated│sandbox│     WorkflowRunner가 실행 시 조회  │  │  ║
║  │   │         mcp│openapi│file                                            │  │  ║
║  │   │                                                                     │  │  ║
║  │   └─────────────────────────────────────────────────────────────────────┘  │  ║
║  └────────────────────────────────────────────────────────────────────────────┘  ║
║                                          │                                       ║
║                        ┌─────────────────┼─────────────────┐                    ║
║                        ▼                 ▼                 ▼                    ║
║  ┌─────────────────────────────────── TOOLS ─────────────────────────────────┐  ║
║  │                                                                            │  ║
║  │  ┌─ Sandbox 도구 ──────┐  ┌─ 생성 도구 ──────────┐  ┌─ 조회 도구 ──────┐ │  ║
║  │  │ execute_code         │  │ create_tool           │  │ search_tools     │ │  ║
║  │  │  Docker 컨테이너에서  │  │  설명 → AI 코드생성   │  │  graph-tool-call │ │  ║
║  │  │  Python 코드 실행     │  │  → Sandbox 검증       │  │  기반 도구 검색   │ │  ║
║  │  │  pip 패키지 설치 가능  │  │  → Registry 등록      │  │                  │ │  ║
║  │  │  타임아웃/메모리 제한  │  │  → 다음 iter 즉시 사용 │  │ list_tools       │ │  ║
║  │  │                      │  │                       │  │  전체 도구 이름/  │ │  ║
║  │  │ execute_code_with_   │  │                       │  │  설명 목록 반환   │ │  ║
║  │  │   test               │  │                       │  │                  │ │  ║
║  │  │  코드+테스트 함께 실행 │  │                       │  │                  │ │  ║
║  │  │  ALL_TESTS_PASSED    │  │                       │  │                  │ │  ║
║  │  └──────────────────────┘  └───────────────────────┘  └──────────────────┘ │  ║
║  │                                                                            │  ║
║  │  ┌─ 워크플로우 도구 ──────────────────────────────────────────────────────┐ │  ║
║  │  │                                                                        │ │  ║
║  │  │  generate_workflow              create_workflow                         │ │  ║
║  │  │   설명 → LLM이 도구 목록 보고     step JSON 직접 전달                   │ │  ║
║  │  │   최적의 step 구성 자동 설계      → 검증 후 Store 저장                   │ │  ║
║  │  │   → 도구 존재 검증                (캔버스 어댑터가 이걸 호출)             │ │  ║
║  │  │   → Store 저장                                                         │ │  ║
║  │  │                                                                        │ │  ║
║  │  │  run_workflow                   list_workflows                          │ │  ║
║  │  │   Store에서 WorkflowDef 조회      등록된 워크플로우 목록                  │ │  ║
║  │  │   WorkflowRunner가 step별 실행                                          │ │  ║
║  │  │   step type별 처리:                                                     │ │  ║
║  │  │    tool → registry.execute()                                           │ │  ║
║  │  │    condition → 분기 판단                                                │ │  ║
║  │  │    agent → Sub-Agent 위임                                               │ │  ║
║  │  │    parallel → asyncio.gather()                                          │ │  ║
║  │  └────────────────────────────────────────────────────────────────────────┘ │  ║
║  │                                                                            │  ║
║  │  ┌─ 사용자 도구 (@tool) ──────────────────────────────────────────────────┐ │  ║
║  │  │  lookup_order, send_slack, query_db, fetch_weather, ...                │ │  ║
║  │  │  (직접 정의 또는 AI가 create_tool로 생성)                                │ │  ║
║  │  └────────────────────────────────────────────────────────────────────────┘ │  ║
║  └────────────────────────────────────────────────────────────────────────────┘  ║
║                                                                                  ║
║  ┌──────────────────────────── MIDDLEWARE ────────────────────────────────────┐  ║
║  │  Agent 루프의 매 단계에 자동으로 끼어듦 (LLM이 선택하는 게 아님)             │  ║
║  │                                                                            │  ║
║  │  on_start          on_before_llm       on_before_tool                      │  ║
║  │  ┌──────────┐      ┌──────────────┐    ┌───────────────┐                   │  ║
║  │  │State     │      │GraphSearch   │    │Approval       │                   │  ║
║  │  │ 세션 복구 │      │ 도구 15개 이상│    │ 위험 패턴 매칭 │                   │  ║
║  │  │ 체크포인트│      │ → 자동 필터링 │    │ → 승인 요청    │                   │  ║
║  │  └──────────┘      └──────────────┘    │ → 거절 시 차단 │                   │  ║
║  │                                         └───────────────┘                   │  ║
║  │  on_after_tool     on_end              ┌───────────────┐                   │  ║
║  │  ┌──────────┐      ┌──────────┐        │AutoCorrect    │                   │  ║
║  │  │Trace     │      │State     │        │ 도구 이름 오타 │                   │  ║
║  │  │ 도구 호출 │      │ 최종 상태│        │ → fuzzy 교정   │                   │  ║
║  │  │ 이력 기록 │      │ 저장     │        │ 파라미터 보정   │                   │  ║
║  │  └──────────┘      └──────────┘        └───────────────┘                   │  ║
║  └────────────────────────────────────────────────────────────────────────────┘  ║
║                                                                                  ║
║  ┌──────────────────────── INFRASTRUCTURE ───────────────────────────────────┐  ║
║  │  엔진/미들웨어가 내부적으로 사용하는 인프라 (Agent에 직접 노출 안 됨)        │  ║
║  │                                                                            │  ║
║  │  DockerSandbox         GraphToolManager       ApprovalManager              │  ║
║  │   Docker API 호출       graph-tool-call 연동    패턴 매칭/승인 대기          │  ║
║  │   컨테이너 생성/실행     임베딩 기반 검색         요청/대기/응답              │  ║
║  │   타임아웃/메모리 관리   호출 이력 기반 추천                                  │  ║
║  │                                                                            │  ║
║  │  ModelClient            ConversationContext    TraceCollector               │  ║
║  │   OpenAI 호환 API        messages 배열 관리     실행 흐름 기록               │  ║
║  │   httpx 기반             system/user/assistant  TraceExporter로 내보내기     │  ║
║  │   스트리밍 지원           /tool 역할 관리        (Jaeger, Langfuse 등)       │  ║
║  │                                                                            │  ║
║  │  StateStore             ToolTester                                          │  ║
║  │   체크포인트/재개         smoke_test (더미 호출)                               │  ║
║  │   PostgreSQL/메모리      schema_validate (타입 검증)                          │  ║
║  │                          run_assert_tests/pytest (Sandbox 검증)             │  ║
║  └────────────────────────────────────────────────────────────────────────────┘  ║
║                                                                                  ║
║  ┌────────────────────────── ADAPTERS ────────────────────────────────────────┐  ║
║  │                                                                            │  ║
║  │  SSEAdapter              CanvasAdapter              FastAPIAdapter          │  ║
║  │   ExecutionEvent →        xgen-workflow 캔버스        Agent → FastAPI 라우터 │  ║
║  │   event: type\n           JSON → create_workflow     즉시 API 서버 생성     │  ║
║  │   data: json\n\n          도구 호출로 변환                                  │  ║
║  │                           (노드→step, 엣지→edge)                            │  ║
║  └────────────────────────────────────────────────────────────────────────────┘  ║
║                                                                                  ║
╠══════════════════════════════════════════════════════════════════════════════════╣
║                                                                                  ║
║  도구의 생명주기:                                                                 ║
║                                                                                  ║
║  ① 등록                          ② 검색/선택                 ③ 실행             ║
║  @tool 데코레이터          →       GraphSearch 미들웨어  →     Agent가            ║
║  load_from_directory       →       (자동: 15개 이상)    →     registry.execute() ║
║  create_tool (AI 생성)     →       search_tools 도구    →     호출               ║
║  make_sandbox_tools()      →       (명시적: Agent 판단)                           ║
║  make_workflow_tools()     →                                                     ║
║  MCP Bridge / OpenAPI      →       list_tools 도구                               ║
║         │                          (전체 목록 조회)                               ║
║         ▼                                                                        ║
║  ToolRegistry                                                                    ║
║  (글로벌 + 세션 스코프)                                                            ║
║                                                                                  ║
║  워크플로우의 생명주기:                                                             ║
║                                                                                  ║
║  ① 설계                          ② 저장           ③ 실행                        ║
║  generate_workflow (AI 설계) →    WorkflowStore →  run_workflow →                ║
║  create_workflow (수동 정의) →                      WorkflowRunner가              ║
║  CanvasAdapter (캔버스 변환) →                      step별 순차 실행              ║
║                                                     조건 분기/병렬 처리           ║
║                                                     Sub-Agent 위임              ║
║                                                                                  ║
╠══════════════════════════════════════════════════════════════════════════════════╣
║                                                                                  ║
║  배포 타겟:                                                                       ║
║                                                                                  ║
║  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐               ║
║  │  xgen-workflow    │  │  xgen3.0         │  │  새 프로젝트      │               ║
║  │                   │  │                  │  │                  │               ║
║  │  CanvasAdapter로  │  │  기존처럼 사용    │  │  pip install     │               ║
║  │  캔버스 JSON 변환  │  │  Agent + 미들웨어 │  │  mantis          │               ║
║  │  execution_core → │  │  그대로 이식      │  │  3줄이면 시작    │               ║
║  │  Agent로 교체      │  │                  │  │                  │               ║
║  │  agent_core →     │  │                  │  │                  │               ║
║  │  삭제 (Agent 대체) │  │                  │  │                  │               ║
║  └──────────────────┘  └──────────────────┘  └──────────────────┘               ║
║                                                                                  ║
║  의존성: pip install mantis → httpx만 필수                                        ║
║          mantis[search] → + graph-tool-call   mantis[sandbox] → + docker         ║
║          mantis[state]  → + asyncpg           mantis[all] → 전부                 ║
║                                                                                  ║
╚══════════════════════════════════════════════════════════════════════════════════╝
```
