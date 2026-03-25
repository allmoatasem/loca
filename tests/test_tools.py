"""
Tests for the tools layer.

Run with: pytest tests/test_tools.py -v

Note: web_search and web_fetch tests require network/SearXNG access.
Run offline tests only with: pytest tests/test_tools.py -v -m "not network"
"""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from src.tools.file_ops import file_read, file_write
from src.tools.shell import shell_exec


# ---------------------------------------------------------------------------
# file_read
# ---------------------------------------------------------------------------

def test_file_read_existing_file():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("hello world\nline 2")
        path = f.name
    try:
        result = file_read(path)
        assert result["error"] is None
        assert "hello world" in result["content"]
        assert result["path"] == path
    finally:
        os.unlink(path)


def test_file_read_missing_file():
    result = file_read("/tmp/this_file_does_not_exist_12345.txt")
    assert result["error"] is not None
    assert "not found" in result["error"].lower() or "no such" in result["error"].lower()
    assert result["content"] == ""


def test_file_read_respects_max_chars():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("a" * 1000)
        path = f.name
    try:
        result = file_read(path, max_chars=100)
        assert len(result["content"]) <= 100
        assert "warning" in result
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# file_write
# ---------------------------------------------------------------------------

def test_file_write_creates_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "test_output.txt")
        result = file_write(path, "test content")
        assert result["error"] is None
        assert result["bytes_written"] > 0
        assert os.path.exists(path)
        with open(path) as f:
            assert f.read() == "test content"


def test_file_write_creates_parent_dirs():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "nested", "dir", "file.txt")
        result = file_write(path, "nested content")
        assert result["error"] is None
        assert os.path.exists(path)


def test_file_write_overwrite_false_blocks():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("original")
        path = f.name
    try:
        result = file_write(path, "new content", overwrite=False)
        assert result["error"] is not None
        assert "overwrite" in result["error"].lower()
        with open(path) as f:
            assert f.read() == "original"
    finally:
        os.unlink(path)


def test_file_write_overwrite_true_replaces():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("original")
        path = f.name
    try:
        result = file_write(path, "replaced", overwrite=True)
        assert result["error"] is None
        with open(path) as f:
            assert f.read() == "replaced"
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# shell_exec
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_shell_exec_allowed_command():
    result = await shell_exec("pwd")
    assert result["error"] is None
    assert result["returncode"] == 0
    assert "/" in result["stdout"]


@pytest.mark.asyncio
async def test_shell_exec_ls():
    with tempfile.TemporaryDirectory() as tmpdir:
        open(os.path.join(tmpdir, "testfile.txt"), "w").close()
        result = await shell_exec(f"ls {tmpdir}")
        assert result["error"] is None
        assert "testfile.txt" in result["stdout"]


@pytest.mark.asyncio
async def test_shell_exec_blocked_command():
    result = await shell_exec("rm -rf /tmp/test")
    assert result["error"] is not None
    assert "not in the allowed list" in result["error"]
    assert result["returncode"] == -1


@pytest.mark.asyncio
async def test_shell_exec_empty_command():
    result = await shell_exec("")
    assert result["error"] is not None


@pytest.mark.asyncio
async def test_shell_exec_nonzero_exit():
    result = await shell_exec("ls /this/path/definitely/does/not/exist")
    assert result["returncode"] != 0
    assert result["error"] is None  # error field is for execution errors, not exit codes


@pytest.mark.asyncio
async def test_shell_exec_timeout():
    # Use a very short timeout to force a timeout
    result = await shell_exec(
        "python3 -c 'import time; time.sleep(10)'",
        allowed_commands={"python3"},
        timeout_seconds=1,
    )
    assert result["error"] is not None
    assert "timed out" in result["error"].lower()


# ---------------------------------------------------------------------------
# Network tests (skipped by default)
# ---------------------------------------------------------------------------

@pytest.mark.network
@pytest.mark.asyncio
async def test_web_search_returns_results():
    from src.tools.web_search import web_search
    # Requires SearXNG running — update URL before running
    results = await web_search(
        query="Python asyncio tutorial",
        searxng_url="http://localhost:8080",
        max_results=3,
    )
    assert len(results) > 0
    assert results[0].url.startswith("http")


@pytest.mark.network
@pytest.mark.asyncio
async def test_web_fetch_returns_content():
    from src.tools.web_fetch import web_fetch
    result = await web_fetch("https://httpbin.org/get")
    assert result["error"] is None
    assert len(result["content"]) > 0
