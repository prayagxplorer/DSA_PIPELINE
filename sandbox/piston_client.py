"""
Client for a SELF-HOSTED Piston instance (not the public API, which is no
longer usable for this -- see setup instructions in README.md).

Key differences from the earlier public-API client:
  - PISTON_BASE_URL defaults to localhost:2000 (your own Docker container)
  - run_code() passes stdin directly (these problems are stdin/stdout style,
    confirmed from the real JSON structure -- no function-call wrapping needed)
  - run_timeout / run_memory_limit are enforced BY PISTON ITSELF, not just
    client-side -- this is part of why self-hosting is safer than a bare
    subprocess: the sandbox process is killed by Piston's own supervisor,
    not by a timer in your Python script that could itself hang.
"""

import os
import re
import time
import requests

PISTON_BASE_URL = os.environ.get("PISTON_BASE_URL", "http://localhost:2000")
EXECUTE_URL = f"{PISTON_BASE_URL}/api/v2/execute"
RUNTIMES_URL = f"{PISTON_BASE_URL}/api/v2/runtimes"

_runtime_version_cache = {}


def get_runtime_version(language: str) -> str:
    """Looks up the currently-installed version for a language from your
    self-hosted instance -- avoids hardcoding a version string that could
    go stale (bit us before with the public API)."""
    if language in _runtime_version_cache:
        return _runtime_version_cache[language]

    resp = requests.get(RUNTIMES_URL, timeout=10)
    resp.raise_for_status()
    runtimes = resp.json()

    matches = [r for r in runtimes if r["language"] == language]
    if not matches:
        raise RuntimeError(
            f"No Piston runtime found for '{language}' on your self-hosted "
            f"instance. Available: {sorted(set(r['language'] for r in runtimes))}. "
            f"Install it with the piston CLI -- see README.md."
        )
    version = matches[0]["version"]
    _runtime_version_cache[language] = version
    return version


def run_code(code: str, stdin_text: str, time_limit_seconds: float = 2.0,
             memory_limit_mb: float = 256.0, language: str = "python") -> dict:
    """
    Executes `code` in the self-hosted sandbox with `stdin_text` fed to
    stdin, enforcing the problem's own time/memory limits at the Piston
    level (not just client-side).

    Returns {"stdout": str, "stderr": str, "code": int, "timed_out": bool}
    """
    version = get_runtime_version(language)

    try:
        resp = requests.post(
            EXECUTE_URL,
            json={
                "language": language,
                "version": version,
                "files": [{"content": code}],
                "stdin": stdin_text,
                "run_timeout": int(time_limit_seconds * 1000) + 500,  # small buffer over the problem's own limit
                "run_memory_limit": int(memory_limit_mb * 1024 * 1024),
            },
            timeout=time_limit_seconds + 10,  # client-side safety net in case Piston itself hangs
        )
    except requests.RequestException as e:
        return {"stdout": "", "stderr": f"REQUEST ERROR: {e}", "code": -1, "timed_out": False}

    if resp.status_code != 200:
        return {"stdout": "", "stderr": f"PISTON HTTP {resp.status_code}: {resp.text[:500]}",
                "code": -1, "timed_out": False}

    try:
        result = resp.json()
    except ValueError:
        return {"stdout": "", "stderr": "PISTON returned non-JSON response", "code": -1, "timed_out": False}

    run = result.get("run")
    compile_ = result.get("compile")

    if run is None and compile_ is None:
        return {"stdout": "", "stderr": f"PISTON API ERROR (no run/compile): {result}",
                "code": -1, "timed_out": False}

    compile_ = compile_ or {}
    run = run or {}

    if compile_.get("code", 0) != 0:
        return {"stdout": "", "stderr": "COMPILE ERROR: " + compile_.get("stderr", ""),
                "code": compile_.get("code", -1), "timed_out": False}

    # Piston reports a killed-by-signal run (e.g. SIGKILL from timeout) via
    # a non-zero "signal" field -- surface that distinctly from a normal
    # non-zero exit code so the error message is actually useful
    timed_out = run.get("signal") == "SIGKILL"

    return {
        "stdout": run.get("stdout", ""),
        "stderr": run.get("stderr", ""),
        "code": run.get("code", -1),
        "timed_out": timed_out,
    }


def normalize_output(text: str) -> str:
    """Whitespace-normalizes output before comparing -- the real test_cases
    in the actual JSON have inconsistent trailing newlines ("8" vs "8\\n"),
    confirmed directly in the uploaded file. Strip trailing whitespace per
    line and collapse trailing blank lines, without altering meaningful
    internal whitespace."""
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)
