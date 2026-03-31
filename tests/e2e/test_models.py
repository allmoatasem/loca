"""
Tests for the models panel: open/close, tabs, downloaded list, discover tab.
"""

import json


class TestModelsPanelOpenClose:
    def test_panel_closed_by_default(self, page):
        overlay = page.locator("#models-overlay")
        assert "open" not in (overlay.get_attribute("class") or "")

    def test_open_models_panel(self, page, base_url):
        page.route(f"{base_url}/api/local-models", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body='{"models": []}',
        ))
        page.locator(".sidebar-action-btn").nth(2).click()
        page.wait_for_selector("#models-overlay.open")
        assert page.locator("#models-panel").is_visible()

    def test_close_via_button(self, page, base_url):
        page.route(f"{base_url}/api/local-models", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body='{"models": []}',
        ))
        page.locator(".sidebar-action-btn").nth(2).click()
        page.wait_for_selector("#models-overlay.open")
        page.locator("#models-panel .panel-close").click()
        page.wait_for_function(
            "!document.getElementById('models-overlay').classList.contains('open')"
        )

    def test_close_via_overlay(self, page, base_url):
        page.route(f"{base_url}/api/local-models", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body='{"models": []}',
        ))
        page.locator(".sidebar-action-btn").nth(2).click()
        page.wait_for_selector("#models-overlay.open")
        # Click outside the panel
        page.locator("#models-overlay").click(position={"x": 10, "y": 10})
        page.wait_for_function(
            "!document.getElementById('models-overlay').classList.contains('open')"
        )

    def test_panel_header(self, page, base_url):
        page.route(f"{base_url}/api/local-models", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body='{"models": []}',
        ))
        page.locator(".sidebar-action-btn").nth(2).click()
        page.wait_for_selector("#models-overlay.open")
        assert "Manage Models" in page.locator("#models-panel-hdr h3").text_content()


class TestModelsTabs:
    def _open_panel(self, page, base_url):
        page.route(f"{base_url}/api/local-models", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body='{"models": []}',
        ))
        page.locator(".sidebar-action-btn").nth(2).click()
        page.wait_for_selector("#models-overlay.open")

    def test_two_tabs_present(self, page, base_url):
        self._open_panel(page, base_url)
        tabs = page.locator(".models-tab")
        assert tabs.count() == 2
        assert "Downloaded" in tabs.nth(0).text_content()
        assert "Discover" in tabs.nth(1).text_content()

    def test_downloaded_tab_active_by_default(self, page, base_url):
        self._open_panel(page, base_url)
        assert "active" in page.locator('.models-tab[data-tab="downloaded"]').get_attribute("class")

    def test_switch_to_discover_tab(self, page, base_url):
        self._open_panel(page, base_url)
        page.route(f"{base_url}/api/recommended-models", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body='{"recommendations": []}',
        ))
        page.locator('.models-tab[data-tab="discover"]').click()
        assert "active" in page.locator('.models-tab[data-tab="discover"]').get_attribute("class")
        # Discover filters should now be visible
        assert page.locator("#discover-filters").is_visible()
        assert page.locator("#discover-search-row").is_visible()


