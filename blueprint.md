# mantis — AI Agent 실행 엔진 라이브러리 설계 청사진

## 한줄 요약

> xgen3.0에서 만든 AI Agent 실행 로직을 **독립 라이브러리(mantis)**로 추출하여,
> 기존 xgen-workflow에 `pip install`로 이식하거나, 새 프로젝트에서 바로 쓸 수 있게 한다.
> **핵심 설계 철학: 실행 파이프라인에 논리적 단계(Phase)를 부여하여 확장성과 강건성을 확보한다.**

---

## 1. 왜 만드는가

### 기존 xgen-workflow executor의 근본 문제

**"로직에 구조적 순서가 없다. 누더기로 끼어있다."**

```
execution_core.py (694줄) — 한 함수 안에 9가지 관심사:

  1. workflow_data 로드                    ← DB 로직
  2. 파일 선택 적용                        ← 전처리
  3. 테이블 MCP 처리                       ← 전처리
  4. bypass 적용                           ← 전처리
  5. executor 생성                         ← 실행
  6. 스트리밍 반복하면서:
     6-a. [AGENT_EVENT] 태그 정규식 파싱   ← 파싱
     6-b. [AGENT_STATUS] 태그 파싱         ← 파싱
     6-c. 멀티 Agent 마커 처리             ← 파싱
     6-d. IO 살균                          ← 후처리
     6-e. 에러 메시지 치환                 ← 후처리
     6-f. chunk 카운트해서 스트리밍 판단    ← 판단
     6-g. summary 생성 여부 결정           ← 판단
  7. ExecutionIO DB 저장                   ← DB
  8. Redis 세션 업데이트                   ← 상태
  9. Trace flush                           ← 추적

→ "전처리 → 실행 → 후처리" 같은 논리적 단계가 없음
→ 어디에 뭘 끼워넣을지 알 수 없음
→ 확장하려면 누더기 사이에 코드를 끼워야 함
```

```
async_workflow_executor.py (1088줄) — 하나의 for 루프 안에 전부:

  - DAG 정렬
  - 노드 순회
  - 입력 수집 (엣지 매핑)
  - 파라미터 주입
  - Agent 노드 특수 처리 (interaction_id, trace 주입)
  - 노드 실행
  - Agent 출력 파싱 (Generator 소비 + 이벤트 추출)
  - Router 분기 처리
  - EndNode 처리 + DB 저장
  - Generator 복제 (BufferedGeneratorFactory / tee)
  - 출력 포트 활성화 판단
  - 비활성 경로 제외
  - 취소 확인, 로그 전송, 상태 이벤트, 메모리 정리

→ 구조적 단계가 아니라 if-elif 분기로 끼워넣은 것
→ 총 4500줄 중 ~2500줄이 "sync→async 변환" + "문자열 태그 파싱" + "Generator 복제"
```

### 추가 문제점

| 문제 | 현재 상태 |
|------|----------|
| LangChain 강결합 | Agent 실행이 LangGraph 블랙박스, 떼어낼 수 없음 |
| 도구 검색 없음 | 도구 30개를 전부 LLM에 넘김 → 토큰 낭비 + 정확도 저하 |
| 승인 워크플로우 없음 | 위험 액션(DELETE, 외부 메시지) 그냥 실행됨 |
| 샌드박스 없음 | 코드 격리 실행 불가 |
| 실패 재개 없음 | 에러 시 처음부터 다시 |
| 디버깅 불가 | LangChain 콜백 5겹 → 에러 추적 불가 |
| 재사용 불가 | xgen-workflow에 갇혀 있음 |

---

## 2. 핵심 설계 철학 — Phase 기반 파이프라인

### 기존: 순서 없는 누더기

```
한 함수 안에서 전부 처리:
  로드 → 전처리 → 실행 → 파싱 → 후처리 → 저장
  (경계 없이 if-elif로 분기)
```

### 목표: 논리적 Phase로 분리

```
Phase 1: PREPARE     전처리 — 워크플로우 로드, 도구 수집, 컨텍스트 조립
Phase 2: RESOLVE     결정 — 도구 검색, 입력 매핑, 스키마 결정
Phase 3: EXECUTE     실행 — Think→Act→Observe 루프, 도구 호출
Phase 4: STREAM      전달 — 실행 이벤트 → SSE 변환 → 클라이언트
Phase 5: PERSIST     저장 — DB, Trace, 세션 상태, 리소스 정리
```

각 Phase가 **독립적**이라:
- **확장**: 새 기능은 해당 Phase에만 추가 (approval → Phase 3, graph 검색 → Phase 2)
- **강건성**: Phase 3 실패해도 Phase 5(저장)는 실행됨
- **테스트**: Phase별 단위 테스트 가능
- **디버깅**: "어느 Phase에서 문제인지" 즉시 파악

