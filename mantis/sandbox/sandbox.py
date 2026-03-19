"""Docker 컨테이너 격리 실행 — AI가 생성한 코드를 안전하게 실행."""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SandboxConfig:
    """샌드박스 설정."""

    image: str = "python:3.12-slim"
    timeout: int = 30          # 초
    memory_limit: str = "256m"
    cpu_limit: float = 1.0     # CPU 코어
    network: str = "none"      # none | bridge | host
    work_dir: str = "/tmp/sandbox"
    pip_packages: list[str] = field(default_factory=list)  # 사전 설치할 패키지


@dataclass
class SandboxResult:
    """샌드박스 실행 결과."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    timed_out: bool = False
    duration_ms: float = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "duration_ms": round(self.duration_ms, 1),
            "error": self.error,
        }

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.error


class DockerSandbox:
    """Docker 기반 코드 격리 실행기.

    - 컨테이너 생성 → 코드 마운트 → 실행 → 결과 수집 → 컨테이너 제거
    - 타임아웃, 메모리/CPU 제한, 네트워크 차단
    """

    def __init__(self, config: SandboxConfig | None = None):
        self.config = config or SandboxConfig()

    async def execute(self, code: str, language: str = "python") -> SandboxResult:
        """코드를 Docker 컨테이너에서 격리 실행.

        Args:
            code: 실행할 코드 문자열
            language: 언어 (현재 python만 지원)

        Returns:
            SandboxResult
        """
        if language != "python":
            return SandboxResult(error=f"미지원 언어: {language}")

        # Docker 사용 가능 여부 확인
        if not await self._docker_available():
            return SandboxResult(error="Docker가 설치되지 않았거나 실행 중이 아닙니다.")

        container_name = f"xgen-sandbox-{uuid.uuid4().hex[:8]}"

        # 임시 파일에 코드 저장
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            code_file = Path(f.name)

        try:
            result = await self._run_container(container_name, code_file)
            return result
        finally:
            # 임시 파일 정리
            code_file.unlink(missing_ok=True)
            # 컨테이너 정리 (이미 종료되었어도)
            await self._cleanup_container(container_name)

    async def execute_file(self, file_path: str | Path) -> SandboxResult:
        """파일을 Docker 컨테이너에서 실행."""
        file_path = Path(file_path)
        if not file_path.exists():
            return SandboxResult(error=f"파일 없음: {file_path}")

        code = file_path.read_text(encoding="utf-8")
        return await self.execute(code)

    async def _run_container(
        self, container_name: str, code_file: Path
    ) -> SandboxResult:
        """실제 Docker 컨테이너 실행. 코드는 stdin으로 전달 (파일 마운트 불필요)."""
        import time

        code = code_file.read_text(encoding="utf-8")

        # Docker run 명령 조립 — stdin으로 코드 전달
        cmd = [
            "docker", "run",
            "--name", container_name,
            "--rm",
            "-i",  # stdin 활성화
            # 리소스 제한
            "--memory", self.config.memory_limit,
            f"--cpus={self.config.cpu_limit}",
            # 네트워크
            f"--network={self.config.network}",
            # 보안 강화
            "--security-opt", "no-new-privileges",
        ]

        # pip 패키지 사전 설치가 필요하면 entrypoint 조정
        if self.config.pip_packages:
            install_cmd = f"pip install -q {' '.join(self.config.pip_packages)} && python -"
            cmd.extend([self.config.image, "sh", "-c", install_cmd])
        else:
            cmd.extend([self.config.image, "python", "-"])

        start_time = time.time()

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(input=code.encode("utf-8")),
                    timeout=self.config.timeout,
                )
                duration_ms = (time.time() - start_time) * 1000

                return SandboxResult(
                    stdout=stdout_bytes.decode("utf-8", errors="replace"),
                    stderr=stderr_bytes.decode("utf-8", errors="replace"),
                    exit_code=proc.returncode or 0,
                    duration_ms=duration_ms,
                )

            except asyncio.TimeoutError:
                duration_ms = (time.time() - start_time) * 1000
                # 타임아웃 시 프로세스 강제 종료
                proc.kill()
                await proc.wait()
                return SandboxResult(
                    stderr=f"타임아웃: {self.config.timeout}초 초과",
                    exit_code=-1,
                    timed_out=True,
                    duration_ms=duration_ms,
                )

        except FileNotFoundError:
            return SandboxResult(error="Docker 실행 파일을 찾을 수 없습니다.")
        except Exception as e:
            return SandboxResult(error=f"Docker 실행 오류: {e}")

    async def _docker_available(self) -> bool:
        """Docker CLI 사용 가능 여부."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "info",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            return proc.returncode == 0
        except FileNotFoundError:
            return False

    async def _cleanup_container(self, container_name: str) -> None:
        """컨테이너 강제 정리."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "rm", "-f", container_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except Exception:
            pass
