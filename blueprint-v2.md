# Mantis v2 — 설계 청사진

## 한줄 요약

> v1의 Phase 파이프라인을 유지하면서, **도구 목록이 생성기·실행기·검증기를 관통하여 동적으로 흐르게** 한다.
> 샌드박스를 도구로 노출하고, 캔버스 없이도 워크플로우를 코드로 조립·분기할 수 있게 한다.
> **핵심 설계 철학: 하나의 ToolRegistry를 모든 컴포넌트가 공유하고, 매 iteration마다 최신 도구를 조회한다.**

---

## 0. v1 기반 구조 (유지)

v2는 v1의 5-Phase 파이프라인을 그대로 유지한다. Phase 상세는 [blueprint.md](blueprint.md) 참조.

```
Phase 1: PREPARE     전처리 — 워크플로우 로드, 도구 수집, 컨텍스트 조립
Phase 2: RESOLVE     결정 — 도구 검색, 입력 매핑, 스키마 결정
Phase 3: EXECUTE     실행 — Think→Act→Observe 루프, 도구 호출
Phase 4: STREAM      전달 — 실행 이벤트 → SSE 변환 → 클라이언트
Phase 5: PERSIST     저장 — DB, Trace, 세션 상태, 리소스 정리
```

v2는 이 위에 **동적 도구 흐름, 샌드박스 도구화, 워크플로우 엔진, Tool Store**를 얹는다.

---

## 1. v1에서 무엇이 부족한가

### 문제 1: 도구 목록이 Phase 2에서 고정된다

```
v1 흐름:
  Phase 2 (RESOLVE) → tools_schema 결정 (고정)
  Phase 3 (EXECUTE) → 이 고정된 목록으로 계속 실행

  Iteration 1: LLM → create_tool("send_slack") → 생성 + Registry 등록
  Iteration 2: LLM → send_slack("#general", "hello") → ❌ 도구 없음
  → tools_schema가 Phase 2에서 찍은 스냅샷이라 새 도구가 안 보임
```

mantis v2 해결 방식:

```
v2 흐름:
  Executor._resolve_tools()가 매 iteration마다 호출됨
  → registry.to_openai_tools()가 현재 시점의 전체 도구 반환
  → create_tool로 등록된 도구가 다음 iteration에서 즉시 사용 가능
```

### 문제 2: 샌드박스가 인프라일 뿐 도구가 아니다

```
v1:
  DockerSandbox → ToolGenerator 내부에서만 사용
  Agent가 "이 코드 돌려봐" 할 수 없음

mantis v2:
  @tool(name="execute_code") → Agent가 자유롭게 코드 실행
  @tool(name="execute_code_with_test") → 코드 + 테스트 함께 실행
  → 일반 도구와 동일하게 Registry에 등록됨

OpenAI Codex:
  Container를 도구처럼 던짐 → 실행 → 결과 → 다음 판단
```

### 문제 3: 워크플로우가 캔버스에서만 만들어진다

```
xgen-workflow:
  async_workflow_executor.py → 캔버스 JSON의 노드/엣지를 순회
  도구 목록 = 캔버스 엣지에 연결된 것만 (고정)
  Router = 정적 분기 (실행 중 조건 변경 불가)

  → 코드로 워크플로우를 조립할 수 없음
  → AI가 워크플로우를 자동 생성할 수 없음
  → 실행 중 동적 분기 불가
```

### 문제 4: 도구 목록이 시스템 간에 연동 안 된다

```
도구 생성기              실행기                 xgen-workflow (캔버스)
  도구를 만듦               도구를 씀              노드로 도구 연결
  └─ 실행기와 분리됨         └─ 생성기와 분리        └─ 실시간 동기화 없음

미래: xgen 앱 여러 개
  앱 A (고객 상담)    앱 B (데이터 분석)    앱 C (운영 자동화)
  → 같은 도구 풀을 공유해야 함
```

### 요약: v2에서 해결할 것

| 문제 | v1 상태 | v2 목표 |
|------|---------|---------|
| 도구 목록 고정 | Phase 2에서 스냅샷 | 매 iteration마다 Registry 재조회 |
| 샌드박스 | ToolGenerator 내부 인프라 | Agent가 호출하는 도구 |
| 도구 생성 → 즉시 사용 | 불가 | 생성 → Registry 등록 → 다음 iteration에서 사용 |
| 워크플로우 | 캔버스 JSON만 | 코드 조립 + 캔버스 호환 + 동적 분기 |
| 멀티 앱 | 단일 프로세스 | 공유 Registry 백엔드 |

---

## 2. 핵심 설계 철학 — 공유 ToolRegistry

### v1: Phase 간 데이터 전달

```
Phase 2 (RESOLVE)                       Phase 3 (EXECUTE)
  tools_schema 결정 ──(고정 전달)──→    이 목록으로 실행
```

### v2: 하나의 Registry를 모든 컴포넌트가 공유

```
                    ToolRegistry (하나의 인스턴스)
                           │
      ┌────────────────────┼────────────────────┐
      │                    │                    │
  Executor             ToolGenerator        WorkflowEngine
  매 iteration마다      생성 → 즉시 등록     노드별 도구 바인딩
  to_openai_tools()
  재조회
```

mantis v2의 패턴:

```python
# 하나의 registry를 모든 곳에서 참조
tool_registry = ToolRegistry()
# → Agent, ToolGenerator, GraphToolManager 모두 이 하나의 인스턴스 공유
# → create_tool이 registry에 등록하면 Agent의 다음 iteration에서 즉시 보임
```

---

## 3. ToolRegistry 확장

### v1 ToolRegistry

```
입력: ToolSpec (name, description, parameters, fn)
출력: OpenAI function calling 스키마
하는 일: 등록/조회/실행
```

### v2 ToolRegistry — 세션 스코프 + 소스 추적

```
입력: ToolSpec + source + session_id (선택)
출력: OpenAI function calling 스키마 (글로벌 + 세션 도구 합산)
하는 일:
  1. 도구 등록 (글로벌 또는 세션 스코프)
  2. 소스 추적 (builtin, generated, sandbox, mcp, openapi)
  3. 세션별 독립 도구 관리
  4. 세션 종료 시 정리
```

