"""
Tests for the models panel: open/close, tabs, downloaded list, discover tab.
"""

import json


def _open_panel(page, base_url, models=None):
    """Open the models panel with optional model list."""
    page.route(f"{base_url}/api/local-models", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"models": models or []}),
    ))
    page.locator(".sidebar-action-btn").nth(3).click()
    page.wait_for_selector("#models-overlay.open")


class TestModelsPanel:
    def test_open_close_and_structure(self, page, base_url):
        """Panel opens with header/tabs, closes via button and overlay."""
        assert "open" not in (page.locator("#models-overlay").get_attribute("class") or "")

        _open_panel(page, base_url)
        assert "Manage Models" in page.locator("#models-panel-hdr h3").text_content()

        tabs = page.locator(".models-tab")
        assert tabs.count() == 2
        assert "Downloaded" in tabs.nth(0).text_content()
        assert "Discover" in tabs.nth(1).text_content()
        assert "active" in page.locator('.models-tab[data-tab="downloaded"]').get_attribute("class")

        # Close via button
        page.locator("#models-panel .panel-close").click()
        page.wait_for_function(
            "!document.getElementById('models-overlay').classList.contains('open')"
        )

        # Reopen, close via overlay
        _open_panel(page, base_url)
        page.locator("#models-overlay").click(position={"x": 10, "y": 10})
        page.wait_for_function(
            "!document.getElementById('models-overlay').classList.contains('open')"
        )

    def test_downloaded_empty_and_populated(self, page, base_url):
        """Empty state, then list with badges, dots, and action buttons."""
        _open_panel(page, base_url)
        page.wait_for_function(
            "document.getElementById('models-panel-body')?.textContent?.includes('No models downloaded')"
        )
        page.locator("#models-panel .panel-close").click()

        models = [
            {"name": "test.gguf", "format": "gguf", "size_gb": 4.0,
             "is_loaded": False, "param_label": "7B"},
            {"name": "mlx/Llama-70B", "format": "mlx", "size_gb": 38.5,
             "is_loaded": True, "param_label": "70B", "context_length": 131072},
        ]
        _open_panel(page, base_url, models)
        page.wait_for_selector(".model-card")
        assert page.locator(".model-card").count() == 2
        assert page.locator(".badge-gguf").count() >= 1
        assert page.locator(".model-loaded-dot").count() == 1
        # Unloaded has Load button, loaded has Eject
        assert "Load" in page.locator(".model-action-btn.primary").first.text_content()
        assert "Eject" in page.locator(".model-card").nth(1).text_content()

    def test_discover_tab_with_search_and_filters(self, page, base_url):
        """Switch to Discover, see recommendations, search, filter by category."""
        recs = [
            {"name": "Qwen2.5-7B", "format": "mlx", "size_gb": 4.0,
             "repo_id": "a/b", "fit_level": "Perfect", "quant": "Q4",
             "use_case": "general", "context": 32768},
            {"name": "CodeLlama-34B", "format": "gguf", "size_gb": 18.0,
             "repo_id": "c/d", "fit_level": "Good", "quant": "Q4",
             "use_case": "code"},
        ]
        page.route(f"{base_url}/api/recommended-models", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"recommendations": recs}),
        ))
        _open_panel(page, base_url)
        page.locator('.models-tab[data-tab="discover"]').click()
        assert "active" in page.locator('.models-tab[data-tab="discover"]').get_attribute("class")
        assert page.locator("#discover-filters").is_visible()
        assert page.locator("#discover-search-row").is_visible()

        page.wait_for_selector(".model-card")
        assert page.locator(".model-card").count() == 2
        assert "Get" in page.locator(".model-action-btn.primary").first.text_content()

        # Filter categories: All, General, Code, Reason, Vision
        assert page.locator(".disc-filter").count() == 5

        # Search narrows results
        page.locator("#discover-search").fill("qwen")
        page.wait_for_function("document.querySelectorAll('.model-card').length === 1")
        assert "Qwen" in page.locator(".model-card").first.text_content()

        # Category filter
        page.locator("#discover-search").fill("")
        page.wait_for_function("document.querySelectorAll('.model-card').length === 2")
        page.locator('.disc-filter[data-cat="code"]').click()
        page.wait_for_function("document.querySelectorAll('.model-card').length === 1")
        assert "CodeLlama" in page.locator(".model-card").first.text_content()