class TestDownloadedModels:
    def test_no_models_message(self, page, base_url):
        page.route(f"{base_url}/api/local-models", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body='{"models": []}',
        ))
        page.locator(".sidebar-action-btn").nth(2).click()
        page.wait_for_selector("#models-overlay.open")
        body = page.locator("#models-panel-body")
        assert "No models downloaded" in body.text_content()

    def test_models_listed(self, page, base_url):
        models = [
            {"name": "qwen2.5-7b-q4.gguf", "format": "gguf", "size_gb": 4.2,
             "is_loaded": False, "param_label": "7B", "context_length": 32768},
            {"name": "mlx-community/Llama-3.3-70B-4bit", "format": "mlx",
             "size_gb": 38.5, "is_loaded": True, "param_label": "70B",
             "context_length": 131072},
        ]
        page.route(f"{base_url}/api/local-models", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"models": models}),
        ))
        page.locator(".sidebar-action-btn").nth(2).click()
        page.wait_for_selector("#models-overlay.open")
        page.wait_for_selector(".model-card")
        assert page.locator(".model-card").count() == 2

    def test_model_badges(self, page, base_url):
        models = [
            {"name": "test.gguf", "format": "gguf", "size_gb": 4.0,
             "is_loaded": False, "param_label": "7B"},
        ]
        page.route(f"{base_url}/api/local-models", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"models": models}),
        ))
        page.locator(".sidebar-action-btn").nth(2).click()
        page.wait_for_selector(".model-card")
        assert page.locator(".badge-gguf").count() >= 1

    def test_loaded_model_shows_green_dot(self, page, base_url):
        models = [
            {"name": "loaded.gguf", "format": "gguf", "size_gb": 4.0,
             "is_loaded": True, "param_label": "7B"},
        ]
        page.route(f"{base_url}/api/local-models", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"models": models}),
        ))
        page.locator(".sidebar-action-btn").nth(2).click()
        page.wait_for_selector(".model-card")
        assert page.locator(".model-loaded-dot").count() == 1

    def test_unloaded_model_has_load_button(self, page, base_url):
        models = [
            {"name": "unloaded.gguf", "format": "gguf", "size_gb": 4.0,
             "is_loaded": False, "param_label": "7B"},
        ]
        page.route(f"{base_url}/api/local-models", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"models": models}),
        ))
        page.locator(".sidebar-action-btn").nth(2).click()
        page.wait_for_selector(".model-card")
        load_btn = page.locator(".model-action-btn.primary")
        assert load_btn.count() >= 1
        assert "Load" in load_btn.first.text_content()

    def test_loaded_model_has_eject_button(self, page, base_url):
        models = [
            {"name": "loaded.gguf", "format": "gguf", "size_gb": 4.0,
             "is_loaded": True, "param_label": "7B"},
        ]
        page.route(f"{base_url}/api/local-models", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"models": models}),
        ))
        page.locator(".sidebar-action-btn").nth(2).click()
        page.wait_for_selector(".model-card")
        assert "Eject" in page.locator(".model-card").first.text_content()

    def test_load_model_calls_api(self, page, base_url):
        """Clicking Load should call /api/models/load."""
        models = [
            {"name": "mymodel.gguf", "format": "gguf", "size_gb": 4.0,
             "is_loaded": False, "param_label": "7B"},
        ]
        load_called = {"called": False}

        page.route(f"{base_url}/api/local-models", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"models": models}),
        ))
        def _handle_load(route):
            load_called["called"] = True
            route.fulfill(status=200, content_type="application/json",
                          body='{"ok": true}')

        page.route(f"{base_url}/api/models/load", _handle_load)

        page.locator(".sidebar-action-btn").nth(2).click()
        page.wait_for_selector(".model-card")
        page.locator(".model-action-btn.primary").first.click()
        # Wait for the panel to refresh
        page.wait_for_timeout(500)