```python
class ToolRegistry:
    """v2: 세션 스코프 + 소스 추적.

    v1과 하위 호환: session_id를 안 쓰면 v1과 동일하게 동작.
    """

    _tools: dict[str, ToolSpec]                          # 글로벌 도구
    _sources: dict[str, str]                              # tool_name → source
    _session_tools: dict[str, dict[str, ToolSpec]]        # session_id → {name: spec}

    def register(self, spec: ToolSpec, source: str = "manual", session_id: str | None = None):
        """도구 등록.

        session_id 있으면 → 해당 세션에서만 보이는 도구
        session_id 없으면 → 글로벌 (모든 세션에서 사용)
        """
        if session_id:
            self._session_tools.setdefault(session_id, {})[spec.name] = spec
        else:
            self._tools[spec.name] = spec
        self._sources[spec.name] = source

    def to_openai_tools(self, session_id: str | None = None) -> list[dict]:
        """현재 시점의 도구 목록 반환.

        ★ 핵심: 호출할 때마다 최신 상태를 반환한다.
        create_tool로 등록된 도구가 즉시 포함됨.
        """
        tools = dict(self._tools)
        if session_id and session_id in self._session_tools:
            tools.update(self._session_tools[session_id])
        return [spec.to_openai_schema() for spec in tools.values()]

    def cleanup_session(self, session_id: str):
        """세션 종료 시 세션 스코프 도구 정리."""
        self._session_tools.pop(session_id, None)
```

---

## 4. Executor 변경 — 매 iteration마다 도구 재조회

### v1 ExecutePhase

```
입력: ResolvedContext (tools_schema 고정)
→ 모든 iteration에서 같은 도구 목록 사용
```

### v2 ExecutePhase

```
입력: ResolvedContext + ToolRegistry 참조
→ 매 iteration마다 registry.to_openai_tools(session_id) 재조회
→ create_tool로 만든 도구가 다음 iteration에서 즉시 사용 가능
```

```python
class ExecutePhase:
    async def run(self, ctx: ResolvedContext) -> AsyncGenerator[ExecutionEvent]:

        for iteration in range(max_iterations):
            # ★ v2: 매 iteration마다 최신 도구 조회
            tools_schema = self._resolve_tools(
                query=ctx.context.message,
                session_id=ctx.context.session_id,
            )

            # Think
            yield ExecutionEvent("thinking", {"iteration": iteration})
            response = await self.llm.generate(
                messages=conversation.to_messages(),
                tools=tools_schema,
            )

            # 종료
            if not response.tool_calls:
                yield ExecutionEvent("done", {"text": response.text})
                return

            # Act
            for tc in response.tool_calls:
                tc = self.validate(tc)

                if self.approval and self.approval.needs(tc):
                    yield ExecutionEvent("approval_required", tc.to_dict())
                    decision = await self.approval.wait()
                    if not decision.approved:
                        continue

                yield ExecutionEvent("tool_call", {"name": tc.name, "args": tc.args})
                result = await self.tool_registry.execute(tc)
                yield ExecutionEvent("tool_result", {"name": tc.name, "result": result})

                ctx.messages.add_tool_result(tc, result)

    def _resolve_tools(self, query: str, session_id: str | None) -> list[dict]:
        """매 iteration마다 최신 도구 목록 조회."""
        if self.graph_search and self.graph_search.should_use_search:
            result = self.graph_search.retrieve_as_openai_tools(query)
            if result:
                return result
        return self.tool_registry.to_openai_tools(session_id=session_id)
```

### 동작 시나리오

```
사용자: "슬랙 도구 만들어서 #general에 인사해"

Iteration 1:
  _resolve_tools() → [create_tool, execute_code, ...]
  LLM → create_tool(description="슬랙 채널에 메시지를 보내는 도구")
  ToolGenerator → LLM 코드생성 → Sandbox 검증 → registry.register("send_slack", session_id="s1")

Iteration 2:
  _resolve_tools(session_id="s1") → [..., send_slack]  ← 즉시 포함됨
  LLM → send_slack(channel="#general", text="안녕하세요!")
  실행 → 성공

Iteration 3:
  LLM → "메시지를 보냈습니다." (done)
```

---

## 5. Sandbox 도구화

### v1: 인프라로서의 Sandbox

```
DockerSandbox
  └─ ToolGenerator._test_syntax()에서 사용
  └─ ToolGenerator._test_functional()에서 사용
  └─ ToolTester.smoke_test()에서 사용
  → Agent가 직접 호출 불가
```

### v2: 도구로서의 Sandbox

Sandbox를 @tool 함수로 감싸서 Registry에 등록:

```
DockerSandbox
  ├─ 인프라: ToolGenerator, ToolTester가 내부적으로 사용 (v1과 동일)
  └─ 도구: execute_code, execute_code_with_test를 ToolRegistry에 등록
           → Agent가 자유롭게 코드 실행/실험 가능
```

```python
# mantis/sandbox/tools.py

def make_sandbox_tools(sandbox: DockerSandbox) -> list[ToolSpec]:
    """DockerSandbox를 Agent가 쓸 수 있는 도구 2개로 변환."""

    @tool(
        name="execute_code",
        description="Python 코드를 격리된 Docker 컨테이너에서 실행한다. "
                    "데이터 분석, 계산, 파일 처리, API 테스트 등에 사용.",
        parameters={
            "code": {"type": "string", "description": "실행할 Python 코드"},
            "pip_packages": {
                "type": "array", "items": {"type": "string"},
                "description": "설치할 pip 패키지 (선택)",
            },
            "timeout": {
                "type": "integer",
                "description": "타임아웃 초 (기본 30, 최대 120)",
            },
        },
    )
    async def execute_code(code: str, timeout: int = 30, pip_packages: list = None) -> dict:
        result = await sandbox.execute(code, pip_packages=pip_packages, timeout=min(timeout, 120))
        return {"stdout": result.stdout, "stderr": result.stderr, "exit_code": result.exit_code}

    @tool(
        name="execute_code_with_test",
        description="Python 코드와 테스트 코드를 함께 실행하여 검증한다.",
        parameters={
            "code": {"type": "string", "description": "검증할 코드"},
            "test_code": {"type": "string", "description": "테스트 코드"},
        },
    )
    async def execute_code_with_test(code: str, test_code: str) -> dict:
        combined = code + "\n\n# --- Tests ---\n\n" + test_code
        result = await sandbox.execute(combined, timeout=30)
        return {
            "stdout": result.stdout, "stderr": result.stderr,
            "exit_code": result.exit_code,
            "tests_passed": "ALL_TESTS_PASSED" in result.stdout,
        }

    return [execute_code._tool_spec, execute_code_with_test._tool_spec]
```

