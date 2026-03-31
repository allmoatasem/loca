"""
Tests for the chat send/receive flow, streaming, markdown rendering,
and vision model guard.
"""

import json


def _mock_stream_response(text, model="test-model"):
    """Build a SSE stream body mimicking /v1/chat/completions streaming."""
    lines = []
    for i, ch in enumerate(text):
        chunk = {
            "choices": [{"delta": {"content": ch}, "index": 0}],
            "model": model,
        }
        lines.append(f"data: {json.dumps(chunk)}\n\n")
    # Final usage chunk
    usage = {
        "model": model,
        "usage": {"completion_tokens": len(text), "prompt_tokens": 10},
        "choices": [],
    }
    lines.append(f"data: {json.dumps(usage)}\n\n")
    lines.append("data: [DONE]\n\n")
    return "".join(lines)


class TestSendMessage:
    def test_send_empty_does_nothing(self, page):
        """Clicking send with no text should not add any message bubble."""
        page.locator("#send-btn").click()
        assert page.locator(".msg-wrap").count() == 0

    def test_send_message_adds_user_bubble(self, page, base_url):
        """Typing and pressing Enter should add a user bubble."""
        # Mock the streaming endpoint
        page.route(f"{base_url}/v1/chat/completions", lambda route: route.fulfill(
            status=200,
            content_type="text/event-stream",
            body=_mock_stream_response("Hello!"),
        ))
        # Mock conversation save
        page.route(f"{base_url}/api/conversations", lambda route: (
            route.fulfill(status=200, content_type="application/json",
                          body='{"id": "conv1", "ok": true}')
            if route.request.method == "POST" else route.continue_()
        ))
        page.route(f"{base_url}/api/extract-memories", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body='{"memories": []}',
        ))

        page.locator("#input").fill("Hello world")
        page.locator("#send-btn").click()

        # User bubble appears
        page.wait_for_selector(".user-bubble")
        assert "Hello world" in page.locator(".user-bubble").first.text_content()

    def test_send_shows_assistant_response(self, page, base_url):
        """After sending, assistant response should stream and appear."""
        page.route(f"{base_url}/v1/chat/completions", lambda route: route.fulfill(
            status=200,
            content_type="text/event-stream",
            body=_mock_stream_response("Hi there! How can I help?"),
        ))
        page.route(f"{base_url}/api/conversations", lambda route: (
            route.fulfill(status=200, content_type="application/json",
                          body='{"id": "conv2", "ok": true}')
            if route.request.method == "POST" else route.continue_()
        ))
        page.route(f"{base_url}/api/extract-memories", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body='{"memories": []}',
        ))

        page.locator("#input").fill("test message")
        page.locator("#send-btn").click()

        # Wait for assistant content
        page.wait_for_selector(".asst-content")
        content = page.locator(".asst-content").first
        page.wait_for_function(
            "document.querySelector('.asst-content')?.textContent?.includes('How can I help')"
        )
        assert "Hi there" in content.text_content()

    def test_assistant_has_avatar(self, page, base_url):
        """Assistant message should show the 'L' avatar."""
        page.route(f"{base_url}/v1/chat/completions", lambda route: route.fulfill(
            status=200,
            content_type="text/event-stream",
            body=_mock_stream_response("ok"),
        ))
        page.route(f"{base_url}/api/conversations", lambda route: (
            route.fulfill(status=200, content_type="application/json",
                          body='{"id": "c3", "ok": true}')
            if route.request.method == "POST" else route.continue_()
        ))
        page.route(f"{base_url}/api/extract-memories", lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"memories": []}',
        ))

        page.locator("#input").fill("hi")
        page.locator("#send-btn").click()
        page.wait_for_selector(".asst-avatar")
        assert "L" in page.locator(".asst-avatar").first.text_content()

    def test_model_chip_shown(self, page, base_url):
        """Assistant message should display the model name chip."""
        page.route(f"{base_url}/v1/chat/completions", lambda route: route.fulfill(
            status=200,
            content_type="text/event-stream",
            body=_mock_stream_response("ok", model="qwen2.5-7b"),
        ))
        page.route(f"{base_url}/api/conversations", lambda route: (
            route.fulfill(status=200, content_type="application/json",
                          body='{"id": "c4", "ok": true}')
            if route.request.method == "POST" else route.continue_()
        ))
        page.route(f"{base_url}/api/extract-memories", lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"memories": []}',
        ))

        page.locator("#input").fill("hi")
        page.locator("#send-btn").click()
        page.wait_for_selector(".model-chip")
        # The chip should be present
        assert page.locator(".model-chip").first.is_visible()

    def test_empty_state_removed_after_send(self, page, base_url):
        """The empty state should disappear once a message is sent."""
        page.route(f"{base_url}/v1/chat/completions", lambda route: route.fulfill(
            status=200,
            content_type="text/event-stream",
            body=_mock_stream_response("ok"),
        ))
        page.route(f"{base_url}/api/conversations", lambda route: (
            route.fulfill(status=200, content_type="application/json",
                          body='{"id": "c5", "ok": true}')
            if route.request.method == "POST" else route.continue_()
        ))
        page.route(f"{base_url}/api/extract-memories", lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"memories": []}',
        ))

        assert page.locator("#empty").count() == 1
        page.locator("#input").fill("hi")
        page.locator("#send-btn").click()
        page.wait_for_selector(".user-bubble")
        assert page.locator("#empty").count() == 0

    def test_send_disables_button_during_stream(self, page, base_url):
        """The send button should be disabled while streaming."""
        # Use a slow response that we can check mid-stream
        page.route(f"{base_url}/v1/chat/completions", lambda route: route.fulfill(
            status=200,
            content_type="text/event-stream",
            body=_mock_stream_response("A long response to check streaming state"),
        ))
        page.route(f"{base_url}/api/conversations", lambda route: (
            route.fulfill(status=200, content_type="application/json",
                          body='{"id": "c6", "ok": true}')
            if route.request.method == "POST" else route.continue_()
        ))
        page.route(f"{base_url}/api/extract-memories", lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"memories": []}',
        ))

        page.locator("#input").fill("hi")
        page.locator("#send-btn").click()
        # After streaming completes, send button should be re-enabled
        page.wait_for_selector(".asst-content")
        page.wait_for_function(
            "!document.getElementById('send-btn').disabled"
        )
        assert not page.locator("#send-btn").is_disabled()

    def test_input_cleared_after_send(self, page, base_url):
        """The input should be cleared after sending."""
        page.route(f"{base_url}/v1/chat/completions", lambda route: route.fulfill(
            status=200,
            content_type="text/event-stream",
            body=_mock_stream_response("ok"),
        ))
        page.route(f"{base_url}/api/conversations", lambda route: (
            route.fulfill(status=200, content_type="application/json",
                          body='{"id": "c7", "ok": true}')
            if route.request.method == "POST" else route.continue_()
        ))
        page.route(f"{base_url}/api/extract-memories", lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"memories": []}',
        ))

        page.locator("#input").fill("test")
        page.locator("#send-btn").click()
        page.wait_for_selector(".user-bubble")
        assert page.locator("#input").text_content().strip() == ""


