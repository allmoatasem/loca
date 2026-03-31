"""
Tests for the memory panel: open/close, add, delete, extract, count.
"""

import json


class TestMemoryPanelOpenClose:
    def test_panel_closed_by_default(self, page):
        overlay = page.locator("#mem-overlay")
        assert "open" not in (overlay.get_attribute("class") or "")

    def test_open_memory_panel(self, page):
        page.locator("#mem-count-label").click()
        page.wait_for_selector("#mem-overlay.open")
        assert page.locator("#mem-panel").is_visible()

    def test_close_memory_panel_via_button(self, page):
        page.locator("#mem-count-label").click()
        page.wait_for_selector("#mem-overlay.open")
        page.locator("#mem-panel .panel-close").click()
        page.wait_for_function(
            "!document.getElementById('mem-overlay').classList.contains('open')"
        )

    def test_close_memory_panel_via_overlay_click(self, page):
        page.locator("#mem-count-label").click()
        page.wait_for_selector("#mem-overlay.open")
        # Click the overlay (outside the panel)
        page.locator("#mem-overlay").click(position={"x": 700, "y": 300})
        page.wait_for_function(
            "!document.getElementById('mem-overlay').classList.contains('open')"
        )

    def test_memory_panel_has_input(self, page):
        page.locator("#mem-count-label").click()
        page.wait_for_selector("#mem-overlay.open")
        assert page.locator("#mem-input").is_visible()
        assert page.locator("#mem-save-btn").is_visible()

    def test_memory_panel_has_extract_button(self, page):
        page.locator("#mem-count-label").click()
        page.wait_for_selector("#mem-overlay.open")
        assert page.locator("#mem-extract-btn").is_visible()
        assert "Extract" in page.locator("#mem-extract-btn").text_content()


class TestMemoryEmpty:
    def test_empty_memory_message(self, page):
        page.locator("#mem-count-label").click()
        page.wait_for_selector("#mem-overlay.open")
        assert "No memories yet" in page.locator("#mem-list").text_content()


class TestMemoryAdd:
    def test_add_manual_memory(self, page, base_url):
        """Adding a memory should call the API and re-render the list."""
        new_mem = {"id": "m1", "content": "User likes Python", "type": "user_fact",
                   "created": 1711900000}
        page.route(f"{base_url}/api/memories", lambda route: (
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"id": "m1", "ok": True}))
            if route.request.method == "POST"
            else route.fulfill(status=200, content_type="application/json",
                               body=json.dumps({"memories": [new_mem]}))
        ))

        page.locator("#mem-count-label").click()
        page.wait_for_selector("#mem-overlay.open")

        page.locator("#mem-input").fill("User likes Python")
        page.locator("#mem-save-btn").click()

        page.wait_for_selector(".mem-item")
        assert "User likes Python" in page.locator(".mem-item").first.text_content()

    def test_add_memory_via_enter(self, page, base_url):
        """Pressing Enter in the memory input should save it."""
        new_mem = {"id": "m2", "content": "Uses M3 Ultra", "type": "user_fact",
                   "created": 1711900000}
        page.route(f"{base_url}/api/memories", lambda route: (
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"id": "m2", "ok": True}))
            if route.request.method == "POST"
            else route.fulfill(status=200, content_type="application/json",
                               body=json.dumps({"memories": [new_mem]}))
        ))

        page.locator("#mem-count-label").click()
        page.wait_for_selector("#mem-overlay.open")

        page.locator("#mem-input").fill("Uses M3 Ultra")
        page.locator("#mem-input").press("Enter")

        page.wait_for_selector(".mem-item")
        assert "Uses M3 Ultra" in page.locator(".mem-item").first.text_content()

    def test_add_empty_memory_does_nothing(self, page, base_url):
        """Saving an empty memory should do nothing."""
        page.locator("#mem-count-label").click()
        page.wait_for_selector("#mem-overlay.open")
        page.locator("#mem-save-btn").click()
        # No new .mem-item should appear
        assert page.locator(".mem-item").count() == 0

    def test_input_cleared_after_save(self, page, base_url):
        """After saving, the input field should be cleared."""
        page.route(f"{base_url}/api/memories", lambda route: (
            route.fulfill(status=200, content_type="application/json",
                          body='{"id": "m3", "ok": true}')
            if route.request.method == "POST"
            else route.fulfill(status=200, content_type="application/json",
                               body='{"memories": []}')
        ))

        page.locator("#mem-count-label").click()
        page.wait_for_selector("#mem-overlay.open")
        page.locator("#mem-input").fill("some fact")
        page.locator("#mem-save-btn").click()
        page.wait_for_function("document.getElementById('mem-input').value === ''")
        assert page.locator("#mem-input").input_value() == ""


