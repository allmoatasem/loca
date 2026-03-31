"""
Tests for conversation persistence: save, load, delete, new conversation.
"""

import json


def _mock_stream(text="ok"):
    lines = []
    for ch in text:
        lines.append(f'data: {json.dumps({"choices": [{"delta": {"content": ch}}], "model": "m"})}\n\n')
    lines.append(f'data: {json.dumps({"model": "m", "usage": {"completion_tokens": len(text), "prompt_tokens": 5}, "choices": []})}\n\n')
    lines.append("data: [DONE]\n\n")
    return "".join(lines)


def _setup_chat_routes(page, base_url, conv_id="conv-test"):
    """Set up routes needed for a chat round-trip."""
    page.route(f"{base_url}/v1/chat/completions", lambda route: route.fulfill(
        status=200, content_type="text/event-stream", body=_mock_stream("Sure!"),
    ))
    page.route(f"{base_url}/api/conversations", lambda route: (
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"id": conv_id, "ok": True}))
        if route.request.method == "POST" else route.continue_()
    ))
    page.route(f"{base_url}/api/extract-memories", lambda route: route.fulfill(
        status=200, content_type="application/json", body='{"memories": []}',
    ))


class TestConversationList:
    def test_empty_conv_list(self, page):
        """When no conversations exist, the list should show a placeholder."""
        list_el = page.locator("#conv-list")
        text = list_el.text_content()
        assert "No conversations" in text

    def test_conv_list_shows_items(self, page, base_url):
        """After loading conversations from the API, items should appear."""
        convs = [
            {"id": "c1", "title": "First chat", "updated": 1711900000},
            {"id": "c2", "title": "Second chat", "updated": 1711800000},
        ]
        # Unroute default and add new
        page.unroute(f"{base_url}/api/conversations")
        page.route(f"{base_url}/api/conversations", lambda route: (
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"conversations": convs}))
            if route.request.method == "GET" else route.continue_()
        ))

        # Trigger reload
        page.evaluate("loadConvList()")
        page.wait_for_selector(".conv-item")
        assert page.locator(".conv-item").count() == 2
        assert "First chat" in page.locator(".conv-item").first.text_content()

    def test_conv_item_has_delete_button(self, page, base_url):
        """Each conversation item should have a delete button."""
        convs = [{"id": "c1", "title": "Chat 1", "updated": 1711900000}]
        page.unroute(f"{base_url}/api/conversations")
        page.route(f"{base_url}/api/conversations", lambda route: (
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"conversations": convs}))
            if route.request.method == "GET" else route.continue_()
        ))
        page.evaluate("loadConvList()")
        page.wait_for_selector(".conv-item")
        assert page.locator(".conv-del").count() == 1