### Registry 등록

```python
sandbox = DockerSandbox()

# Sandbox 도구를 일반 도구와 동일하게 등록
for spec in make_sandbox_tools(sandbox):
    registry.register(spec, source="sandbox")

# 이제 Agent는 execute_code, execute_code_with_test를 자유롭게 호출
```

---

## 6. ToolGenerator 정비

### 도구 생성 파이프라인 (6단계)

```
1. LLM 코드 생성 (temperature=0.3, 구조화 프롬프트)
   → @tool 데코레이터 포함 Python 코드 + 테스트 코드

2. 도구 이름 추출
   → regex: @tool(name="xxx")

3. Sandbox 문법 검증 (timeout=15s)
   → _MOCK_PREAMBLE + code + print("SYNTAX_OK")
   → DockerSandbox에서 실행

4. Sandbox 기능 테스트 (timeout=30s)
   → _MOCK_PREAMBLE + code + test_code
   → "ALL_TESTS_PASSED" in stdout

5. 파일 저장
   → tools/{tool_name}.py

6. Registry 등록
   → registry.load_from_file(path)
   → 다음 iteration에서 즉시 사용 가능
```

### mantis v2 ToolGenerator

```
입력: description (문자열) + session_id
출력: ToolSpec (이미 Registry에 등록된 상태)

하는 일:
  1. LLM 코드 생성
  2. 도구 이름 추출
  3. Sandbox 문법 검증
  4. Sandbox 기능 테스트
  5. 파일 저장 (선택 — 라이브러리이므로)
  6. Registry 등록 (session_id 지원 추가)
```

```python
class ToolGenerator:
    """도구 생성 파이프라인.

    핵심 변경 (vs v1):
    - session_id 지원 → 세션별 도구 격리
    - registry.register(source="generated") → 즉시 사용 가능
    - make_create_tool()로 Agent 호출 가능한 도구로 변환
    """

    def __init__(
        self,
        llm: ModelClient,
        registry: ToolRegistry,
        sandbox: DockerSandbox,
        tools_dir: Path | None = None,  # None이면 파일 저장 안 함
    ):
        self.llm = llm
        self.registry = registry
        self.sandbox = sandbox
        self.tools_dir = tools_dir

    async def create(self, description: str, session_id: str | None = None) -> ToolSpec:
        """도구 생성 전체 파이프라인. 반환 시점에 이미 Registry에 등록됨."""

        # 1. LLM 코드 생성
        code, test_code = await self._generate_code(description)
        tool_name = self._extract_tool_name(code)

        # 2. Sandbox 문법 검증
        syntax = await self.sandbox.execute(
            code=_MOCK_PREAMBLE + code + '\nprint("SYNTAX_OK")',
            timeout=15,
        )
        if not syntax.success or "SYNTAX_OK" not in syntax.stdout:
            raise ToolGenerationError("syntax", syntax.stderr)

        # 3. Sandbox 기능 테스트
        test = await self.sandbox.execute(
            code=_MOCK_PREAMBLE + code + "\n\n" + test_code,
            timeout=30,
        )
        if "ALL_TESTS_PASSED" not in test.stdout:
            raise ToolGenerationError("test", test.stderr)

        # 4. 파일 저장 (선택)
        if self.tools_dir:
            (self.tools_dir / f"{tool_name}.py").write_text(code)

        # 5. Registry에 즉시 등록
        spec = self._code_to_spec(code, tool_name)
        self.registry.register(spec, source="generated", session_id=session_id)

        return spec


def make_create_tool(generator: ToolGenerator) -> ToolSpec:
    """ToolGenerator를 Agent가 호출할 수 있는 도구로 변환."""

    @tool(
        name="create_tool",
        description="새로운 도구를 만든다. 설명을 받아 Python 코드를 생성하고, "
                    "Docker에서 테스트한 후 등록한다. 등록 즉시 사용 가능.",
        parameters={
            "description": {"type": "string", "description": "만들 도구의 기능 설명"},
        },
    )
    async def create_tool(description: str) -> dict:
        spec = await generator.create(description)
        return {"tool_name": spec.name, "status": "registered"}

    return create_tool._tool_spec
```

---

## 7. WorkflowEngine — 캔버스 실행기를 라이브러리로

### xgen-workflow 실행 모델 분석

```
async_workflow_executor.py 핵심 로직:

  execution_order = topological_sort(nodes, edges)    # Kahn 알고리즘
  node_outputs: dict[str, dict[str, Any]] = {}        # 노드별 출력 저장
  excluded_nodes: set[str] = set()                     # Router에 의해 비활성

  for node_id in execution_order:
      if node_id in excluded_nodes: continue

      # 엣지에서 입력 수집
      kwargs = {}
      for edge in edges_to(node_id):
          kwargs[edge.target_port] = node_outputs[edge.source_node][edge.source_port]

      # 노드 실행
      result = node.execute(**kwargs)
      node_outputs[node_id] = {port_id: result}

      # Router면 분기 처리
      if is_router(node_id):
          inactive_branches = get_inactive_branches(result)
          excluded_nodes.update(inactive_branches)
```

### mantis WorkflowEngine

```
입력: 노드 + 엣지 (캔버스 JSON, 코드, 또는 LLM 생성)
출력: AsyncGenerator[WorkflowEvent]

하는 일:
  1. 위상 정렬 (Kahn 알고리즘)
  2. 노드별 순차 실행
  3. 엣지 통해 출력→입력 전달 (node_outputs)
  4. Router 노드에서 조건 분기 (excluded_nodes)
  5. 각 단계를 WorkflowEvent로 yield

3가지 생성 모드:
  A. from_canvas(json)  → xgen-workflow 호환
  B. 코드로 직접 조립    → 캔버스 없이 사용
  C. from_llm(description) → AI가 워크플로우 자동 생성
```