---

## 3. Phase 상세 설계

### Phase 1: PREPARE (전처리)

```
입력: WorkflowRequest (또는 단순 문자열 메시지)
출력: ExecutionContext (실행에 필요한 모든 것이 준비된 상태)

하는 일:
  1. workflow_data 로드 (DB 또는 직접 전달)
  2. 파일 선택, bypass, MCP 적용
  3. DAG 정렬 → 실행 순서 결정 (캔버스 모드)
  4. 또는 단순 Agent 모드 (DAG 없이 직접 실행)
  5. 세션 초기화 (session_id 생성 또는 복구)
  6. Trace 시작

코드:
  context = await pipeline.prepare(request)
```

```python
class PreparePhase:
    async def run(self, request: ExecutionRequest) -> ExecutionContext:
        # 워크플로우 모드
        if request.workflow_data:
            nodes = parse_nodes(request.workflow_data)
            edges = parse_edges(request.workflow_data)
            execution_order = topological_sort(nodes, edges)
            return ExecutionContext(mode="workflow", order=execution_order, ...)

        # Agent 직접 모드
        return ExecutionContext(mode="agent", message=request.input_data, ...)
```

### Phase 2: RESOLVE (결정)

```
입력: ExecutionContext
출력: ResolvedContext (도구, RAG, 메모리, 스키마가 결정된 상태)

하는 일:
  1. Agent 노드에 연결된 도구 수집
  2. graph-tool-call로 관련 도구 검색 (도구 많을 때)
  3. RAG 컨텍스트 수집 (Qdrant 등) - check 피룡 
  4. 대화 메모리 로드 (DB Memory)
  5. 입출력 스키마 결정
  6. 시스템 프롬프트 조립

코드:
  resolved = await pipeline.resolve(context)
```

```python
class ResolvePhase:
    async def run(self, ctx: ExecutionContext) -> ResolvedContext:
        # 도구 수집
        tools = self.collect_tools(ctx)

        # 도구 검색 (선택 — GraphToolSearch 있을 때만)
        if self.search and len(tools) > threshold:
            tools = self.search.find(ctx.message, tools, top_k=10)

        # RAG (캔버스에서 Qdrant 노드가 연결된 경우)
        rag_context = await self.collect_rag(ctx)

        # 메모리
        memory = await self.collect_memory(ctx)

        return ResolvedContext(tools=tools, rag=rag_context, memory=memory, ...)
```

### Phase 3: EXECUTE (실행)

```
입력: ResolvedContext
출력: AsyncGenerator[ExecutionEvent] (실행 이벤트 스트림)

하는 일:
  워크플로우 모드:
    1. 노드 순차 실행
    2. Agent 노드 → Think→Act→Observe 루프
    3. Router 노드 → 분기 결정
    4. EndNode → 최종 출력

  Agent 직접 모드:
    1. Think→Act→Observe 루프
    2. 각 단계에서 ExecutionEvent yield

  공통 (Agent 루프 내부):
    1. LLM 호출 (Think)
    2. 도구 이름 교정 (validate)
    3. 승인 체크 (approval)
    4. 도구 실행 (Act)
    5. 결과 피드백 (Observe)
    6. 체크포인트 저장

코드:
  async for event in pipeline.execute(resolved):
      yield event
```

```python
class ExecutePhase:
    async def run(self, ctx: ResolvedContext) -> AsyncGenerator[ExecutionEvent]:

        for iteration in range(max_iterations):
            # Think
            yield ExecutionEvent("thinking", {"iteration": iteration})
            response = await self.llm.chat(ctx.messages, ctx.tools)

            # 종료
            if not response.tool_calls:
                yield ExecutionEvent("done", {"text": response.text})
                return

            # Act
            for tc in response.tool_calls:
                # 교정
                tc = self.validate(tc)

                # 승인
                if self.approval and self.approval.needs(tc):
                    yield ExecutionEvent("approval_required", tc.to_dict())
                    decision = await self.approval.wait()
                    if not decision.approved:
                        continue

                # 실행
                yield ExecutionEvent("tool_call", {"name": tc.name, "args": tc.args})
                result = await self.tools.execute(tc)
                yield ExecutionEvent("tool_result", {"name": tc.name, "result": result})

                # 피드백
                ctx.messages.add_tool_result(tc, result)

            # 체크포인트
            if self.state:
                await self.state.checkpoint(ctx.session_id, ctx.to_state())
```

### Phase 4: STREAM (이벤트 전달)

