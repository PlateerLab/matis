# mantis — AI Agent 실행 엔진 라이브러리

pip install mantis으로 설치 가능한 독립 라이브러리.
xgen3.0에서 추출한 핵심 로직을 라이브러리로 패키징.

## 프로젝트 한줄 요약

> AI Agent의 Think→Act→Observe 실행 루프를 라이브러리로 제공.
> 도구 검색, 코드 샌드박스, AI 도구 생성, 승인 워크플로우 등을 모듈식으로 조합.
> 기존 xgen-workflow에 import 한 줄로 이식 가능.

## 핵심 설계

- Phase 기반 파이프라인: PREPARE → RESOLVE → EXECUTE → STREAM → PERSIST
- 각 모듈 독립 사용 가능 (검색만, 샌드박스만 등)
- Protocol 기반 인터페이스 (LLMProvider 등)
- 상세 설계는 blueprint.md 참조

## 기술 스택

- Python 3.11+, async/await 기반
- 필수 의존성: httpx만
- 선택: graph-tool-call (도구 검색), docker (샌드박스)

## 패키지 구조

```
mantis/
├── pipeline/       ← Phase 파이프라인 (조합기)
├── engine/         ← AgentRunner (Think→Act→Observe 루프)
├── tools/          ← @tool 데코레이터 + ToolRegistry
├── llm/            ← LLM 추상화 (OpenAI 호환)
├── search/         ← 도구 검색 (graph-tool-call)
├── sandbox/        ← Docker 코드 격리
├── generate/       ← AI 도구 생성
├── safety/         ← 승인 워크플로우
├── state/          ← 상태 저장/복구
├── context/        ← 대화 컨텍스트
├── trace/          ← 트레이싱
├── testing/        ← 도구 품질 검증
└── adapters/       ← xgen-workflow 호환 어댑터
```

## 핵심 사용법

```python
from mantis import AgentRunner, tool

@tool(name="greet", description="인사한다")
async def greet(name: str) -> dict:
    return {"message": f"안녕 {name}"}

runner = AgentRunner(model="gpt-4o-mini", api_key="sk-...")
runner.add_tool(greet)
result = await runner.run("인사해줘")
```

## 코드 컨벤션

- Python 3.11+ async/await
- Protocol 기반 인터페이스 (LLMProvider, TraceExporter)
- 테스트: pytest, tests/ 디렉토리
- 모듈 간 순환 의존 금지