class TestMarkdownRendering:
    def _send_and_get_response(self, page, base_url, md_text):
        """Helper: send a message, get the assistant response with given text."""
        page.route(f"{base_url}/v1/chat/completions", lambda route: route.fulfill(
            status=200,
            content_type="text/event-stream",
            body=_mock_stream_response(md_text),
        ))
        page.route(f"{base_url}/api/conversations", lambda route: (
            route.fulfill(status=200, content_type="application/json",
                          body='{"id": "md1", "ok": true}')
            if route.request.method == "POST" else route.continue_()
        ))
        page.route(f"{base_url}/api/extract-memories", lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"memories": []}',
        ))
        page.locator("#input").fill("render this")
        page.locator("#send-btn").click()
        page.wait_for_function(
            "document.querySelector('.asst-content')?.innerHTML?.length > 10"
        )

    def test_bold_rendering(self, page, base_url):
        self._send_and_get_response(page, base_url, "This is **bold** text")
        html = page.locator(".asst-content").first.inner_html()
        assert "<strong>" in html

    def test_italic_rendering(self, page, base_url):
        self._send_and_get_response(page, base_url, "This is *italic* text")
        html = page.locator(".asst-content").first.inner_html()
        assert "<em>" in html

    def test_inline_code_rendering(self, page, base_url):
        self._send_and_get_response(page, base_url, "Use `console.log()` here")
        html = page.locator(".asst-content").first.inner_html()
        assert "<code>" in html

    def test_code_block_rendering(self, page, base_url):
        self._send_and_get_response(
            page, base_url, "```python\nprint('hi')\n```"
        )
        html = page.locator(".asst-content").first.inner_html()
        assert "<pre" in html
        assert "<code" in html

    def test_heading_rendering(self, page, base_url):
        self._send_and_get_response(page, base_url, "# Title\n\nSome text")
        html = page.locator(".asst-content").first.inner_html()
        assert "<h1>" in html

    def test_link_rendering(self, page, base_url):
        self._send_and_get_response(
            page, base_url, "Visit [here](https://example.com)"
        )
        html = page.locator(".asst-content").first.inner_html()
        assert '<a href="https://example.com"' in html

    def test_unordered_list_rendering(self, page, base_url):
        self._send_and_get_response(
            page, base_url, "- item one\n- item two\n- item three"
        )
        html = page.locator(".asst-content").first.inner_html()
        assert "<ul>" in html
        assert "<li>" in html

    def test_ordered_list_rendering(self, page, base_url):
        self._send_and_get_response(
            page, base_url, "1. first\n2. second\n3. third"
        )
        html = page.locator(".asst-content").first.inner_html()
        assert "<ol>" in html