```
입력: AsyncGenerator[ExecutionEvent]
출력: AsyncGenerator[StreamEvent] (클라이언트용 포맷)

하는 일:
  1. ExecutionEvent → StreamEvent 변환
  2. SSE 포맷 생성
  3. 또는 xgen-workflow 호환 포맷 생성 (WorkflowAdapter)

코드:
  async for stream_event in pipeline.stream(execution_events):
      yield stream_event.to_sse()
```

```python
class StreamPhase:
    async def run(self, events: AsyncGenerator[ExecutionEvent]) -> AsyncGenerator[StreamEvent]:
        async for event in events:
            yield self.adapter.convert(event)
        yield StreamEvent("end", {"message": "Stream finished"})

# 어댑터별 변환:
class SSEAdapter:       # → event: type\ndata: json\n\n
class WorkflowAdapter:  # → {"type": "tool", "data": {...}} (기존 호환)
class JSONAdapter:      # → {"events": [...]} (배치 응답)
```

### Phase 5: PERSIST (저장)

```
입력: 실행 결과 + 트레이스 데이터
출력: 없음 (사이드이펙트: DB 저장)

하는 일:
  1. ExecutionIO DB 저장 (입출력 기록)
  2. Trace flush (실행 흐름 기록)
  3. 세션 상태 업데이트 (completed/error)
  4. 리소스 정리 (Generator close, 메모리 해제)

코드:
  await pipeline.persist(result, trace)
```

```python
class PersistPhase:
    async def run(self, result: ExecutionResult):
        if self.db:
            await self.db.save_execution_io(result.input, result.output)
        if self.trace:
            await self.trace.flush()
        if self.session:
            await self.session.update_status("completed")
        self.cleanup()
```

### 전체 파이프라인 조합

```python
class ExecutionPipeline:
    """Phase 기반 실행 파이프라인 — 논리적 순서가 보장됨."""

    def __init__(
        self,
        prepare: PreparePhase,
        resolve: ResolvePhase,
        execute: ExecutePhase,
        stream: StreamPhase,
        persist: PersistPhase,
    ): ...

    async def run(self, request: ExecutionRequest) -> AsyncGenerator[StreamEvent]:
        # Phase 1
        context = await self.prepare.run(request)

        # Phase 2
        resolved = await self.resolve.run(context)

        # Phase 3 + Phase 4 (파이프라인으로 연결)
        execution_events = self.execute.run(resolved)

        try:
            async for stream_event in self.stream.run(execution_events):
                yield stream_event
        finally:
            # Phase 5 (항상 실행 — 에러가 나도)
            await self.persist.run(context.result)
```

---

## 4. Phase에 우리 기능이 끼는 지점

| Phase | 기존 (누더기) | mantis (구조적) |
|-------|-------------|----------------|
| **PREPARE** | execution_core.py 상단에 산재 | PreparePhase 클래스로 분리 |
| **RESOLVE** | 없음 (도구 전체를 넘김) | **graph-tool-call 검색**, RAG 수집, 메모리 로드 |
| **EXECUTE** | LangChain 블랙박스 | **Think→Act→Observe 직접 루프** |
| └─ 교정 | 없음 | **validate_tool_call** (이름 오타 교정) |
| └─ 승인 | 없음 | **ApprovalManager** (위험 액션 차단) |
| └─ 실행 | LangChain ToolExecutor | **ToolRegistry.execute()** (직접 호출) |
| └─ 샌드박스 | 없음 | **DockerSandbox** (코드 격리) |
| └─ 도구 생성 | 없음 | **ToolGenerator** (AI 코드생성→테스트→등록) |
| └─ 체크포인트 | 없음 | **StateStore** (실패 재개) |
| └─ **테스트** | 없음 | **ToolTester** (스모크/assert/pytest 검증) |
| **STREAM** | [AGENT_EVENT] 문자열 파싱 | **구조화 이벤트 직접 반환** (파싱 불필요) |
| **PERSIST** | execution_core.py 하단에 산재 | PersistPhase 클래스로 분리 |

---

## 5. 도구 테스트 — pytest/assert가 끼는 구조

### 문제: 깨진 도구가 LLM에 전달되면?

```
현재:
  도구 등록 → 검증 없음 → LLM이 호출 → 런타임 에러
  AI가 만든 도구 → 검증 없음 → 등록 → 호출 시에야 에러 발견
  OpenAPI에서 가져온 도구 → 스키마 검증 없음 → 파라미터 불일치

→ 깨진 도구가 LLM에 전달되는 걸 원천 차단해야 함
```

### 테스트가 끼는 3개 지점

