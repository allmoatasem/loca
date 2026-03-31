"""
Tests for conversation persistence: list, load, delete, new conversation.
"""

import json


def _mock_stream(text="ok"):
    lines = []
    for ch in text:
        lines.append(
            f'data: {json.dumps({"choices": [{"delta": {"content": ch}}], "model": "m"})}\n\n'
        )
    lines.append(
        f'data: {json.dumps({"model": "m", "usage": {"completion_tokens": len(text), "prompt_tokens": 5}, "choices": []})}\n\n'
    )
    lines.append("data: [DONE]\n\n")
    return "".join(lines)


class TestConversationList:
    def test_empty_and_populated_list(self, page, base_url):
        """Empty state, then populated list with items and delete buttons."""
        assert "No conversations" in page.locator("#conv-list").text_content()

        convs = [
            {"id": "c1", "title": "First chat", "updated": 1711900000},
            {"id": "c2", "title": "Second chat", "updated": 1711800000},
        ]
        page.unroute(f"{base_url}/api/conversations")
        page.route(f"{base_url}/api/conversations", lambda route: (
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"conversations": convs}))
            if route.request.method == "GET" else route.fallback()
        ))
        page.evaluate("loadConvList()")
        page.wait_for_selector(".conv-item")
        assert page.locator(".conv-item").count() == 2
        assert "First chat" in page.locator(".conv-item").first.text_content()
        assert page.locator(".conv-del").count() == 2


class TestLoadConversation:
    def test_load_populates_messages_and_stats(self, page, base_url):
        """Clicking a conversation loads messages, highlights it, updates stats."""
        conv_data = {
            "id": "c1", "title": "Test", "model": "test-model",
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there!"},
            ],
        }
        page.route(f"{base_url}/api/conversations/c1", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(conv_data),
        ))
        convs = [{"id": "c1", "title": "Test", "updated": 1711900000}]
        page.unroute(f"{base_url}/api/conversations")
        page.route(f"{base_url}/api/conversations", lambda route: (
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"conversations": convs}))
            if route.request.method == "GET" else route.fallback()
        ))

        page.evaluate("loadConvList()")
        page.wait_for_selector(".conv-item")
        page.locator(".conv-item").first.click()

        page.wait_for_selector(".user-bubble")
        assert "Hello" in page.locator(".user-bubble").first.text_content()
        assert "Hi there!" in page.locator(".asst-content").first.text_content()
        assert "active" in page.locator(".conv-item").first.get_attribute("class")
        assert page.locator("#stat-msgs").text_content() == "1"


class TestDeleteConversation:
    def test_delete_removes_from_list(self, page, base_url):
        convs = [{"id": "c1", "title": "To delete", "updated": 1711900000}]
        call_count = {"n": 0}

        page.unroute(f"{base_url}/api/conversations")

        def handle_convs(route):
            if route.request.method == "GET":
                call_count["n"] += 1
                body = '{"conversations": []}' if call_count["n"] > 1 else json.dumps(
                    {"conversations": convs})
                route.fulfill(status=200, content_type="application/json", body=body)
            else:
                route.fulfill(status=200, content_type="application/json", body='{"ok": true}')

        page.route(f"{base_url}/api/conversations", handle_convs)
        page.route(f"{base_url}/api/conversations/c1", lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"ok": true}',
        ))
        page.route(f"{base_url}/api/extract-memories", lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"memories": []}',
        ))

        page.evaluate("loadConvList()")
        page.wait_for_selector(".conv-item")
        page.locator(".conv-del").first.click()
        page.wait_for_function("document.querySelectorAll('.conv-item').length === 0")


class TestNewConversation:
    def test_new_chat_clears_and_resets(self, page, base_url):
        """New conversation clears messages, restores empty state, resets stats."""
        page.route(f"{base_url}/v1/chat/completions", lambda route: route.fulfill(
            status=200, content_type="text/event-stream", body=_mock_stream("ok"),
        ))
        page.route(f"{base_url}/api/conversations", lambda route: (
            route.fulfill(status=200, content_type="application/json",
                          body='{"id": "cx", "ok": true}')
            if route.request.method == "POST" else route.fallback()
        ))
        page.route(f"{base_url}/api/extract-memories", lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"memories": []}',
        ))

        page.locator("#input").fill("hello")
        page.locator("#send-btn").click()
        page.wait_for_function("document.getElementById('stat-msgs').textContent !== '0'")

        page.locator("#new-chat-btn").click()
        page.wait_for_selector("#empty")
        assert page.locator("#empty").is_visible()
        assert page.locator(".user-bubble").count() == 0
        assert page.locator("#stat-msgs").text_content() == "0"