class TestMemoryDelete:
    def test_delete_memory(self, page, base_url):
        """Clicking delete on a memory should remove it from the list."""
        mems = [
            {"id": "m1", "content": "Fact A", "type": "user_fact", "created": 1711900000},
            {"id": "m2", "content": "Fact B", "type": "user_fact", "created": 1711800000},
        ]
        call_count = {"n": 0}

        def handle(route):
            if route.request.method == "DELETE":
                route.fulfill(status=200, content_type="application/json",
                              body='{"ok": true}')
            else:
                call_count["n"] += 1
                if call_count["n"] > 1:
                    route.fulfill(status=200, content_type="application/json",
                                  body=json.dumps({"memories": [mems[1]]}))
                else:
                    route.fulfill(status=200, content_type="application/json",
                                  body=json.dumps({"memories": mems}))

        page.unroute(f"{base_url}/api/memories")
        page.route(f"{base_url}/api/memories", handle)
        page.route(f"{base_url}/api/memories/m1", lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"ok": true}',
        ))

        page.locator("#mem-count-label").click()
        page.wait_for_selector("#mem-overlay.open")
        page.wait_for_selector(".mem-item")
        assert page.locator(".mem-item").count() == 2

        page.locator(".mem-del").first.click()
        page.wait_for_function("document.querySelectorAll('.mem-item').length <= 1")


class TestMemoryExtract:
    def test_extract_with_no_history(self, page, base_url):
        """Extracting with no messages should show 'Nothing to extract'."""
        page.locator("#mem-count-label").click()
        page.wait_for_selector("#mem-overlay.open")
        page.locator("#mem-extract-btn").click()
        page.wait_for_function(
            "document.getElementById('mem-extract-btn').textContent.includes('Nothing')"
        )

    def test_extract_calls_api(self, page, base_url):
        """With a conversation, extract should call the API."""
        # First send a message to populate history
        page.route(f"{base_url}/v1/chat/completions", lambda route: route.fulfill(
            status=200, content_type="text/event-stream",
            body='data: {"choices": [{"delta": {"content": "ok"}}]}\n\ndata: {"model": "m", "usage": {"completion_tokens": 2, "prompt_tokens": 2}, "choices": []}\n\ndata: [DONE]\n\n',
        ))
        page.route(f"{base_url}/api/conversations", lambda route: (
            route.fulfill(status=200, content_type="application/json",
                          body='{"id": "cx", "ok": true}')
            if route.request.method == "POST" else route.continue_()
        ))

        extracted = [
            {"id": "ex1", "content": "Extracted fact", "type": "user_fact", "created": 1711900000},
        ]
        page.route(f"{base_url}/api/extract-memories", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"memories": extracted}),
        ))

        page.locator("#input").fill("I use Python daily")
        page.locator("#send-btn").click()
        page.wait_for_selector(".asst-content")

        # Now open memory panel and extract
        page.unroute(f"{base_url}/api/memories")
        page.route(f"{base_url}/api/memories", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"memories": extracted}),
        ))

        page.locator("#mem-count-label").click()
        page.wait_for_selector("#mem-overlay.open")
        page.locator("#mem-extract-btn").click()

        page.wait_for_function(
            "document.getElementById('mem-extract-btn').textContent.includes('Extracted')"
        )


class TestMemoryCount:
    def test_memory_count_badge(self, page, base_url):
        """The sidebar badge should show the memory count."""
        mems = [
            {"id": "m1", "content": "F1", "type": "user_fact", "created": 1711900000},
            {"id": "m2", "content": "F2", "type": "user_fact", "created": 1711800000},
            {"id": "m3", "content": "F3", "type": "knowledge", "created": 1711700000},
        ]
        page.unroute(f"{base_url}/api/memories")
        page.route(f"{base_url}/api/memories", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"memories": mems}),
        ))

        page.evaluate("loadMemCount()")
        page.wait_for_function(
            "document.getElementById('mem-count').textContent.includes('3')"
        )
        assert "3 facts" in page.locator("#mem-count").text_content()