```
Phase 2 (RESOLVE) — 도구를 LLM에 전달하기 전
  │
  ├─ @tool 도구 등록 시: smoke_test
  │   → 더미 값으로 호출 → dict 반환하는지 → 통과해야 LLM에 전달
  │
  ├─ OpenAPI 도구: schema_validate
  │   → 파라미터 타입, 필수값, URL 유효성 검증
  │
  └─ MCP 도구: schema_validate
     → inputSchema 유효성 검증

Phase 3 (EXECUTE) — 도구 생성 시
  │
  └─ create_tool 호출 시: ToolGenerator 내부
     ├─ Sandbox 1차: 문법 검증 (SYNTAX_OK)
     ├─ Sandbox 2차: 기능 테스트 (ALL_TESTS_PASSED)
     │   → assert 기반 또는 pytest 실행
     └─ 통과해야만 Registry에 등록

Phase 5 (PERSIST) — 저장 전
  │
  └─ 실행 결과 무결성: trace 데이터 완전성 검증
```

### ToolTester 클래스

```python
class ToolTester:
    """도구 품질 게이트 — 깨진 도구가 LLM에 전달되는 걸 차단."""

    def __init__(self, sandbox: Sandbox | None = None):
        self.sandbox = sandbox

    async def smoke_test(self, spec: ToolSpec) -> TestResult:
        """스모크 테스트 — 더미 값으로 호출, dict 반환하는지 확인.

        @tool 도구 등록 시 자동 실행.
        샌드박스 있으면 격리 실행, 없으면 직접 호출.
        """
        dummy_args = generate_dummy_args(spec.parameters)
        # {"order_id": "test_string", "count": 0, "flag": False}

        if self.sandbox:
            result = await self.sandbox.run(f"""
                {mock_preamble}
                {spec.source_code}
                import asyncio
                result = asyncio.run({spec.fn_name}(**{dummy_args}))
                assert isinstance(result, dict), f"dict 아님: {{type(result)}}"
                print("SMOKE_OK")
            """)
            return TestResult(passed="SMOKE_OK" in result.stdout, output=result)
        else:
            result = await spec.execute(**dummy_args)
            return TestResult(passed=isinstance(result, dict))

    async def run_assert_tests(self, code: str, test_code: str) -> TestResult:
        """assert 기반 테스트 — AI가 생성한 테스트 코드 실행.

        create_tool 파이프라인에서 사용.
        """
        script = mock_preamble + code + "\n\n" + test_code
        result = await self.sandbox.run(script, timeout=30)
        return TestResult(
            passed="ALL_TESTS_PASSED" in result.stdout,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    async def run_pytest(self, code: str, test_code: str) -> TestResult:
        """pytest 실행 — 상세 리포트 포함.

        고급 테스트가 필요할 때 (fixture, parametrize 등).
        """
        pytest_script = f"""
            {mock_preamble}
            {code}

            {test_code}
        """
        result = await self.sandbox.run(
            code=pytest_script,
            pip_packages=["pytest"],
            command="python -m pytest -v --tb=short",
            timeout=60,
        )
        return TestResult(
            passed=result.exit_code == 0,
            stdout=result.stdout,
            stderr=result.stderr,
            report=parse_pytest_output(result.stdout),
        )

    def validate_schema(self, spec: ToolSpec) -> TestResult:
        """스키마 검증 — 파라미터 타입, 필수값, description 체크.

        OpenAPI/MCP 도구에 사용. 샌드박스 불필요.
        """
        errors = []
        for name, param in spec.parameters.items():
            if not param.get("type"):
                errors.append(f"파라미터 '{name}'에 type 없음")
            if not param.get("description"):
                errors.append(f"파라미터 '{name}'에 description 없음")
        if not spec.description:
            errors.append("도구 description 없음")
        return TestResult(passed=len(errors) == 0, errors=errors)
```

### 테스트 수준 3단계

```
Level 1: schema_validate (빠름, 샌드박스 불필요)
  → 파라미터 타입/필수값/description 존재 여부
  → OpenAPI/MCP 도구 등록 시 자동 실행
  → 0ms

Level 2: smoke_test (중간, 샌드박스 선택)
  → 더미 값으로 실제 호출 → dict 반환 확인
  → @tool 도구 등록 시 자동 실행
  → ~500ms (샌드박스) 또는 ~10ms (직접)

Level 3: run_assert_tests / run_pytest (느림, 샌드박스 필수)
  → AI 생성 테스트 코드로 기능 검증
  → create_tool 시 자동 실행
  → ~2-5초
```

### Phase 파이프라인에서의 동작