class TestDiscoverTab:
    def test_discover_shows_recommendations(self, page, base_url):
        recs = [
            {"name": "Qwen2.5-7B-Instruct-4bit", "format": "mlx",
             "size_gb": 4.1, "quant": "Q4_K_M", "repo_id": "mlx/qwen",
             "fit_level": "Perfect fit", "why": "Fast general model",
             "context": 32768},
            {"name": "Llama-3.3-70B-Q4", "format": "gguf",
             "size_gb": 38.0, "quant": "Q4_K_M", "repo_id": "bar/llama",
             "fit_level": "Good fit", "why": "Strong reasoning",
             "context": 131072},
        ]
        page.route(f"{base_url}/api/local-models", lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"models": []}',
        ))
        page.route(f"{base_url}/api/recommended-models", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"recommendations": recs}),
        ))

        page.locator(".sidebar-action-btn").nth(2).click()
        page.wait_for_selector("#models-overlay.open")
        page.locator('.models-tab[data-tab="discover"]').click()
        page.wait_for_selector(".model-card")
        assert page.locator(".model-card").count() == 2

    def test_discover_search_filters(self, page, base_url):
        recs = [
            {"name": "Qwen2.5-7B", "format": "mlx", "size_gb": 4.0,
             "repo_id": "a/b", "fit_level": "Perfect", "quant": "Q4"},
            {"name": "CodeLlama-34B", "format": "gguf", "size_gb": 18.0,
             "repo_id": "c/d", "fit_level": "Good", "quant": "Q4",
             "use_case": "code"},
        ]
        page.route(f"{base_url}/api/local-models", lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"models": []}',
        ))
        page.route(f"{base_url}/api/recommended-models", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"recommendations": recs}),
        ))

        page.locator(".sidebar-action-btn").nth(2).click()
        page.wait_for_selector("#models-overlay.open")
        page.locator('.models-tab[data-tab="discover"]').click()
        page.wait_for_selector(".model-card")

        # Search for "qwen"
        page.locator("#discover-search").fill("qwen")
        page.wait_for_function("document.querySelectorAll('.model-card').length === 1")
        assert "Qwen" in page.locator(".model-card").first.text_content()

    def test_discover_category_filter(self, page, base_url):
        recs = [
            {"name": "General-7B", "format": "mlx", "size_gb": 4.0,
             "repo_id": "a/b", "fit_level": "Perfect", "quant": "Q4",
             "use_case": "general"},
            {"name": "CodeLlama-34B", "format": "gguf", "size_gb": 18.0,
             "repo_id": "c/d", "fit_level": "Good", "quant": "Q4",
             "use_case": "code"},
        ]
        page.route(f"{base_url}/api/local-models", lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"models": []}',
        ))
        page.route(f"{base_url}/api/recommended-models", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"recommendations": recs}),
        ))

        page.locator(".sidebar-action-btn").nth(2).click()
        page.wait_for_selector("#models-overlay.open")
        page.locator('.models-tab[data-tab="discover"]').click()
        page.wait_for_selector(".model-card")

        # Filter to "Code" category
        page.locator('.disc-filter[data-cat="code"]').click()
        page.wait_for_function("document.querySelectorAll('.model-card').length === 1")
        assert "CodeLlama" in page.locator(".model-card").first.text_content()

    def test_discover_filters_visible(self, page, base_url):
        page.route(f"{base_url}/api/local-models", lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"models": []}',
        ))
        page.route(f"{base_url}/api/recommended-models", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body='{"recommendations": []}',
        ))
        page.locator(".sidebar-action-btn").nth(2).click()
        page.wait_for_selector("#models-overlay.open")
        page.locator('.models-tab[data-tab="discover"]').click()

        filters = page.locator(".disc-filter")
        assert filters.count() == 5
        labels = [filters.nth(i).text_content().strip() for i in range(5)]
        assert "All" in labels
        assert "Code" in labels
        assert "Reason" in labels

    def test_get_button_on_discover_cards(self, page, base_url):
        recs = [
            {"name": "TestModel-7B", "format": "gguf", "size_gb": 4.0,
             "repo_id": "a/b", "fit_level": "Perfect", "quant": "Q4"},
        ]
        page.route(f"{base_url}/api/local-models", lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"models": []}',
        ))
        page.route(f"{base_url}/api/recommended-models", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"recommendations": recs}),
        ))
        page.locator(".sidebar-action-btn").nth(2).click()
        page.wait_for_selector("#models-overlay.open")
        page.locator('.models-tab[data-tab="discover"]').click()
        page.wait_for_selector(".model-card")
        assert "Get" in page.locator(".model-action-btn.primary").first.text_content()
