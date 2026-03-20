# mantis — AI Agent 실행 엔진 라이브러리

pip install mantis로 설치 가능한 독립 라이브러리.
xgen3.0에서 추출한 핵심 로직을 라이브러리로 패키징.

## 프로젝트 한줄 요약

> 엔진은 Generator + Executor 두 개뿐. 나머지는 전부 도구.
> Agent의 Think→Act→Observe 루프 하나가 곧 파이프라인.
> 미들웨어로 횡단 관심사(승인, 트레이싱, 상태 저장)를 자동 처리.

## 핵심 설계 (v3)

- Everything is a Tool: 워크플로우, 샌드박스, 검색 모두 도구로 노출
- 엔진 = Agent(루프) + ToolGenerator(도구 생성) + WorkflowGenerator(워크플로우 생성) + ToolRegistry/WorkflowStore(저장소)
- 미들웨어 체인: on_start → on_before_llm → on_before_tool → on_after_tool → on_end
- LLMProvider Protocol 기반 — 아무 LLM 구현이나 끼울 수 있음
- make_*_tools() 팩토리 패턴으로 인프라를 도구로 변환
- 상세 설계는 blueprint-v3.md 참조 (v1: blueprint.md, v2: blueprint-v2.md)

## 기술 스택

- Python 3.11+, async/await 기반
- 필수 의존성: httpx만
- 선택: graph-tool-call (도구 검색), docker (샌드박스), asyncpg (상태 저장)

## 패키지 구조

```
mantis/
├── engine/         ← Agent (Think→Act→Observe + 미들웨어 체인)
├── tools/          ← @tool 데코레이터 + ToolRegistry + meta 도구
├── llm/            ← LLMProvider Protocol + ModelClient (OpenAI 호환)
├── middleware/     ← Approval, Trace, GraphSearch, AutoCorrect, State
├── workflow/       ← WorkflowDef, Store, Runner, Generator, 도구 4개
├── generate/       ← ToolGenerator + make_create_tool()
├── sandbox/        ← DockerSandbox + make_sandbox_tools()
├── search/         ← GraphToolManager (graph-tool-call)
├── safety/         ← ApprovalManager (미들웨어가 래핑)
├── state/          ← StateStore (미들웨어가 래핑)
├── context/        ← ConversationContext
├── trace/          ← TraceCollector (미들웨어가 래핑)
├── testing/        ← ToolTester (smoke/assert/pytest)
├── adapters/       ← SSE 변환 + 캔버스 JSON 변환
└── exceptions.py   ← MantisError 예외 계층
```

## 핵심 사용법

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
    model_client=ModelClient(base_url="https://api.openai.com/v1", model="gpt-4o-mini", api_key="sk-..."),
    tool_registry=registry,
)
result = await agent.run("인사해줘")
```

## 코드 컨벤션

- Python 3.11+ async/await
- Protocol 기반 인터페이스 (LLMProvider, Middleware, StepExecutor)
- 전역 상태 금지 (라이브러리 안전성)
- 모듈 간 순환 의존 금지
- 예외는 MantisError 계층 사용
- 테스트: pytest, tests/ 디렉토리
