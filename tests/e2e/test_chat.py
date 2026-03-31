"""
Tests for the chat send/receive flow, streaming, markdown rendering,
and vision model guard.
"""

import json


def _mock_stream(text, model="test-model"):
    """Build a SSE stream body mimicking /v1/chat/completions streaming."""
    lines = []
    for ch in text:
        chunk = {"choices": [{"delta": {"content": ch}, "index": 0}], "model": model}
        lines.append(f"data: {json.dumps(chunk)}\n\n")
    usage = {"model": model, "usage": {"completion_tokens": len(text), "prompt_tokens": 10},
             "choices": []}
    lines.append(f"data: {json.dumps(usage)}\n\n")
    lines.append("data: [DONE]\n\n")
    return "".join(lines)


def _setup_chat(page, base_url, response="Hello!", model="test-model", conv_id="c1"):
    """Wire up routes needed for a full send/receive cycle."""
    page.route(f"{base_url}/v1/chat/completions", lambda route: route.fulfill(
        status=200, content_type="text/event-stream", body=_mock_stream(response, model),
    ))
    page.route(f"{base_url}/api/conversations", lambda route: (
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"id": conv_id, "ok": True}))
        if route.request.method == "POST" else route.fallback()
    ))
    page.route(f"{base_url}/api/extract-memories", lambda route: route.fulfill(
        status=200, content_type="application/json", body='{"memories": []}',
    ))


class TestSendAndReceive:
    def test_empty_send_does_nothing(self, page):
        page.locator("#send-btn").click()
        assert page.locator(".msg-wrap").count() == 0

    def test_full_chat_cycle(self, page, base_url):
        """Send a message, receive streamed response, check all UI elements."""
        _setup_chat(page, base_url, response="Hi there! How can I help?", model="qwen2.5-7b")

        page.locator("#input").fill("Hello world")
        page.locator("#send-btn").click()

        # User bubble
        page.wait_for_selector(".user-bubble")
        assert "Hello world" in page.locator(".user-bubble").first.text_content()

        # Empty state gone
        assert page.locator("#empty").count() == 0

        # Input cleared
        assert page.locator("#input").text_content().strip() == ""

        # Assistant response with avatar + model chip
        page.wait_for_function(
            "document.querySelector('.asst-content')?.textContent?.includes('How can I help')"
        )
        assert "L" in page.locator(".asst-avatar").first.text_content()
        assert page.locator(".model-chip").first.is_visible()

        # Stats appear (tok, tok/s)
        page.wait_for_function(
            "document.querySelector('.msg-stats')?.textContent?.includes('tok')"
        )
        stats = page.locator(".msg-stats").first.text_content()
        assert "tok/s" in stats

        # Copy button
        page.wait_for_function(
            "document.querySelector('.copy-btn')?.style.display === 'inline-flex'"
        )
        assert page.locator(".copy-btn").first.text_content() == "Copy"

        # Sidebar stats updated
        page.wait_for_function("document.getElementById('stat-ctx').textContent !== '—'")
        assert page.locator("#stat-msgs").text_content() != "0"

        # Send button re-enabled
        page.wait_for_function("!document.getElementById('send-btn').disabled")


class TestMarkdownRendering:
    def test_markdown_elements(self, page, base_url):
        """Bold, italic, code, headings, links, lists all render correctly."""
        md = (
            "# Title\n\n"
            "This is **bold** and *italic* and `inline code`.\n\n"
            "```python\nprint('hi')\n```\n\n"
            "Visit [here](https://example.com)\n\n"
            "- item one\n- item two\n\n"
            "1. first\n2. second"
        )
        _setup_chat(page, base_url, response=md)
        page.locator("#input").fill("render")
        page.locator("#send-btn").click()
        page.wait_for_function(
            "document.querySelector('.asst-content')?.innerHTML?.length > 50"
        )
        html = page.locator(".asst-content").first.inner_html()
        assert "<h1>" in html
        assert "<strong>" in html
        assert "<em>" in html
        assert "<code>" in html
        assert "<pre" in html
        assert '<a href="https://example.com"' in html
        assert "<ul>" in html
        assert "<ol>" in html


class TestVisionGuard:
    def test_non_vision_model_rejects_images(self, page, base_url):
        page.unroute(f"{base_url}/v1/models")
        page.route(f"{base_url}/v1/models", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body='{"data": [{"id": "qwen2.5-7b"}]}',
        ))
        page.reload()
        page.wait_for_load_state("domcontentloaded")

        page.evaluate("""() => {
            attachments = [{type: 'image', name: 'test.png', data: 'data:image/png;base64,abc'}];
            document.getElementById('attach-strip').classList.add('has-files');
        }""")
        page.locator("#input").fill("describe this")
        page.locator("#send-btn").click()
        page.wait_for_function(
            "document.querySelector('.asst-content')?.textContent?.includes('does not support')"
        )


class TestErrorHandling:
    def test_http_error_shows_message(self, page, base_url):
        page.route(f"{base_url}/v1/chat/completions", lambda route: route.fulfill(
            status=500, content_type="text/plain", body="Internal Server Error",
        ))
        page.locator("#input").fill("will fail")
        page.locator("#send-btn").click()
        page.wait_for_function(
            "document.querySelector('.asst-content')?.textContent?.includes('Error')"
        )