```python
class ResolvePhase:
    async def run(self, ctx: ExecutionContext) -> ResolvedContext:
        tools = self.collect_tools(ctx)

        # ★ 도구 검증 게이트
        if self.tester:
            verified_tools = []
            for tool in tools:
                if tool.source == "openapi" or tool.source == "mcp":
                    result = self.tester.validate_schema(tool)
                else:
                    result = await self.tester.smoke_test(tool)

                if result.passed:
                    verified_tools.append(tool)
                else:
                    logger.warning("도구 '%s' 검증 실패, LLM에 전달 안 함: %s", tool.name, result.errors)

            tools = verified_tools

        # 도구 검색
        if self.search:
            tools = self.search.find(ctx.message, tools, top_k=10)

        return ResolvedContext(tools=tools, ...)


class ExecutePhase:
    async def run(self, ctx: ResolvedContext) -> AsyncGenerator[ExecutionEvent]:
        for iteration in range(max_iterations):
            response = await self.llm.chat(ctx.messages, ctx.tools)

            for tc in response.tool_calls:
                if tc.name == "create_tool":
                    # ★ AI 도구 생성 — 테스트 포함 파이프라인
                    gen_result = await self.tool_generator.create(tc.args["description"])
                    # 내부에서 tester.run_assert_tests() 실행됨
                    # 통과해야만 Registry에 등록
                    yield ExecutionEvent("tool_result", gen_result)
                else:
                    result = await self.tools.execute(tc)
                    yield ExecutionEvent("tool_result", result)
```

### 패키지 구조에 추가

```
mantis/
├── ...
├── testing/                    ← 도구 품질 검증
│   ├── __init__.py             ← ToolTester, TestResult
│   ├── tool_tester.py          ← ToolTester (smoke/assert/pytest/schema)
│   ├── dummy_args.py           ← 파라미터 타입별 더미 값 생성
│   └── pytest_runner.py        ← 샌드박스 안에서 pytest 실행 + 출력 파싱
├── sandbox/
│   └── sandbox.py
└── ...
```

---

## 6. 캔버스 워크플로우와의 연결

### 캔버스가 하는 일 (유지)

사용자가 노코드로 조합하는 RAG 파이프라인:

```
[입력 스키마]──→[Qdrant 검색]──→[Agent]──→[출력 스키마]──→[Print]
[DB Memory]────→[Agent]
[API Tool x5]──→[Agent]
[MCP Slack]────→[Agent]
```

### 캔버스 노드 → Phase 매핑

```
캔버스 노드            어떤 Phase에서 처리되는지
────────────           ────────────────────────
입력 스키마 노드    →   Phase 1 (PREPARE) — 입력 파싱
Qdrant 검색 노드   →   Phase 2 (RESOLVE) — RAG 컨텍스트 수집
DB Memory 노드     →   Phase 2 (RESOLVE) — 메모리 로드
API Tool 노드      →   Phase 2 (RESOLVE) — 도구 수집 + 검색
MCP 노드           →   Phase 2 (RESOLVE) — 도구 수집
Agent 노드         →   Phase 3 (EXECUTE) — Think→Act→Observe 루프
Router 노드        →   Phase 3 (EXECUTE) — 분기 결정
출력 스키마 노드   →   Phase 4 (STREAM) — 응답 포맷팅
Print 노드         →   Phase 4 (STREAM) — 클라이언트 전달
DB 저장            →   Phase 5 (PERSIST) — 결과 저장
```

**캔버스 UI는 변경 없음. 내부 실행 파이프라인만 Phase 기반으로 재구성.**

### WorkflowAdapter — 캔버스 JSON → Phase 파이프라인 변환

```python
class WorkflowAdapter:
    """캔버스 워크플로우 JSON을 Phase 파이프라인으로 변환."""

    def from_workflow(self, workflow_data: dict) -> ExecutionPipeline:
        # 캔버스의 각 노드를 해당 Phase의 provider로 변환

        for node in workflow_data["nodes"]:
            if node.type == "qdrant_search":
                # → Phase 2 (RESOLVE)의 RAG provider로 등록
                pipeline.resolve.add_rag_provider(
                    QdrantProvider(collection=node.params["collection"], top_k=node.params["top_k"])
                )

            elif node.type == "db_memory":
                # → Phase 2 (RESOLVE)의 memory provider로 등록
                pipeline.resolve.add_memory_provider(
                    DBMemoryProvider(limit=node.params["limit"])
                )

            elif node.type == "api_tool":
                # → Phase 2 (RESOLVE)의 도구로 등록
                pipeline.resolve.add_tool(
                    api_to_tool(node.params["url"], node.params["method"])
                )

            elif node.type == "agents":
                # → Phase 3 (EXECUTE)의 Agent 설정
                pipeline.execute.configure(
                    model=node.params["model"],
                    system_prompt=node.params["prompt"],
                    temperature=node.params["temperature"],
                )

            elif node.type == "schema_output":
                # → Phase 4 (STREAM)의 출력 포맷
                pipeline.stream.set_output_schema(node.params["fields"])

        return pipeline
```

---

## 6. 모듈 분해

### 11개 독립 모듈

