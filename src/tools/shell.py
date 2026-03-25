"""
Shell execution tool — runs allowed shell commands with a timeout.
Only whitelisted base commands are permitted.
"""

import asyncio
import logging
import shlex

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30
_DEFAULT_ALLOWED = {"ls", "cat", "grep", "find", "wc", "head", "tail", "git", "python3", "pwd"}
_MAX_OUTPUT_CHARS = 8000


async def shell_exec(
    command: str,
    allowed_commands: set[str] | None = None,
    timeout_seconds: int = _DEFAULT_TIMEOUT,
) -> dict:
    """
    Run a shell command and return its output.

    Only the base command (first token) is checked against the allowlist.

    Returns:
        {"command": str, "stdout": str, "stderr": str, "returncode": int, "error": str | None}
    """
    if allowed_commands is None:
        allowed_commands = _DEFAULT_ALLOWED

    try:
        tokens = shlex.split(command)
    except ValueError as e:
        return {"command": command, "stdout": "", "stderr": "", "returncode": -1, "error": f"Invalid command: {e}"}

    if not tokens:
        return {"command": command, "stdout": "", "stderr": "", "returncode": -1, "error": "Empty command"}

    base_cmd = tokens[0]
    # Strip path prefixes (e.g. /usr/bin/ls → ls)
    base_name = base_cmd.split("/")[-1]

    if base_name not in allowed_commands:
        return {
            "command": command,
            "stdout": "",
            "stderr": "",
            "returncode": -1,
            "error": f"Command '{base_name}' is not in the allowed list: {sorted(allowed_commands)}",
        }

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_seconds
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return {
                "command": command,
                "stdout": "",
                "stderr": "",
                "returncode": -1,
                "error": f"Command timed out after {timeout_seconds}s",
            }

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        if len(stdout) > _MAX_OUTPUT_CHARS:
            stdout = stdout[:_MAX_OUTPUT_CHARS] + "\n[output truncated]"
        if len(stderr) > _MAX_OUTPUT_CHARS:
            stderr = stderr[:_MAX_OUTPUT_CHARS] + "\n[output truncated]"

        logger.info(f"shell_exec: {command!r} → returncode={proc.returncode}")
        return {
            "command": command,
            "stdout": stdout,
            "stderr": stderr,
            "returncode": proc.returncode,
            "error": None,
        }

    except Exception as e:
        logger.exception(f"shell_exec error: {command!r}")
        return {"command": command, "stdout": "", "stderr": "", "returncode": -1, "error": str(e)}