```python
class WorkflowEngine:
    """xgen-workflow의 async_workflow_executor를 라이브러리로 재구성."""

    def __init__(self, registry: ToolRegistry):
        self.registry = registry
        self.nodes: dict[str, WorkflowNode] = {}
        self.edges: list[Edge] = []

    # ─── 모드 A: 캔버스 호환 ───

    @classmethod
    def from_canvas(cls, workflow_data: dict, registry: ToolRegistry) -> "WorkflowEngine":
        """xgen-workflow 캔버스 JSON → WorkflowEngine 변환.

        노드 타입 매핑:
          agents/xgen  → AgentNode (mantis Agent 실행)
          router       → RouterNode (조건 분기)
          api_tool     → ToolNode (도구 직접 호출)
          qdrant       → RAGNode (벡터 검색)
          schema_input → InputNode
          end          → OutputNode
        """
        engine = cls(registry)
        for node in workflow_data["nodes"]:
            engine.add_node(cls._map_node(node, registry))
        for edge in workflow_data["edges"]:
            engine.add_edge(Edge(
                source_node=edge["source"]["nodeId"],
                source_port=edge["source"]["portId"],
                target_node=edge["target"]["nodeId"],
                target_port=edge["target"]["portId"],
            ))
        return engine

    # ─── 모드 B: 코드 조립 ───

    def add_node(self, node: WorkflowNode): ...
    def add_edge(self, edge: Edge): ...

    # ─── 모드 C: LLM 자동 생성 ───

    @classmethod
    async def from_llm(cls, description: str, llm: ModelClient, registry: ToolRegistry) -> "WorkflowEngine":
        """LLM이 워크플로우를 자동 생성.

        사용 가능한 도구 목록을 LLM에 알려주고,
        노드/엣지 구조를 JSON으로 받아서 조립.
        """
        available_tools = registry.list_names()
        response = await llm.generate(messages=[
            {"role": "system", "content": f"Available tools: {available_tools}\n"
                                          "Design a workflow as JSON with nodes and edges."},
            {"role": "user", "content": description},
        ])
        return cls.from_canvas(json.loads(response.text), registry)

    # ─── 실행 ───

    async def run(self, input_data: dict) -> AsyncIterator[WorkflowEvent]:
        """워크플로우 실행.

        xgen-workflow의 _execute_workflow_sync를 대체.
        """
        execution_order = self._topological_sort()
        node_outputs: dict[str, dict[str, Any]] = {}
        excluded: set[str] = set()

        for node_id in execution_order:
            if node_id in excluded:
                continue

            node = self.nodes[node_id]
            inputs = self._collect_inputs(node_id, node_outputs)

            yield WorkflowEvent("node_start", {"node_id": node_id, "type": node.type})

            result = await node.execute(inputs)
            node_outputs[node_id] = result

            yield WorkflowEvent("node_complete", {"node_id": node_id, "output": result})

            if isinstance(node, RouterNode):
                inactive = self._get_inactive_branches(node_id, result)
                excluded.update(inactive)

        yield WorkflowEvent("workflow_complete", {"outputs": node_outputs})
```

### 노드 타입

```python
class WorkflowNode(Protocol):
    id: str
    type: str
    async def execute(self, inputs: dict) -> dict[str, Any]: ...

class AgentNode:
    """mantis Agent로 실행. xgen-workflow의 agent_core.py 대체.
    LangChain 블랙박스 대신 Think→Act→Observe 직접 루프."""
    type = "agent"

class RouterNode:
    """조건 분기. xgen-workflow의 process_router_node_output 대체."""
    type = "router"

class ToolNode:
    """단일 도구 직접 실행."""
    type = "tool"

class RAGNode:
    """벡터 검색 (Qdrant 등)."""
    type = "rag"

class InputNode:
    """입력 스키마 파싱."""
    type = "input"

class OutputNode:
    """결과 출력."""
    type = "output"
```

### 코드로 워크플로우 조립 (캔버스 없이)

```python
engine = WorkflowEngine(registry=registry)

engine.add_node(AgentNode(id="analyze", model="gpt-4o-mini", prompt="데이터 분석"))
engine.add_node(RouterNode(id="check", conditions={
    "good": lambda s: s.get("confidence", 0) > 0.8,
    "retry": lambda s: s.get("confidence", 0) <= 0.8,
}))
engine.add_node(AgentNode(id="report", model="gpt-4o-mini", prompt="리포트 작성"))

engine.add_edge(Edge("analyze", "result", "check", "input"))
engine.add_edge(Edge("check", "good", "report", "text"))
engine.add_edge(Edge("check", "retry", "analyze", "text"))  # 루프백

async for event in engine.run({"text": "매출 데이터 분석해줘"}):
    print(event)
```

---

## 8. xgen-workflow 이식 — mantis로 교체하는 지점

### xgen-workflow 현재 계층

```
controller/workflow/utils/execution_core.py (695줄)
  │  전처리 + 이벤트 파싱 + DB 저장
  │
  ▼
editor/async_workflow_executor.py (1089줄)
  │  DAG 워커 (스레드풀 + queue.Queue → async 변환)
  │  node_outputs로 노드 간 데이터 전달
  │
  ▼
editor/nodes/xgen/agent/agent_core.py (1193줄)
  │  LangChain create_agent(model, tools) → 블랙박스 실행
  │  [AGENT_EVENT] 문자열 태그로 이벤트 출력
  │
  ▼
editor/nodes/xgen/agent/agent_xgen.py (1525줄)
     캔버스 Agent 노드 — tools 포트에서 도구 수집
```

### mantis로 교체하는 매핑