| # | 모듈 | Phase | 하는 일 | 의존성 |
|---|------|-------|---------|--------|
| 1 | **LLM 호출** | EXECUTE | OpenAI 호환 API 호출 | httpx |
| 2 | **@tool + Registry** | RESOLVE/EXECUTE | 도구 정의/등록/실행 | 없음 |
| 3 | **도구 검색** | RESOLVE | 쿼리→관련 도구 N개 | graph-tool-call |
| 4 | **이름 교정** | EXECUTE | fuzzy matching 자동 교정 | graph-tool-call |
| 5 | **승인** | EXECUTE | 위험 액션 차단/승인 | 없음 |
| 6 | **샌드박스** | EXECUTE | Docker 격리 코드 실행 | docker |
| 7 | **도구 생성** | EXECUTE | 코드생성→테스트→등록 | LLM+Sandbox+Registry |
| 8 | **컨텍스트** | PREPARE/EXECUTE | messages 배열 관리 | 없음 |
| 9 | **상태 저장** | EXECUTE/PERSIST | 체크포인트/재개 | DB |
| 10 | **트레이싱** | 전 Phase | 실행 흐름 기록 | 없음 |
| 11 | **도구 테스트** | RESOLVE/EXECUTE | 스모크/assert/pytest/스키마 검증 | Sandbox(선택) |
| 12 | **AgentRunner** | 전체 조합 | Phase 파이프라인 실행기 | 1+2+8 필수 |

### 모듈 간 의존 관계

```
ExecutionPipeline (Phase 조합기)
  │
  ├─ Phase 1 (PREPARE)
  │   └─ ConversationContext, StateStore(복구)
  │
  ├─ Phase 2 (RESOLVE)
  │   └─ ToolRegistry, GraphToolSearch, RAG/Memory providers
  │
  ├─ Phase 3 (EXECUTE) — 핵심
  │   ├─ 필수: ModelClient, ToolRegistry, ConversationContext
  │   └─ 선택: GraphToolSearch(교정), ApprovalManager, Sandbox, ToolGenerator, StateStore(체크포인트)
  │
  ├─ Phase 4 (STREAM)
  │   └─ SSEAdapter | WorkflowAdapter | JSONAdapter
  │
  └─ Phase 5 (PERSIST)
      └─ DB, TraceCollector, StateStore(상태)
```

---

## 7. 사용 이미지

### 가장 간단한 사용법

```python
from mantis import AgentRunner, tool

@tool(name="lookup_order", description="주문 ID로 주문 상태를 조회한다")
async def lookup_order(order_id: str) -> dict:
    return {"order_id": order_id, "status": "배송중"}

runner = AgentRunner(model="gpt-4o-mini", api_key="sk-...")
runner.add_tool(lookup_order)

result = await runner.run("주문 ABC-123 상태 알려줘")
print(result.text)
```

### 풀옵션 (모든 Phase 활성)

```python
from mantis import AgentRunner, tool, ToolRegistry
from mantis.search import GraphToolSearch
from mantis.sandbox import Sandbox
from mantis.safety import ApprovalManager
from mantis.trace import TraceCollector
from mantis.state import StateStore

runner = AgentRunner(
    model="gpt-4o-mini",
    api_key="sk-...",
    tools=my_registry,
    search=GraphToolSearch(),                    # Phase 2: 도구 검색
    sandbox=Sandbox(),                           # Phase 3: 코드 격리
    approval=ApprovalManager(["DELETE *"]),       # Phase 3: 승인
    trace=TraceCollector(),                       # 전 Phase: 트레이싱
    state=StateStore(db_url="postgresql://..."),  # Phase 3+5: 체크포인트
)

async for event in runner.stream("주문 조회해줘"):
    print(event.type, event.data)
```

### 모듈별 독립 사용

```python
# 도구 검색만
from mantis.search import GraphToolSearch
search = GraphToolSearch()
search.add_tools(my_tools)
results = search.find("주문 조회", top_k=5)

# 샌드박스만
from mantis.sandbox import Sandbox
result = await Sandbox().run("print(sum(range(1, 101)))")

# AI 도구 생성만
from mantis.generate import ToolGenerator
gen = ToolGenerator(model="gpt-4o-mini", api_key="sk-...")
result = await gen.create("두 숫자의 합을 구하는 도구")
```

### xgen-workflow에 이식

```python
# repos/xgen-workflow/editor/nodes/xgen/agent/agent_core.py
from mantis import AgentRunner
from mantis.adapters import WorkflowAdapter

def _execute_streaming(self):
    runner = AgentRunner(
        model=self.config.model,
        api_key=self.config.api_key,
        tools=self._existing_tools,
        search=GraphToolSearch(),
        sandbox=Sandbox(),
    )
    adapter = WorkflowAdapter(runner)
    yield from adapter.execute(user_input)
```

