"""
Tests for the vault analyser panel: open/close, auto-detect, scan, tabs.
"""

import json

_MOCK_ANALYSIS = {
    "stats": {"note_count": 42, "link_count": 87, "total_words": 15000,
              "tag_count": 12, "top_tags": [{"tag": "python", "count": 8}, {"tag": "ml", "count": 5}],
              "folder_count": 6},
    "orphans": [{"rel_path": "orphan.md", "title": "Orphan Note", "word_count": 120, "has_outgoing_links": False}],
    "dead_ends": [{"rel_path": "dead.md", "title": "Dead End", "word_count": 50}],
    "broken_links": [{"from_note": "a.md", "to_note": "missing", "link_type": "wiki"}],
    "tag_orphans": [{"tag": "one-off", "note": "random.md"}],
    "link_suggestions": [{"note_a": {"rel_path": "x.md", "title": "X"}, "note_b": {"rel_path": "y.md", "title": "Y"},
                          "shared_tags": ["python"], "score": 2, "reason": "Share 2 tags: python, ml"}],
}

_EMPTY_ANALYSIS = {
    "stats": {"note_count": 0, "link_count": 0, "total_words": 0, "tag_count": 0, "top_tags": [], "folder_count": 0},
    "orphans": [], "dead_ends": [], "broken_links": [], "tag_orphans": [], "link_suggestions": [],
}


def _setup_vault_routes(page, base_url, vaults=None, analysis=None):
    """Set up all vault API routes before opening the panel."""
    v = vaults if vaults is not None else [{"name": "studio", "path": "/Users/test/vault"}]
    a = analysis or _MOCK_ANALYSIS

    page.route(f"{base_url}/api/vault/detect", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"vaults": v}),
    ))
    page.route(f"{base_url}/api/vault/analysis**", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps(a),
    ))
    page.route(f"{base_url}/api/vault/scan", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"ok": True, "total": 42, "added": 42, "updated": 0, "skipped": 0, "removed": 0, "errors": 0}),
    ))


def _open_vault(page, base_url, vaults=None, analysis=None):
    """Set up routes and open the vault panel."""
    _setup_vault_routes(page, base_url, vaults, analysis)
    page.locator(".sidebar-action-btn").nth(2).click()
    page.wait_for_selector("#vault-overlay.open")


class TestVaultPanel:
    def test_open_close(self, page, base_url):
        """Panel opens and closes."""
        _open_vault(page, base_url)
        assert page.locator("#vault-overlay.open").is_visible()

        page.locator("#vault-panel .panel-close").click()
        page.wait_for_function(
            "!document.getElementById('vault-overlay').classList.contains('open')"
        )

    def test_overview_tab_shows_stats(self, page, base_url):
        """Overview tab shows stat cards and tags."""
        _open_vault(page, base_url)
        page.wait_for_function(
            "document.getElementById('vault-body').textContent.includes('42')"
        )
        body = page.locator("#vault-body").text_content()
        assert "42" in body
        assert "87" in body
        assert "#python" in body

    def test_tab_switching(self, page, base_url):
        """Clicking tabs switches content."""
        _open_vault(page, base_url)
        # Wait for overview to load first
        page.wait_for_function(
            "document.getElementById('vault-body').textContent.includes('42')"
        )

        page.locator("#vault-tabs button", has_text="Orphans").click()
        page.wait_for_function(
            "document.getElementById('vault-body').textContent.includes('Orphan Note')"
        )

        page.locator("#vault-tabs button", has_text="Broken Links").click()
        page.wait_for_function(
            "document.getElementById('vault-body').textContent.includes('missing')"
        )

        page.locator("#vault-tabs button", has_text="Suggestions").click()
        page.wait_for_function(
            "document.getElementById('vault-body').textContent.includes('Share 2 tags')"
        )

    def test_scan_button(self, page, base_url):
        """Scan button triggers scan and refreshes analysis."""
        _open_vault(page, base_url)
        page.wait_for_function(
            "document.getElementById('vault-body').textContent.includes('42')"
        )
        page.locator("#vault-scan-btn").click()
        # After scan, analysis reloads — still shows 42
        page.wait_for_function(
            "document.getElementById('vault-body').textContent.includes('42')"
        )

    def test_unindexed_vault_shows_prompt(self, page, base_url):
        """Vault with 0 notes shows scan prompt."""
        _open_vault(page, base_url, analysis=_EMPTY_ANALYSIS)
        page.wait_for_function(
            "document.getElementById('vault-body').textContent.includes('not indexed')"
        )

    def test_no_vaults_detected(self, page, base_url):
        """When no vaults detected, path input is visible."""
        _open_vault(page, base_url, vaults=[])
        assert page.locator("#vault-path-input").is_visible()