```
xgen-workflow (현재)                         mantis (교체)
──────────────────────                       ──────────────
execution_core.py                        →   Phase Pipeline
  workflow_data 로드                          PREPARE: 워크플로우 파싱
  파일 선택, bypass 전처리                     PREPARE: 전처리
  [AGENT_EVENT] 태그 파싱                      STREAM: 구조화 이벤트 (파싱 불필요)
  ExecutionIO DB 저장                          PERSIST: DB 저장
  Redis 세션 업데이트                           PERSIST: 상태 업데이트

async_workflow_executor.py               →   WorkflowEngine
  topological_sort()                          WorkflowEngine._topological_sort()
  node_outputs dict                           WorkflowEngine.node_outputs
  엣지 기반 입력 수집                           WorkflowEngine._collect_inputs()
  Router 분기 + excluded_nodes                 RouterNode + _get_inactive_branches()
  스레드풀 + Queue → async                     네이티브 async/await (스레드 불필요)
  Generator tee / BufferedGeneratorFactory     AsyncGenerator 직접 사용

agent_core.py + agent_xgen.py            →   AgentNode + Agent
  LangChain create_agent(tools)               mantis Agent(tool_registry)
  prepare_llm_components()                    ToolRegistry.to_openai_tools()
  kwargs['tools'] (엣지에서 고정)              매 iteration Registry 재조회
  [AGENT_EVENT] 문자열 출력                    ExecutionEvent 구조화 객체
  LangGraph 블랙박스                           Think→Act→Observe 직접 루프
```

### xgen-workflow에 mantis 끼우는 코드

```python
# repos/xgen-workflow/editor/nodes/xgen/agent/agent_core.py
# LangChain 코드를 mantis로 교체

# Before (LangChain)
from langchain.agents import create_agent
agent = create_agent(model=llm, tools=tools_list)
result = agent.invoke(user_input)

# After (mantis)
from mantis import Agent, ToolRegistry
from mantis.llm.openai_provider import ModelClient

registry = ToolRegistry()
for tool_obj in tools_list:  # 캔버스 엣지에서 온 도구
    registry.register(tool_obj)

agent = Agent(
    name=node_name,
    model_client=ModelClient(model=model_name, api_key=api_key),
    tool_registry=registry,
    system_prompt=system_prompt,
)

# 스트리밍 — [AGENT_EVENT] 태그 파싱 대신 구조화 이벤트
async for event in agent.run_stream(user_input):
    yield event  # {"type": "tool_call", "data": {...}}
```

### execution_core.py 단순화

```python
# Before (695줄 — 전처리+실행+파싱+저장 뒤섞임)
async def run_workflow_execution(...):
    workflow_data = load_from_db(...)
    apply_file_selection(...)
    apply_bypass(...)
    executor = create_executor(workflow_data)
    async for chunk in executor.execute_streaming():
        # [AGENT_EVENT] 정규식 파싱
        # [AGENT_STATUS] 파싱
        # IO 살균
        # 에러 치환
        yield sse_format(chunk)
    save_execution_io(...)
    update_redis(...)

# After (mantis Phase 파이프라인)
from mantis.workflow import WorkflowEngine
from mantis.pipeline import build_pipeline

async def run_workflow_execution(workflow_data: dict, user_input: str):
    engine = WorkflowEngine.from_canvas(workflow_data, registry)
    async for event in engine.run({"text": user_input}):
        yield event.to_sse()
    # PERSIST Phase가 DB 저장 + 상태 업데이트 자동 처리
```

### 도구 전달 방식 변화

```
Before (캔버스 엣지 고정):
  [API Tool] ──엣지──→ Agent.tools 포트 → kwargs['tools'] = [tool1, tool2]
  → 실행 중 도구 추가 불가
  → 도구 30개 전부 LLM에 전달

After (mantis ToolRegistry):
  [API Tool] ──엣지──→ AgentNode → registry.register(tool)
  → registry.to_openai_tools()로 매 iteration 최신 조회
  → GraphToolManager로 관련 도구만 검색
  → create_tool로 실행 중 도구 추가 가능
```

### 스트리밍 모델 변화

```
Before (스레드 + 큐 + 문자열 태그):
  _execute_workflow_sync()     ← 동기, 스레드풀에서 실행
    → self._streaming_queue.put(('data', chunk))
    → self._streaming_queue.put(('agent_event', {...}))
  execute_workflow_async_streaming()  ← 비동기, 큐 폴링
    → queue.get(timeout=0.02)
  execution_core.py
    → [AGENT_EVENT]{json}[/AGENT_EVENT] 정규식 파싱

After (네이티브 async + 구조화 이벤트):
  WorkflowEngine.run()        ← 네이티브 async
    → yield WorkflowEvent("node_start", {...})
    → AgentNode → yield ExecutionEvent("tool_call", {...})
    → yield WorkflowEvent("node_complete", {...})
  → 문자열 파싱 불필요, 스레드 불필요
```

---

## 9. 도구 목록 전체 흐름

v2의 핵심: 하나의 ToolRegistry를 통해 모든 곳에서 동기화.

```
도구 소스                    ToolRegistry              소비자
──────────                  ──────────────             ──────
@tool 데코레이터     ──register──→                  ──→ Executor (매 iter 조회)
load_from_directory  ──register──→                  ──→ GraphToolManager (검색)
make_sandbox_tools() ──register──→  to_openai_tools ──→ LLM (function calling)
make_create_tool()   ──register──→  (session_id)    ──→ WorkflowEngine (노드 바인딩)
MCP Bridge           ──register──→
OpenAPI Loader       ──register──→

도구 검증:
  @tool 등록 시         → ToolTester.smoke_test()
  create_tool 생성 시   → Sandbox 문법+기능 테스트
  MCP/OpenAPI 등록 시   → ToolTester.validate_schema()
  실패 → 등록 거부      → 깨진 도구가 LLM에 전달 안 됨
```

---

## 10. 멀티 앱 배포

### 단일 앱 (기본)

```python
registry = ToolRegistry()
agent = Agent(name="bot", ..., tool_registry=registry)
```

### 멀티 앱 — 공유 Registry 백엔드

```python
class ToolRegistryBackend(Protocol):
    """저장소 백엔드 — 교체 가능."""
    async def list_tools(self, session_id: str | None) -> list[ToolSpec]: ...
    async def register(self, spec: ToolSpec, source: str, session_id: str | None): ...

class InMemoryBackend:   # 단일 프로세스 (기본값)
class RedisBackend:      # 멀티 앱 공유 (Redis pub/sub로 변경 알림)
```

```
┌──────────────────────────────────────┐
│     Shared ToolRegistry (Redis)       │
│  builtin + MCP + generated            │
└──────┬──────────┬──────────┬─────────┘
       │          │          │
  xgen-app-1  xgen-app-2  xgen-app-3
  (고객 상담)  (데이터 분석) (운영 자동화)
```

---

## 11. Tool Store — 도구 저장소/레지스트리

### 문제

