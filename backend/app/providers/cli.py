"""Controlled execution wrapper for external CLI tools (spec section 4).

Guarantees: argv list (never a shell string), fixed binary path resolved from configuration, timeout,
CPU/file-size/open-file resource limits, scratch working directory, minimal environment, bounded output.
User-provided strings may only ever appear as validated *arguments*, never as the executable.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.providers.base import ProviderTimeout, ProviderUnavailable

MAX_OUTPUT_BYTES = 256 * 1024


@dataclass
class CliResult:
    exit_code: int
    stdout: str
    stderr: str
    workdir: Path


def resolve_binary(configured: str) -> str:
    path = shutil.which(configured)
    if path is None:
        raise ProviderUnavailable(f"binary {configured!r} not found on PATH")
    return path


def _limits(cpu_seconds: int):
    def apply() -> None:  # runs in the child between fork and exec (POSIX only)
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024, 64 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))

    return apply


def run_controlled(argv: list[str], *, timeout: int, cpu_seconds: int | None = None) -> CliResult:
    if not argv or not all(isinstance(a, str) for a in argv):
        raise ValueError("argv must be a non-empty list of strings")
    workdir = Path(tempfile.mkdtemp(prefix="osint-cli-"))
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(workdir),
        "LANG": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
    }
    try:
        proc = subprocess.run(  # noqa: S603 - argv list, shell=False, validated arguments
            argv,
            cwd=workdir,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            shell=False,
            preexec_fn=_limits(cpu_seconds or timeout * 2) if os.name == "posix" else None,
        )
    except subprocess.TimeoutExpired as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        raise ProviderTimeout(f"{Path(argv[0]).name} exceeded {timeout}s timeout") from exc
    except OSError as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        raise ProviderUnavailable(f"cannot execute {argv[0]!r}: {exc}") from exc
    return CliResult(
        proc.returncode,
        proc.stdout[:MAX_OUTPUT_BYTES].decode("utf-8", "replace"),
        proc.stderr[:MAX_OUTPUT_BYTES].decode("utf-8", "replace"),
        workdir,
    )


def cleanup(result: CliResult) -> None:
    shutil.rmtree(result.workdir, ignore_errors=True)