---

## 8. 패키지 구조

```
mantis/
├── pyproject.toml
└── mantis/
    ├── __init__.py                 ← 공개 API (AgentRunner, tool, ToolRegistry)
    │
    ├── pipeline/                   ← Phase 파이프라인
    │   ├── pipeline.py             ← ExecutionPipeline (Phase 조합기)
    │   ├── phases.py               ← PreparePhase, ResolvePhase, ExecutePhase, StreamPhase, PersistPhase
    │   ├── context.py              ← ExecutionContext, ResolvedContext
    │   └── events.py               ← ExecutionEvent, StreamEvent
    │
    ├── engine/                     ← 실행 엔진 (Phase 3의 핵심)
    │   ├── runner.py               ← AgentRunner (Think→Act→Observe 루프)
    │   └── config.py               ← RunnerConfig
    │
    ├── tools/                      ← 도구 시스템
    │   ├── decorator.py            ← @tool 데코레이터
    │   └── registry.py             ← ToolRegistry (등록/조회/실행)
    │
    ├── llm/                        ← LLM 추상화
    │   ├── protocol.py             ← LLMProvider (Protocol)
    │   ├── openai_provider.py      ← OpenAI 호환 구현
    │   └── response.py             ← LLMResponse, ToolCall
    │
    ├── search/                     ← 도구 검색 [선택 설치]
    │   └── graph_search.py         ← GraphToolSearch (검색 + 분류 + 교정)
    │
    ├── sandbox/                    ← 코드 격리 [선택 설치]
    │   └── sandbox.py              ← Sandbox (Docker 컨테이너)
    │
    ├── generate/                   ← AI 도구 생성 [선택]
    │   └── tool_generator.py       ← ToolGenerator (코드생성→테스트→등록)
    │
    ├── safety/                     ← 승인 워크플로우
    │   └── approval.py             ← ApprovalManager
    │
    ├── state/                      ← 상태 저장/복구 [선택]
    │   └── store.py                ← StateStore (체크포인트/재개)
    │
    ├── context/                    ← 대화 컨텍스트
    │   └── conversation.py         ← ConversationContext
    │
    ├── trace/                      ← 관찰 가능성 [선택]
    │   ├── collector.py            ← TraceCollector
    │   └── exporter.py             ← TraceExporter (Protocol)
    │
    ├── testing/                    ← 도구 품질 검증
    │   ├── tool_tester.py          ← ToolTester (smoke/assert/pytest/schema)
    │   ├── dummy_args.py           ← 파라미터 타입별 더미 값 생성
    │   └── pytest_runner.py        ← 샌드박스 안에서 pytest 실행 + 출력 파싱
    │
    └── adapters/                   ← 이식 레이어
        ├── workflow_adapter.py     ← xgen-workflow 호환 (캔버스 JSON → Phase)
        ├── sse_adapter.py          ← SSE 이벤트 포맷 변환
        └── fastapi_adapter.py      ← FastAPI 라우터 즉시 생성
```

---

## 9. 의존성 전략

```toml
[project]
name = "mantis"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["httpx>=0.27"]  # 유일한 필수 의존성

[project.optional-dependencies]
search = ["graph-tool-call>=0.13"]
sandbox = ["docker>=7.0"]
all = ["graph-tool-call>=0.13", "docker>=7.0"]
```

```bash
pip install mantis              # 기본 (Phase 파이프라인 + Agent 루프 + @tool)
pip install mantis[search]      # + 도구 검색 (Phase 2 강화)
pip install mantis[sandbox]     # + Docker 샌드박스 (Phase 3 강화)
pip install mantis[all]         # 전부
```

---

## 10. 기존 대비 개선점

| | 기존 (LangChain) | mantis |
|---|---|---|
| **구조** | 누더기 (한 함수에 9가지 관심사) | Phase 기반 파이프라인 (5단계 분리) |
| **확장** | 누더기 사이에 끼워넣기 | 해당 Phase에만 추가 |
| **강건성** | 한 곳 실패 → 전체 실패 | Phase별 독립 (실패해도 Persist 실행) |
| **도구 100개** | 컨텍스트 터짐 | graph 검색으로 10개만 전달 |
| **새 도구** | 개발자 코드 작성 (며칠) | AI 자동 생성 (1분) |
| **위험 액션** | 그냥 실행됨 | 승인 워크플로우 |
| **의존성** | LangChain 100+패키지 | httpx 1개 |
| **실패 복구** | 처음부터 다시 | 체크포인트에서 재개 |
| **코드 실행** | 불가 또는 위험 | Docker 샌드박스 격리 |
| **디버깅** | 콜백 5겹 | while 루프, Phase별 로그 |
| **이벤트** | 문자열 태그 파싱 | 구조화 이벤트 직접 반환 |