```
현재:
  ToolGenerator → 도구 생성 → 로컬 Registry에만 등록
  → 세션 끝나면 사라짐
  → 다른 사람/앱이 가져다 쓸 방법 없음
  → 검증 없이 등록됨 (Sandbox 테스트만)

필요한 것:
  생성된 도구를 어딘가에 저장 → 검증 → 공개 → 다른 곳에서 가져다 씀
  npm/PyPI 같은 도구 전용 레지스트리
```

### 개념: Tool Store

```
도구 생명주기:

  생성 → 검증 → 게시 → 검색 → 설치 → 사용
   │       │       │       │       │       │
   │       │       │       │       │       └─ ToolRegistry.execute()
   │       │       │       │       └─ mantis install <tool-name>
   │       │       │       └─ mantis search "슬랙 메시지"
   │       │       └─ mantis publish <tool-name>
   │       └─ 자동 검증 파이프라인 (Sandbox 테스트 + 스키마 검증 + 보안 스캔)
   └─ ToolGenerator.create() 또는 수동 작성
```

### 저장소 구조

```
도구 = Python 파일 + 메타데이터
→ Git 저장소에 자연스럽게 맞음 (코드니까)

tool-store/
├── tools/
│   ├── send_slack_message/
│   │   ├── tool.py              ← @tool 데코레이터 코드
│   │   ├── test_tool.py         ← 테스트 코드
│   │   ├── manifest.json        ← 메타데이터 (이름, 설명, 버전, 의존성, 작성자)
│   │   └── README.md
│   ├── fetch_weather/
│   │   ├── tool.py
│   │   ├── test_tool.py
│   │   └── manifest.json
│   └── ...
├── categories.json              ← 카테고리 분류
└── verified.json                ← 검증 통과한 도구 목록
```

### manifest.json

```json
{
  "name": "send_slack_message",
  "version": "1.0.0",
  "description": "슬랙 채널에 메시지를 보내는 도구",
  "author": "jinsoo",
  "category": "communication",
  "tags": ["slack", "message", "notification"],
  "parameters": {
    "channel": {"type": "string", "description": "슬랙 채널"},
    "text": {"type": "string", "description": "메시지 내용"}
  },
  "dependencies": ["httpx"],
  "verified": true,
  "verification": {
    "sandbox_test": "passed",
    "schema_validation": "passed",
    "security_scan": "passed",
    "verified_at": "2026-03-19T10:00:00Z"
  }
}
```

### 검증 파이프라인

```
도구 게시 요청 (mantis publish)
  │
  ▼
Step 1: 스키마 검증
  → manifest.json 필수 필드 확인
  → parameters 타입/description 검증
  → @tool 데코레이터 존재 확인
  │
  ▼
Step 2: Sandbox 테스트
  → Docker 컨테이너에서 test_tool.py 실행
  → "ALL_TESTS_PASSED" 확인
  → 타임아웃, 메모리 제한 확인
  │
  ▼
Step 3: 보안 스캔
  → 위험한 import 감지 (os.system, subprocess, eval)
  → 네트워크 접근 패턴 확인
  → 파일 시스템 접근 범위 확인
  │
  ▼
Step 4: 등록
  → Git 저장소에 커밋 (또는 API 서버에 업로드)
  → verified.json 업데이트
  → 검색 인덱스 갱신
```

### ToolStore 클래스

```python
class ToolStore:
    """도구 저장소 — 도구를 게시하고 가져오는 레지스트리.

    저장소 백엔드:
    - GitBackend: Git 저장소 (GitHub, GitLab 등)
    - APIBackend: REST API 서버 (자체 호스팅)
    """

    def __init__(self, backend: StoreBackend):
        self.backend = backend

    async def publish(self, tool_dir: Path, registry: ToolRegistry) -> PublishResult:
        """도구를 저장소에 게시.

        1. manifest.json 로드 + 검증
        2. Sandbox 테스트 실행
        3. 보안 스캔
        4. 저장소에 업로드
        """
        manifest = load_manifest(tool_dir / "manifest.json")
        code = (tool_dir / "tool.py").read_text()
        test_code = (tool_dir / "test_tool.py").read_text()

        # 검증
        verification = await self._verify(code, test_code, manifest)
        if not verification.passed:
            return PublishResult(success=False, errors=verification.errors)

        # 게시
        await self.backend.upload(manifest["name"], tool_dir)
        return PublishResult(success=True, name=manifest["name"], version=manifest["version"])

    async def install(self, tool_name: str, registry: ToolRegistry) -> ToolSpec:
        """저장소에서 도구를 가져와 ToolRegistry에 등록."""
        tool_dir = await self.backend.download(tool_name)
        spec = registry.load_from_file(tool_dir / "tool.py")
        return spec

    async def search(self, query: str, category: str | None = None) -> list[ToolManifest]:
        """도구 검색."""
        return await self.backend.search(query, category=category)

    async def list_categories(self) -> list[str]:
        """카테고리 목록."""
        return await self.backend.list_categories()


class GitStoreBackend:
    """Git 저장소 기반 백엔드.

    GitHub/GitLab 레포를 도구 저장소로 사용.
    게시 = PR 생성, 검증 = CI 파이프라인, 설치 = git clone.
    """

    def __init__(self, repo_url: str, branch: str = "main"):
        self.repo_url = repo_url
        self.branch = branch

    async def upload(self, tool_name: str, tool_dir: Path):
        """PR 생성으로 도구 게시."""
        # git clone → 도구 복사 → commit → push → PR 생성
        ...

    async def download(self, tool_name: str) -> Path:
        """특정 도구 디렉토리만 다운로드."""
        # sparse checkout으로 tools/{tool_name}/ 만 가져옴
        ...

    async def search(self, query: str, **kwargs) -> list[ToolManifest]:
        """manifest.json 기반 검색."""
        ...


class APIStoreBackend:
    """REST API 기반 백엔드.

    자체 호스팅 도구 레지스트리 서버.
    """

    def __init__(self, base_url: str, api_key: str | None = None):
        self.base_url = base_url
        self.api_key = api_key

    async def upload(self, tool_name: str, tool_dir: Path): ...
    async def download(self, tool_name: str) -> Path: ...
    async def search(self, query: str, **kwargs) -> list[ToolManifest]: ...
```

