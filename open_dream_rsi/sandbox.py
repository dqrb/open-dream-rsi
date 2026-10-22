"""Shared process-isolation primitive for executing untrusted candidate code.

Both :class:`open_dream_rsi.tools.CodeVerifier` (task-solution code, which is
NOT statically gated) and :class:`open_dream_rsi.core.policygen.PolicySandbox`
(policy code, additionally gated by an AST validator) run candidates through
:func:`run_isolated` so there is exactly one isolation boundary:

* a ``python -I`` child process with a scrubbed environment (no API keys);
* a wall-clock timeout that kills the WHOLE process group, so a candidate
  cannot survive its own death by parking work in a background child;
* POSIX resource limits: address space, CPU seconds, process count;
* an optional jail prefix — set ``ODR_SANDBOX_CMD`` to a wrapper such as
  ``bwrap --unshare-all --die-with-parent --ro-bind / /`` or an ``nsjail``
  invocation and it is exec'd in front of the interpreter.

This is defence in depth, not a container. ``python -I`` alone does NOT
restrict filesystem or network access; deployments that ingest hostile task
sources should set ``ODR_SANDBOX_CMD`` (or run the whole loop inside a
container) so the OS enforces what the AST gate cannot.
"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
from typing import Dict, List, Optional

#: Address-space cap for a sandboxed child (MB). Override with
#: ODR_SANDBOX_MEMORY_MB for candidates that legitimately need more.
DEFAULT_MEMORY_MB = 512

#: Thread-count cap for the sandboxed child (it must not fork/thread-bomb).
#: RLIMIT_NPROC counts ALL threads of the uid (shared with the parent
#: runtime), so the floor sits well above a busy interpreter's task count;
#: the live-uid measurement below raises it further when needed.
MAX_PROCS = 512

#: Env var naming a wrapper command prefixed to every sandbox spawn, e.g.
#: ODR_SANDBOX_CMD='bwrap --unshare-net --unshare-pid --die-with-parent --ro-bind / /'
JAIL_ENV_VAR = "ODR_SANDBOX_CMD"

_IS_POSIX = os.name == "posix"

#: Windows needs a few OS vars or ``python.exe`` can fail to boot DLLs;
#: we still scrub secrets / PYTHON* / debugger hooks so keys never cross.
_WIN_KEEP = (
    "SYSTEMROOT", "SystemRoot", "SYSTEMDRIVE", "WINDIR", "Windir",
    "TEMP", "TMP", "PATHEXT", "COMSPEC", "USERPROFILE", "USERNAME",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
)


def _build_sandbox_env() -> Dict[str, str]:
    """Minimal child env: no API keys, no PYTHON*, no IDE debugger hooks."""
    if _IS_POSIX:
        return {"PATH": "/usr/bin:/bin"}
    env: Dict[str, str] = {"PATH": os.environ.get("PATH", r"C:\Windows\System32")}
    for key in _WIN_KEEP:
        if key in os.environ:
            env[key] = os.environ[key]
    return env


#: Environment handed to sandboxed children (built once; Windows keeps OS vars).
SANDBOX_ENV: Dict[str, str] = _build_sandbox_env()


def _memory_mb() -> int:
    raw = os.environ.get("ODR_SANDBOX_MEMORY_MB", "")
    try:
        return max(64, int(raw))
    except ValueError:
        return DEFAULT_MEMORY_MB


def _make_preexec(timeout: float):
    """preexec_fn: RLIMIT_AS / RLIMIT_CPU / RLIMIT_NPROC for the child.

    Best-effort by design: in restrictive environments (a hard rlimit below
    the request — common for daemons and desktop sandboxes) ``setrlimit``
    would raise and kill the spawn with ``Exception occurred in
    preexec_fn``, so each limit is clamped to the hard ceiling and applied
    only when it can be.
    """
    import resource

    mem_bytes = _memory_mb() * 1024 * 1024
    cpu_soft = max(2, int(timeout) + 1)

    def _apply(limit: int, soft: int, hard_extra: Optional[int]) -> bool:
        """Set one rlimit, clamped to the environment's hard ceiling."""
        try:
            _, hard = resource.getrlimit(limit)
        except (ValueError, OSError):
            hard = resource.RLIM_INFINITY
        if hard == 0:
            # The environment forbids this resource entirely; applying
            # soft=0 would insta-kill the child, so skip it.
            return False
        if hard != resource.RLIM_INFINITY:
            soft = min(soft, hard)
        if hard_extra is not None:
            hard = (soft + hard_extra) if hard == resource.RLIM_INFINITY \
                else min(hard, soft + hard_extra)
        try:
            resource.setrlimit(limit, (soft, hard))
        except (ValueError, OSError):
            return False
        return True

    def _limits() -> None:  # runs in the forked child only
        # Each limit is best-effort: degrade to fewer enforced limits
        # rather than kill the spawn.
        _apply(resource.RLIMIT_AS, mem_bytes, None)
        _apply(resource.RLIMIT_CPU, cpu_soft, 1)
        # RLIMIT_NPROC counts THREADS of this uid (shared with the parent),
        # so the floor must sit ABOVE the uid's current task count or every
        # fork fails with EAGAIN. Count threads via /proc, fall back to the
        # static floor where /proc is absent.
        existing = 0
        try:
            for entry in os.listdir("/proc"):
                if not entry.isdigit():
                    continue
                try:
                    with open(f"/proc/{entry}/status") as fh:
                        status = fh.read()
                    if f"Uid:\t{os.getuid()}\n" in status:
                        for line in status.splitlines():
                            if line.startswith("Threads:"):
                                existing += int(line.split()[1])
                except OSError:
                    continue
        except OSError:
            existing = 0
        cap = max(MAX_PROCS, existing + 64)
        _apply(resource.RLIMIT_NPROC, cap, None)

    return _limits


def jail_prefix() -> List[str]:
    """The ODR_SANDBOX_CMD wrapper argv ([] when unset)."""
    raw = os.environ.get(JAIL_ENV_VAR, "").strip()
    return shlex.split(raw) if raw else []


def run_isolated(
    argv: List[str],
    *,
    timeout: float,
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    """Run ``argv`` in an isolated child; raise TimeoutExpired past ``timeout``.

    On timeout the ENTIRE process group is SIGKILLed before the exception is
    re-raised — ``subprocess.run`` only kills the direct child, leaving any
    grandchildren the candidate spawned running unsupervised.

    Raises the same :class:`subprocess.TimeoutExpired` as ``subprocess.run``
    so callers keep their existing handling.
    """
    argv = [*jail_prefix(), *argv]
    # Rebuild each call so a mutated SANDBOX_ENV in tests cannot stick;
    # always drop debugger / PYTHON* injections that IDEA pydev may patch in.
    if env is None:
        env = _build_sandbox_env()
    kwargs: Dict[str, object] = {}
    preexec = _make_preexec(timeout) if _IS_POSIX else None
    if _IS_POSIX:
        # own session -> own process group we can kill wholesale
        kwargs["start_new_session"] = True
        kwargs["preexec_fn"] = preexec
    else:
        # New process group on Windows: IDEA/pydev ``--multiprocess`` is less
        # likely to attach into the sandboxed verifier child.
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=cwd,
        **kwargs,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        out, err = proc.communicate()
        raise
    return subprocess.CompletedProcess(argv, proc.returncode, out, err)


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        if _IS_POSIX:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


__all__ = ["run_isolated", "jail_prefix", "SANDBOX_ENV", "JAIL_ENV_VAR"]

# Interpreter path kept here so callers do not each hard-code it.
PYTHON = sys.executable
