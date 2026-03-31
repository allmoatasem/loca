"""
Tests for the memory panel: open/close, add, delete, extract, count.
"""

import json


class TestMemoryPanel:
    def test_open_close_and_structure(self, page):
        """Panel opens, has input/extract/list, closes via button and overlay."""
        assert "open" not in (page.locator("#mem-overlay").get_attribute("class") or "")

        # Open
        page.locator("#mem-count-label").click()
        page.wait_for_selector("#mem-overlay.open")
        assert page.locator("#mem-panel").is_visible()
        assert page.locator("#mem-input").is_visible()
        assert page.locator("#mem-save-btn").is_visible()
        assert "Extract" in page.locator("#mem-extract-btn").text_content()
        assert "No memories yet" in page.locator("#mem-list").text_content()

        # Close via button
        page.locator("#mem-panel .panel-close").click()
        page.wait_for_function(
            "!document.getElementById('mem-overlay').classList.contains('open')"
        )

        # Reopen, close via overlay click
        page.locator("#mem-count-label").click()
        page.wait_for_selector("#mem-overlay.open")
        page.locator("#mem-overlay").click(position={"x": 700, "y": 300})
        page.wait_for_function(
            "!document.getElementById('mem-overlay').classList.contains('open')"
        )

    def test_add_memory_and_clear_input(self, page, base_url):
        """Adding a memory via button calls API, renders it, clears input."""
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
        page.wait_for_function("document.getElementById('mem-input').value === ''")

    def test_add_memory_via_enter(self, page, base_url):
        """Pressing Enter in the input also saves."""
        new_mem = {"id": "m2", "content": "Uses M3", "type": "user_fact", "created": 1711900000}
        page.route(f"{base_url}/api/memories", lambda route: (
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"id": "m2", "ok": True}))
            if route.request.method == "POST"
            else route.fulfill(status=200, content_type="application/json",
                               body=json.dumps({"memories": [new_mem]}))
        ))
        page.locator("#mem-count-label").click()
        page.wait_for_selector("#mem-overlay.open")
        page.locator("#mem-input").fill("Uses M3")
        page.locator("#mem-input").press("Enter")
        page.wait_for_selector(".mem-item")

    def test_add_empty_does_nothing(self, page):
        page.locator("#mem-count-label").click()
        page.wait_for_selector("#mem-overlay.open")
        page.locator("#mem-save-btn").click()
        assert page.locator(".mem-item").count() == 0

    def test_delete_memory(self, page, base_url):
        mems = [
            {"id": "m1", "content": "Fact A", "type": "user_fact", "created": 1711900000},
            {"id": "m2", "content": "Fact B", "type": "user_fact", "created": 1711800000},
        ]
        call_count = {"n": 0}

        def handle(route):
            if route.request.method == "DELETE":
                route.fulfill(status=200, content_type="application/json", body='{"ok": true}')
            else:
                call_count["n"] += 1
                remaining = [mems[1]] if call_count["n"] > 1 else mems
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"memories": remaining}))

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

    def test_extract_with_no_history(self, page):
        page.locator("#mem-count-label").click()
        page.wait_for_selector("#mem-overlay.open")
        page.locator("#mem-extract-btn").click()
        page.wait_for_function(
            "document.getElementById('mem-extract-btn').textContent.includes('Nothing')"
        )

    def test_memory_count_badge(self, page, base_url):
        mems = [{"id": f"m{i}", "content": f"F{i}", "type": "user_fact", "created": 1711900000}
                for i in range(3)]
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