class TestLoadConversation:
    def test_load_conv_populates_messages(self, page, base_url):
        """Clicking a conversation should load its messages."""
        conv_data = {
            "id": "c1",
            "title": "Test conv",
            "model": "test-model",
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there!"},
            ],
        }
        page.route(f"{base_url}/api/conversations/c1", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(conv_data),
        ))

        # Also set up the list so we can click on it
        convs = [{"id": "c1", "title": "Test conv", "updated": 1711900000}]
        page.unroute(f"{base_url}/api/conversations")
        page.route(f"{base_url}/api/conversations", lambda route: (
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"conversations": convs}))
            if route.request.method == "GET" else route.continue_()
        ))

        page.evaluate("loadConvList()")
        page.wait_for_selector(".conv-item")
        page.locator(".conv-item").first.click()

        page.wait_for_selector(".user-bubble")
        assert "Hello" in page.locator(".user-bubble").first.text_content()
        assert "Hi there!" in page.locator(".asst-content").first.text_content()

    def test_load_conv_marks_active(self, page, base_url):
        """The loaded conversation should be highlighted in the sidebar."""
        conv_data = {
            "id": "c1", "title": "Test", "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
        }
        page.route(f"{base_url}/api/conversations/c1", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(conv_data),
        ))
        convs = [{"id": "c1", "title": "Test", "updated": 1711900000}]
        page.unroute(f"{base_url}/api/conversations")
        page.route(f"{base_url}/api/conversations", lambda route: (
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"conversations": convs}))
            if route.request.method == "GET" else route.continue_()
        ))

        page.evaluate("loadConvList()")
        page.wait_for_selector(".conv-item")
        page.locator(".conv-item").first.click()
        page.wait_for_selector(".conv-item.active")
        assert "active" in page.locator(".conv-item").first.get_attribute("class")

    def test_load_conv_updates_stats(self, page, base_url):
        """Loading a conversation should update sidebar stats."""
        conv_data = {
            "id": "c1", "title": "Test", "model": "m",
            "messages": [
                {"role": "user", "content": "Hello world"},
                {"role": "assistant", "content": "Hi!"},
            ],
        }
        page.route(f"{base_url}/api/conversations/c1", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(conv_data),
        ))
        convs = [{"id": "c1", "title": "Test", "updated": 1711900000}]
        page.unroute(f"{base_url}/api/conversations")
        page.route(f"{base_url}/api/conversations", lambda route: (
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"conversations": convs}))
            if route.request.method == "GET" else route.continue_()
        ))

        page.evaluate("loadConvList()")
        page.wait_for_selector(".conv-item")
        page.locator(".conv-item").first.click()
        page.wait_for_selector(".user-bubble")
        # Message count should be 1 (1 exchange = user+assistant)
        assert page.locator("#stat-msgs").text_content() == "1"


class TestDeleteConversation:
    def test_delete_conv(self, page, base_url):
        """Clicking delete should remove the conversation from the list."""
        page.unroute(f"{base_url}/api/conversations")

        convs = [{"id": "c1", "title": "To delete", "updated": 1711900000}]
        call_count = {"get": 0}

        def handle_convs(route):
            if route.request.method == "GET":
                call_count["get"] += 1
                # After delete, return empty
                if call_count["get"] > 1:
                    route.fulfill(status=200, content_type="application/json",
                                  body='{"conversations": []}')
                else:
                    route.fulfill(status=200, content_type="application/json",
                                  body=json.dumps({"conversations": convs}))
            else:
                route.fulfill(status=200, content_type="application/json",
                              body='{"ok": true}')

        page.route(f"{base_url}/api/conversations", handle_convs)
        page.route(f"{base_url}/api/conversations/c1", lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"ok": true}',
        ))
        page.route(f"{base_url}/api/extract-memories", lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"memories": []}',
        ))

        page.evaluate("loadConvList()")
        page.wait_for_selector(".conv-item")
        assert page.locator(".conv-item").count() == 1

        page.locator(".conv-del").first.click()
        page.wait_for_function("document.querySelectorAll('.conv-item').length === 0")


class TestNewConversation:
    def test_new_chat_clears_messages(self, page, base_url):
        """Clicking 'New conversation' should clear the messages area."""
        # First send a message
        _setup_chat_routes(page, base_url)
        page.locator("#input").fill("hello")
        page.locator("#send-btn").click()
        page.wait_for_selector(".user-bubble")

        # Now click new chat
        page.locator("#new-chat-btn").click()

        # Empty state should return
        page.wait_for_selector("#empty")
        assert page.locator("#empty").is_visible()
        assert page.locator(".user-bubble").count() == 0

    def test_new_chat_resets_stats(self, page, base_url):
        """New conversation should reset message count and context."""
        _setup_chat_routes(page, base_url)
        page.locator("#input").fill("hello")
        page.locator("#send-btn").click()
        page.wait_for_function("document.getElementById('stat-msgs').textContent !== '0'")

        page.locator("#new-chat-btn").click()
        assert page.locator("#stat-msgs").text_content() == "0"
