"""
Tests for initial page layout, sidebar, titlebar, empty state, and input area.
"""


class TestPageLayout:
    def test_page_structure(self, page):
        """Title, titlebar, sidebar, main area, and empty state all render."""
        assert page.title() == "Loca"
        assert page.locator("#titlebar").is_visible()
        assert "Loca" in page.locator("#tb-title").text_content()
        assert page.locator("#sidebar").is_visible()
        assert page.locator("#main").is_visible()

        # Empty state
        empty = page.locator("#empty")
        assert empty.is_visible()
        assert "Start a conversation" in empty.text_content()
        assert page.locator("#empty .e-icon").is_visible()

    def test_sidebar_controls(self, page):
        """New chat button, mode tabs, model select, ctx select, conv section."""
        btn = page.locator("#new-chat-btn")
        assert btn.is_visible()
        assert "New conversation" in btn.text_content()

        # Mode tabs: General (active), Code, Reason
        tabs = page.locator(".mode-tab")
        assert tabs.count() == 3
        labels = [tabs.nth(i).text_content().strip() for i in range(3)]
        assert labels == ["General", "Code", "Reason"]
        assert "active" in page.locator('.mode-tab[data-mode="general"]').get_attribute("class")

        # Mode description
        assert "Vision" in page.locator("#model-desc").text_content()

        # Model + context dropdowns
        assert page.locator("#model-select").is_visible()
        assert page.locator("#ctx-select").input_value() == "32768"
        assert page.locator("#ctx-select option").count() == 6

        # Conversations section
        assert "Conversations" in page.locator("#conv-section-hdr").text_content()

    def test_sidebar_stats_and_actions(self, page):
        """RAM stats, message count, memory/theme/models buttons."""
        assert page.locator("#sidebar-stats").is_visible()
        page.wait_for_function(
            "document.getElementById('stat-ram').textContent.includes('GB')"
        )
        assert "8.2" in page.locator("#stat-ram").text_content()
        assert page.locator("#stat-msgs").text_content() == "0"

        assert page.locator("#mem-count-label").is_visible()
        assert page.locator("#theme-btn").is_visible()
        assert page.locator(".sidebar-action-btn").count() == 5

    def test_input_area(self, page):
        """Input, send button, research button, formatting toolbar, hint."""
        assert page.locator("#input").is_visible()
        assert page.locator("#input").text_content().strip() == ""

        btn = page.locator("#send-btn")
        assert btn.is_visible()
        assert "Send" in btn.text_content()

        assert page.locator("#research-btn").is_visible()
        assert page.locator("#fmt-bar").is_visible()
        assert page.locator("#fmt-bar .fmt-btn").count() >= 4

        # Input hint was removed from the HTML; verify the input area itself is functional
        assert page.locator("#input-footer").is_visible()