---

## 11. xgen3.0 → mantis 코드 매핑

```
xgen3.0 (현재)                           mantis (라이브러리)
──────────────────                        ─────────────────
src/core/agent.py                    →   mantis/engine/runner.py + mantis/pipeline/phases.py
src/core/model_client.py             →   mantis/llm/openai_provider.py
src/core/context.py                  →   mantis/context/conversation.py
src/core/approval.py                 →   mantis/safety/approval.py
src/tools/decorator.py               →   mantis/tools/decorator.py
src/tools/registry.py                →   mantis/tools/registry.py
src/tools/graph_tool.py              →   mantis/search/graph_search.py
src/tools/generator.py               →   mantis/generate/tool_generator.py
src/sandbox/docker.py                →   mantis/sandbox/sandbox.py
src/store/state.py                   →   mantis/state/store.py
src/trace/collector.py               →   mantis/trace/collector.py
src/api/sse_adapter.py               →   mantis/adapters/sse_adapter.py
(신규)                                →   mantis/pipeline/pipeline.py (Phase 조합기)
(신규)                                →   mantis/pipeline/phases.py (5개 Phase)
(신규)                                →   mantis/testing/tool_tester.py (도구 품질 검증)
(신규)                                →   mantis/adapters/workflow_adapter.py (캔버스 연동)
```

---

## 12. 실행 계획

### Phase A: 라이브러리 추출 + Phase 구조화

- xgen3.0 핵심 모듈을 mantis 패키지로 추출
- ExecutionPipeline + 5개 Phase 클래스 설계
- pyproject.toml 작성, pip install 가능하게
- 기존 xgen3.0은 mantis을 import해서 사용하도록 전환
- 테스트 이식

### Phase B: 어댑터 작성

- WorkflowAdapter: 캔버스 JSON → Phase 파이프라인 변환
- 기존 이벤트 포맷 호환 (SSE, agent_event 등)
- LangChain Tool → @tool 브릿지

### Phase C: xgen-workflow 이식

- agent_core.py 교체 (LangChain → mantis AgentRunner)
- execution_core.py 단순화 (Phase 파이프라인으로 대체)
- 기존 테스트 전부 통과 확인
- xgen-son 서버에서 먼저 검증

### Phase D: 점진적 고도화

- graph-tool-call 검색 활성화
- Docker 샌드박스 활성화
- AI 도구 생성 활성화
- 고객사별 선택적 기능 on/off

---

## 13. 한 장 그림

```
┌────────────────────────────────────────────────────────────────┐
│                     mantis (라이브러리)                          │
│                                                                 │
│  pip install mantis                                             │
│  from mantis import AgentRunner, tool                           │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐ │
│  │              ExecutionPipeline (Phase 조합기)               │ │
│  │                                                            │ │
│  │  Phase 1        Phase 2        Phase 3        Phase 4     │ │
│  │  PREPARE   →   RESOLVE   →   EXECUTE   →   STREAM        │ │
│  │  세션 준비      도구 검색      Think→Act     SSE 변환      │ │
│  │  컨텍스트       RAG 수집       →Observe      이벤트 전달   │ │
│  │  DAG 정렬       메모리 로드    승인/교정/     포맷 변환    │ │
│  │                 스키마 결정    샌드박스                     │ │
│  │                                            Phase 5        │ │
│  │                                            PERSIST        │ │
│  │                                            DB 저장        │ │
│  │                                            Trace flush    │ │
│  └───────────────────────────────────────────────────────────┘ │
│                                                                 │
│  ┌────────┐ ┌──────────┐ ┌────────┐ ┌─────────┐ ┌──────┐     │
│  │ @tool  │ │GraphTool │ │Sandbox │ │ToolGen  │ │Appro-│     │
│  │Registry│ │ Search   │ │(Docker)│ │(AI생성) │ │val   │     │
│  └────────┘ └──────────┘ └────────┘ └─────────┘ └──────┘     │
│  (각 모듈이 해당 Phase에 독립적으로 끼워짐)                     │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐ │
│  │              Adapters (이식 레이어)                         │ │
│  │  WorkflowAdapter → 캔버스 JSON을 Phase 파이프라인으로 변환 │ │
│  │  SSEAdapter      → StreamEvent → SSE 포맷                 │ │
│  │  FastAPIAdapter  → 즉시 API 서버                           │ │
│  └───────────────────────────────────────────────────────────┘ │
└──────────────────────────┬─────────────────────────────────────┘
                           │
            ┌──────────────┼──────────────┐
            ▼              ▼              ▼
     xgen-workflow      xgen3.0        새 프로젝트
     (캔버스 유지,     (기존처럼       (pip install
      실행기만 교체)    사용)           해서 바로)
```
