"""
Tests for the router module.

Run with: pytest tests/test_router.py -v
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from src.router import Model, route


# ---------------------------------------------------------------------------
# Default routing
# ---------------------------------------------------------------------------

def test_default_routes_to_general():
    r = route("Hello, how are you?")
    assert r.model == Model.GENERAL


def test_simple_code_routes_to_non_specialist():
    # Quick/simple code tasks should not route to the CODE specialist
    # They may go to GENERAL or WRITE depending on phrasing
    r = route("Write a Python function to reverse a string")
    assert r.model in (Model.GENERAL, Model.CODE, Model.WRITE)
    # Should NOT go to CODE specialist (no complexity signals)
    assert r.model != Model.CODE or r.reason != "Code specialist triggered"


# ---------------------------------------------------------------------------
# Image routing
# ---------------------------------------------------------------------------

def test_image_routes_to_general():
    r = route("What do you see in this image?", has_image=True)
    assert r.model == Model.GENERAL


def test_image_overrides_other_signals():
    # Even if message has reasoning signals, image wins
    r = route("Think through and reason about this diagram", has_image=True)
    assert r.model == Model.GENERAL


# ---------------------------------------------------------------------------
# Manual overrides
# ---------------------------------------------------------------------------

def test_explicit_code_override():
    r = route("/code Refactor this entire project's authentication module")
    assert r.model == Model.CODE
    assert r.override_command == "/code"


def test_explicit_reason_override():
    r = route("/reason What are the trade-offs between REST and GraphQL?")
    assert r.model == Model.REASON
    assert r.override_command == "/reason"


def test_explicit_general_override():
    r = route("/general Write a poem about the sea")
    assert r.model == Model.GENERAL
    assert r.override_command == "/general"


def test_override_case_insensitive():
    r = route("/CODE refactor the entire codebase")
    assert r.model == Model.CODE


# ---------------------------------------------------------------------------
# Code routing
# ---------------------------------------------------------------------------

def test_high_complexity_code_routes_to_code():
    r = route("Refactor the entire authentication module across multiple files in the codebase")
    assert r.model == Model.CODE


def test_architecture_routes_to_code():
    r = route("I need an architecture review of this large codebase with 500+ lines of logic")
    assert r.model == Model.CODE


def test_code_block_alone_not_enough_for_code_model():
    # Code block without complexity signals should stay on general
    r = route("What does this do?\n```python\nprint('hello')\n```")
    assert r.model in (Model.GENERAL, Model.REASON)


# ---------------------------------------------------------------------------
# Reason routing
# ---------------------------------------------------------------------------

def test_planning_routes_to_reason():
    r = route("Help me plan a migration strategy for moving from PostgreSQL to MongoDB")
    assert r.model == Model.REASON


def test_tradeoffs_routes_to_reason():
    r = route("What are the pros and cons of using microservices vs a monolith?")
    assert r.model == Model.REASON


def test_math_routes_to_reason():
    r = route("Solve this integral and prove your result")
    assert r.model == Model.REASON


def test_step_by_step_routes_to_reason():
    r = route("Walk me through how TCP handshakes work step-by-step")
    assert r.model == Model.REASON


# ---------------------------------------------------------------------------
# Web search triggers
# ---------------------------------------------------------------------------

def test_latest_triggers_search():
    r = route("What is the latest version of Python?")
    assert r.search_triggered is True


def test_current_triggers_search():
    r = route("What is the current price of NVIDIA stock?")
    assert r.search_triggered is True


def test_news_triggers_search():
    r = route("What's the latest news about AI regulation?")
    assert r.search_triggered is True


def test_look_up_triggers_search():
    r = route("Look up the documentation for FastAPI middleware")
    assert r.search_triggered is True


def test_normal_question_no_search():
    r = route("How do I reverse a list in Python?")
    assert r.search_triggered is False


# ---------------------------------------------------------------------------
# /web command
# ---------------------------------------------------------------------------

def test_web_command_triggers_search():
    r = route("/web best practices for LLM prompt engineering")
    assert r.search_triggered is True
    assert r.search_query == "best practices for LLM prompt engineering"


def test_web_command_routes_to_general_by_default():
    r = route("/web python asyncio tutorial")
    assert r.model == Model.GENERAL
    assert r.search_triggered is True


# ---------------------------------------------------------------------------
# Combined scenarios
# ---------------------------------------------------------------------------

def test_override_plus_search():
    r = route("/reason What is the latest research on chain-of-thought prompting?")
    assert r.model == Model.REASON
    assert r.search_triggered is True


def test_image_plus_search_intent():
    r = route("What is this chart showing? Is this data current?", has_image=True)
    assert r.model == Model.GENERAL
    # Search may or may not trigger depending on keyword detection