### CLI 사용법

```bash
# 도구 검색
mantis store search "슬랙 메시지"
mantis store search --category communication

# 도구 설치
mantis store install send_slack_message

# 도구 게시
mantis store publish ./my_tools/send_slack_message/

# 도구 목록
mantis store list
mantis store list --category database

# 저장소 설정
mantis store config --backend git --repo https://github.com/myorg/tool-store.git
mantis store config --backend api --url https://tools.mycompany.com
```

### ToolGenerator → Tool Store 연동

```python
# 도구 생성 후 자동으로 Store에 게시하는 옵션
generator = ToolGenerator(
    llm=llm,
    registry=registry,
    sandbox=sandbox,
    store=ToolStore(GitStoreBackend("https://github.com/myorg/tool-store.git")),
    auto_publish=True,  # 생성 + 검증 통과 시 자동 게시
)

# 또는 수동 게시
spec = await generator.create("슬랙 메시지 보내는 도구")
await store.publish(spec.source_dir, registry)
```

### awesome-mcp-servers 스타일 카테고리

```
카테고리 예시:
  communication/    → 슬랙, 이메일, Teams, Discord
  database/         → SQL 쿼리, MongoDB, Redis
  file/             → 파일 읽기/쓰기, CSV 파싱, PDF 변환
  api/              → REST API 호출, GraphQL, webhook
  analysis/         → 데이터 분석, 통계, 시각화
  search/           → 웹 검색, 벡터 검색, 문서 검색
  automation/       → 스케줄링, 크롤링, 알림
  cloud/            → AWS, GCP, Azure 연동
  dev_tools/        → Git, CI/CD, 코드 리뷰
  custom/           → 사용자 정의
```

### 전체 흐름

```
ToolGenerator                    Tool Store                     다른 앱/사용자
  │                                │                                │
  │  create("슬랙 도구")           │                                │
  │  → LLM 코드생성               │                                │
  │  → Sandbox 검증               │                                │
  │  → ToolRegistry 등록           │                                │
  │                                │                                │
  │  publish(tool_dir)  ─────────→ │  검증 파이프라인               │
  │                                │  → 스키마 검증                 │
  │                                │  → Sandbox 테스트              │
  │                                │  → 보안 스캔                   │
  │                                │  → Git 저장소에 커밋           │
  │                                │                                │
  │                                │  ←──── search("슬랙")          │
  │                                │  ─────→ [send_slack_message]   │
  │                                │                                │
  │                                │  ←──── install("send_slack")   │
  │                                │  ─────→ tool.py 다운로드       │
  │                                │         ToolRegistry 등록      │
```

---

## 12. v2 패키지 구조

v1 대비 추가/변경 부분:

```
mantis/
├── __init__.py
├── __main__.py
│
├── tools/
│   ├── decorator.py          ← v1 그대로
│   └── registry.py           ← v2: session_id, source 추가
│
├── engine/
│   └── runner.py             ← v2: 매 iteration Registry 재조회
│
├── sandbox/
│   ├── sandbox.py            ← v1 그대로
│   ├── runner.py             ← v1 그대로
│   └── tools.py              ← ★ v2 신규: make_sandbox_tools()
│
├── generate/
│   └── tool_generator.py     ← v2: session_id 지원 + make_create_tool()
│
├── workflow/                  ← ★ v2 신규 패키지
│   ├── __init__.py
│   ├── engine.py             ← WorkflowEngine (from_canvas, from_code, from_llm)
│   ├── nodes.py              ← AgentNode, RouterNode, ToolNode, RAGNode
│   └── models.py             ← WorkflowNode, Edge, WorkflowEvent
│
├── store/                     ← ★ v2 신규 패키지
│   ├── __init__.py
│   ├── store.py              ← ToolStore (publish, install, search)
│   ├── backends.py           ← GitStoreBackend, APIStoreBackend
│   ├── manifest.py           ← ToolManifest 파싱/검증
│   └── verify.py             ← 검증 파이프라인 (스키마+Sandbox+보안)
│
├── pipeline/                 ← v1 그대로 (하위 호환)
├── llm/                      ← v1 그대로
├── context/                  ← v1 그대로
├── safety/                   ← v1 그대로
├── search/                   ← v1 그대로
├── state/                    ← v1 그대로
├── trace/                    ← v1 그대로
├── testing/                  ← v1 그대로
└── adapters/                 ← v1 그대로
```

---

## 12. 사용 이미지

### 가장 간단한 사용법 (v1과 동일)

```python
from mantis import Agent, tool, ToolRegistry
from mantis.llm.openai_provider import ModelClient

@tool(name="greet", description="인사한다")
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

### v2 풀옵션 — 도구 생성 + 샌드박스 + 워크플로우

```python
from mantis import Agent, tool, ToolRegistry
from mantis.llm.openai_provider import ModelClient
from mantis.sandbox.sandbox import DockerSandbox
from mantis.sandbox.tools import make_sandbox_tools
from mantis.generate.tool_generator import ToolGenerator, make_create_tool
from mantis.search.graph_search import GraphToolManager

# 1. 공유 Registry
registry = ToolRegistry()

# 2. 사용자 도구
@tool(name="lookup_order", description="주문 조회")
async def lookup_order(order_id: str) -> dict:
    return {"order_id": order_id, "status": "shipped"}
registry.register(lookup_order._tool_spec, source="builtin")

# 3. Sandbox 도구 등록 → Agent가 코드 실행 가능
sandbox = DockerSandbox()
for spec in make_sandbox_tools(sandbox):
    registry.register(spec, source="sandbox")

# 4. ToolGenerator + create_tool → Agent가 도구 생성 가능
llm = ModelClient(model="gpt-4o-mini", api_key="sk-...")
generator = ToolGenerator(llm=llm, registry=registry, sandbox=sandbox)
registry.register(make_create_tool(generator), source="builtin")

# 5. Graph 검색
graph = GraphToolManager()
graph.ingest_from_registry(registry)

# 6. Agent 생성 — 도구 현황:
#   - lookup_order (builtin)
#   - execute_code (sandbox)
#   - execute_code_with_test (sandbox)
#   - create_tool (builtin)
agent = Agent(
    name="full-agent",
    model_client=llm,
    tool_registry=registry,
    graph_tool_manager=graph,
    approval_patterns=["DELETE *"],
)

