"""
Tests for initial page layout, sidebar, titlebar, and empty state.
"""



class TestPageLoad:
    def test_title_is_loca(self, page):
        assert page.title() == "Loca"

    def test_titlebar_visible(self, page):
        tb = page.locator("#titlebar")
        assert tb.is_visible()
        assert "Loca" in tb.locator("#tb-title").text_content()

    def test_sidebar_visible(self, page):
        assert page.locator("#sidebar").is_visible()

    def test_main_area_visible(self, page):
        assert page.locator("#main").is_visible()


class TestEmptyState:
    def test_empty_state_shown(self, page):
        empty = page.locator("#empty")
        assert empty.is_visible()
        assert "Start a conversation" in empty.text_content()

    def test_empty_state_has_icon(self, page):
        assert page.locator("#empty .e-icon").is_visible()

    def test_empty_state_instructions(self, page):
        assert "Select a model" in page.locator("#empty p").text_content()


class TestSidebar:
    def test_new_chat_button(self, page):
        btn = page.locator("#new-chat-btn")
        assert btn.is_visible()
        assert "New conversation" in btn.text_content()

    def test_mode_tabs_visible(self, page):
        tabs = page.locator(".mode-tab")
        assert tabs.count() == 3

    def test_mode_tabs_labels(self, page):
        labels = [page.locator(".mode-tab").nth(i).text_content().strip()
                  for i in range(3)]
        assert labels == ["General", "Code", "Reason"]

    def test_general_mode_active_by_default(self, page):
        general = page.locator('.mode-tab[data-mode="general"]')
        assert "active" in general.get_attribute("class")

    def test_mode_description_shown(self, page):
        desc = page.locator("#model-desc")
        assert desc.is_visible()
        assert "Vision" in desc.text_content()

    def test_model_select_dropdown(self, page):
        sel = page.locator("#model-select")
        assert sel.is_visible()

    def test_context_window_select(self, page):
        sel = page.locator("#ctx-select")
        assert sel.is_visible()
        # Default is 32k
        assert sel.input_value() == "32768"

    def test_context_window_options(self, page):
        options = page.locator("#ctx-select option")
        assert options.count() == 6

    def test_conversations_section(self, page):
        hdr = page.locator("#conv-section-hdr")
        assert "Conversations" in hdr.text_content()

    def test_sidebar_stats_visible(self, page):
        stats = page.locator("#sidebar-stats")
        assert stats.is_visible()

    def test_ram_stats_populated(self, page):
        ram = page.locator("#stat-ram")
        # Our mock returns 8.2 / 32.0 GB
        page.wait_for_function("document.getElementById('stat-ram').textContent.includes('GB')")
        assert "8.2" in ram.text_content()
        assert "32" in ram.text_content()

    def test_message_count_zero(self, page):
        assert page.locator("#stat-msgs").text_content() == "0"

    def test_memory_button_visible(self, page):
        assert page.locator("#mem-count-label").is_visible()

    def test_theme_button_visible(self, page):
        assert page.locator("#theme-btn").is_visible()

    def test_models_button_visible(self, page):
        # The third sidebar-action-btn is the models button
        btns = page.locator(".sidebar-action-btn")
        assert btns.count() == 3


class TestInputArea:
    def test_input_visible(self, page):
        assert page.locator("#input").is_visible()

    def test_input_placeholder(self, page):
        # The placeholder is a CSS ::before pseudo-element; check the element is empty
        inp = page.locator("#input")
        assert inp.text_content().strip() == ""

    def test_send_button_visible(self, page):
        btn = page.locator("#send-btn")
        assert btn.is_visible()
        assert "Send" in btn.text_content()

    def test_research_button_visible(self, page):
        btn = page.locator("#research-btn")
        assert btn.is_visible()
        assert "Research" in btn.text_content()

    def test_formatting_toolbar(self, page):
        assert page.locator("#fmt-bar").is_visible()
        # Bold, Italic, code, codeblock, attach = 5 buttons
        btns = page.locator("#fmt-bar .fmt-btn")
        assert btns.count() >= 4

    def test_input_hint(self, page):
        hint = page.locator("#input-hint")
        assert "Enter to send" in hint.text_content()
        assert "Shift+Enter" in hint.text_content()