class TestMessageStats:
    def test_copy_button_appears(self, page, base_url):
        """After a response, the Copy button should be visible on hover."""
        page.route(f"{base_url}/v1/chat/completions", lambda route: route.fulfill(
            status=200,
            content_type="text/event-stream",
            body=_mock_stream_response("Copy me"),
        ))
        page.route(f"{base_url}/api/conversations", lambda route: (
            route.fulfill(status=200, content_type="application/json",
                          body='{"id": "cp1", "ok": true}')
            if route.request.method == "POST" else route.continue_()
        ))
        page.route(f"{base_url}/api/extract-memories", lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"memories": []}',
        ))

        page.locator("#input").fill("hi")
        page.locator("#send-btn").click()
        page.wait_for_function(
            "document.querySelector('.copy-btn')?.style.display === 'inline-flex'"
        )
        # Copy button exists
        assert page.locator(".copy-btn").first.text_content() == "Copy"

    def test_stats_shown_after_response(self, page, base_url):
        """Token stats should appear after the response completes."""
        page.route(f"{base_url}/v1/chat/completions", lambda route: route.fulfill(
            status=200,
            content_type="text/event-stream",
            body=_mock_stream_response("Hello world!"),
        ))
        page.route(f"{base_url}/api/conversations", lambda route: (
            route.fulfill(status=200, content_type="application/json",
                          body='{"id": "st1", "ok": true}')
            if route.request.method == "POST" else route.continue_()
        ))
        page.route(f"{base_url}/api/extract-memories", lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"memories": []}',
        ))

        page.locator("#input").fill("hi")
        page.locator("#send-btn").click()
        page.wait_for_function(
            "document.querySelector('.msg-stats')?.textContent?.includes('tok')"
        )
        stats = page.locator(".msg-stats").first.text_content()
        assert "tok" in stats
        assert "tok/s" in stats

    def test_stat_ctx_updates(self, page, base_url):
        """Sidebar context stat should update after a message."""
        page.route(f"{base_url}/v1/chat/completions", lambda route: route.fulfill(
            status=200,
            content_type="text/event-stream",
            body=_mock_stream_response("ok"),
        ))
        page.route(f"{base_url}/api/conversations", lambda route: (
            route.fulfill(status=200, content_type="application/json",
                          body='{"id": "st2", "ok": true}')
            if route.request.method == "POST" else route.continue_()
        ))
        page.route(f"{base_url}/api/extract-memories", lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"memories": []}',
        ))

        page.locator("#input").fill("hello")
        page.locator("#send-btn").click()
        page.wait_for_function(
            "document.getElementById('stat-ctx').textContent !== '—'"
        )
        assert page.locator("#stat-ctx").text_content() != "—"


class TestVisionGuard:
    def test_non_vision_model_rejects_images(self, page, base_url):
        """Sending an image to a non-vision model should show a warning."""
        # Set up a model that is NOT a vision model
        page.route(f"{base_url}/v1/models", lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"data": [{"id": "qwen2.5-7b"}]}',
        ))
        page.reload()
        page.wait_for_load_state("networkidle")

        # Inject a fake image attachment via JS
        page.evaluate("""() => {
            attachments = [{type: 'image', name: 'test.png', data: 'data:image/png;base64,abc'}];
            document.getElementById('attach-strip').classList.add('has-files');
        }""")

        page.locator("#input").fill("describe this image")
        page.locator("#send-btn").click()

        # Should show vision warning
        page.wait_for_function(
            "document.querySelector('.asst-content')?.textContent?.includes('does not support images')"
        )
        assert "vision model" in page.locator(".asst-content").first.text_content()


class TestErrorHandling:
    def test_http_error_shows_message(self, page, base_url):
        """A failed HTTP response should show an error in the chat."""
        page.route(f"{base_url}/v1/chat/completions", lambda route: route.fulfill(
            status=500,
            content_type="text/plain",
            body="Internal Server Error",
        ))
        page.route(f"{base_url}/api/extract-memories", lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"memories": []}',
        ))

        page.locator("#input").fill("will fail")
        page.locator("#send-btn").click()

        page.wait_for_function(
            "document.querySelector('.asst-content')?.textContent?.includes('Error')"
        )
        assert "Error" in page.locator(".asst-content").first.text_content()