# Agent가 도구 만들고, 코드 돌리고, 즉시 사용 가능
async for event in agent.run_stream("슬랙 도구 만들어서 메시지 보내"):
    print(event)
```

### 워크플로우 사용

```python
from mantis.workflow import WorkflowEngine

# 캔버스 JSON에서 (xgen-workflow 호환)
engine = WorkflowEngine.from_canvas(workflow_data, registry)

# 또는 코드로 직접
engine = WorkflowEngine(registry)
engine.add_node(AgentNode(id="agent1", model="gpt-4o-mini"))
engine.add_edge(Edge("input", "result", "agent1", "text"))

async for event in engine.run({"text": "분석해줘"}):
    print(event)
```

---

## 13. v1 대비 개선점

| | v1 | v2 |
|---|---|---|
| **도구 목록** | Phase 2 스냅샷 (고정) | 매 iteration 최신 조회 (라이브) |
| **도구 생성 → 사용** | 다음 실행에서야 가능 | 같은 대화에서 즉시 사용 |
| **샌드박스** | ToolGenerator 내부 인프라 | Agent가 호출하는 도구 |
| **워크플로우** | 캔버스 JSON만 지원 | 캔버스 + 코드 + LLM 생성 |
| **세션 격리** | 없음 | session_id별 도구 관리 |
| **도구 소스 추적** | 없음 | builtin/generated/sandbox/mcp/openapi |
| **멀티 앱** | 단일 프로세스 | Redis 백엔드로 공유 가능 |
| **하위 호환** | — | v1 코드 그대로 동작 |

---

## 14. 실행 계획

### Phase A: ToolRegistry 확장 + Executor 동적 조회

- ToolRegistry에 session_id, source 파라미터 추가
- to_openai_tools(session_id) 글로벌+세션 합산
- ExecutePhase 매 iteration마다 Registry 재조회
- cleanup_session() 추가
- **효과: create_tool → 즉시 사용 해결**

### Phase B: Sandbox 도구화

- mantis/sandbox/tools.py 신규 (make_sandbox_tools)
- execute_code, execute_code_with_test @tool 함수
- **효과: Agent가 코드 실행/실험 가능**

### Phase C: ToolGenerator 정비

- session_id 전달 지원
- make_create_tool() 헬퍼
- _MOCK_PREAMBLE mantis 패키지용 갱신
- **효과: 도구 생성→검증→등록→즉시 사용 전체 파이프라인**

### Phase D: WorkflowEngine

- mantis/workflow/ 패키지 신규
- WorkflowEngine, WorkflowNode, Edge 구현
- from_canvas() 캔버스 호환
- AgentNode, RouterNode, ToolNode 구현
- from_llm() LLM 워크플로우 자동 생성
- **효과: xgen-workflow 실행기 대체**

### Phase E: 멀티 앱

- ToolRegistryBackend Protocol
- RedisBackend 구현
- **효과: xgen 앱 간 도구 공유**

### Phase F: Tool Store

- ToolStore, GitStoreBackend, APIStoreBackend 구현
- 검증 파이프라인 (스키마 + Sandbox + 보안 스캔)
- manifest.json 포맷 정의
- CLI: `mantis store search/install/publish`
- ToolGenerator → Store 자동 게시 옵션
- **효과: 도구를 저장소에 게시하고 다른 앱/팀이 가져다 씀**

---

## 15. 한 장 그림

```
┌────────────────────────────────────────────────────────────────────────┐
│                          Mantis v2 (라이브러리)                         │
│                                                                        │
│  pip install mantis                                                    │
│  from mantis import Agent, tool                                        │
│                                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐ │
│  │               ToolRegistry (하나의 인스턴스, 라이브)               │ │
│  │                                                                   │ │
│  │  builtin    sandbox       generated     MCP      OpenAPI         │ │
│  │  @tool()    execute_code  create_tool   mcp:     api_to_tool    │ │
│  │             execute_test  → 즉시 등록   slack                    │ │
│  │                                                                   │ │
│  │  to_openai_tools(session_id) → 매 iteration 최신 도구 반환       │ │
│  └───────────────┬──────────────────┬───────────────┬───────────────┘ │
│                   │                  │               │                 │
│  ┌────────────────▼──┐  ┌───────────▼───┐  ┌───────▼──────────┐     │
│  │    Executor       │  │ ToolGenerator │  │ WorkflowEngine   │     │
│  │                   │  │               │  │                  │     │
│  │  Think→Act        │  │ LLM 코드생성  │  │ from_canvas()    │     │
│  │  →Observe         │  │ Sandbox 검증  │  │ 코드 조립        │     │
│  │                   │  │ Registry 등록 │  │ from_llm()       │     │
│  │  매 iteration     │  │ → 즉시 사용   │  │ 조건 분기        │     │
│  │  도구 재조회      │  │               │  │ 루프백           │     │
│  └───────────────────┘  └───────────────┘  └──────────────────┘     │
│                                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐ │
│  │           DockerSandbox (인프라 + 도구)                            │ │
│  │  인프라: ToolGenerator 검증, ToolTester smoke_test                 │ │
│  │  도구: execute_code, execute_code_with_test → Agent가 호출        │ │
│  └──────────────────────────────────────────────────────────────────┘ │
│                                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐ │
│  │              Phase Pipeline (v1 호환)                              │ │
│  │  PREPARE → RESOLVE → EXECUTE → STREAM → PERSIST                  │ │
│  │                        ↑ ToolRegistry.to_openai_tools() 매 iter  │ │
│  └──────────────────────────────────────────────────────────────────┘ │
│                                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐ │
│  │              Adapters (이식 레이어)                                 │ │
│  │  WorkflowAdapter → 캔버스 JSON을 Phase 파이프라인으로 변환        │ │
│  │  SSEAdapter      → StreamEvent → SSE 포맷                        │ │
│  └──────────────────────────────────────────────────────────────────┘ │
└───────────────────────────────┬────────────────────────────────────────┘
                                │
              ┌─────────────────┼─────────────────┐
              ▼                 ▼                 ▼
       xgen-workflow        xgen3.0            새 프로젝트
       (캔버스 유지,       (기존처럼           (pip install
        실행기만 교체)      사용)              해서 바로)
```
