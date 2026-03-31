"""
Tests for input area: formatting toolbar, paste behaviour, research toggle,
keyboard shortcuts, mode switching, and context window selector.
"""



class TestModeSelection:
    def test_switch_to_code_mode(self, page):
        page.locator('.mode-tab[data-mode="code"]').click()
        assert "active" in page.locator('.mode-tab[data-mode="code"]').get_attribute("class")
        assert "active" not in page.locator('.mode-tab[data-mode="general"]').get_attribute("class")

    def test_switch_to_reason_mode(self, page):
        page.locator('.mode-tab[data-mode="reason"]').click()
        assert "active" in page.locator('.mode-tab[data-mode="reason"]').get_attribute("class")

    def test_mode_description_updates(self, page):
        page.locator('.mode-tab[data-mode="code"]').click()
        desc = page.locator("#model-desc").text_content()
        assert "Code" in desc or "debug" in desc.lower()

    def test_switch_back_to_general(self, page):
        page.locator('.mode-tab[data-mode="code"]').click()
        page.locator('.mode-tab[data-mode="general"]').click()
        assert "active" in page.locator('.mode-tab[data-mode="general"]').get_attribute("class")
        desc = page.locator("#model-desc").text_content()
        assert "Vision" in desc


class TestContextWindowSelector:
    def test_default_context_value(self, page):
        assert page.locator("#ctx-select").input_value() == "32768"

    def test_change_context_window(self, page):
        page.locator("#ctx-select").select_option("65536")
        assert page.locator("#ctx-select").input_value() == "65536"

    def test_all_context_options(self, page):
        options = page.locator("#ctx-select option")
        values = [options.nth(i).get_attribute("value") for i in range(options.count())]
        assert "8192" in values
        assert "131072" in values
        assert "262144" in values


class TestResearchToggle:
    def test_research_off_by_default(self, page):
        btn = page.locator("#research-btn")
        assert "active" not in (btn.get_attribute("class") or "")

    def test_toggle_research_on(self, page):
        page.locator("#research-btn").click()
        assert "active" in page.locator("#research-btn").get_attribute("class")

    def test_toggle_research_off(self, page):
        page.locator("#research-btn").click()
        page.locator("#research-btn").click()
        assert "active" not in page.locator("#research-btn").get_attribute("class")

    def test_research_mode_title_updates(self, page):
        page.locator("#research-btn").click()
        title = page.locator("#research-btn").get_attribute("title")
        assert "ON" in title


class TestFormattingToolbar:
    def test_bold_button_inserts_strong(self, page):
        inp = page.locator("#input")
        inp.click()
        inp.type("hello")
        # Select text
        page.keyboard.press("Control+a")
        page.locator('.fmt-btn >> text="B"').click()
        html = inp.inner_html()
        assert "<strong>" in html or "<b>" in html

    def test_italic_button_inserts_em(self, page):
        inp = page.locator("#input")
        inp.click()
        inp.type("hello")
        page.keyboard.press("Control+a")
        page.locator('.fmt-btn >> text="I"').click()
        html = inp.inner_html()
        assert "<em>" in html or "<i>" in html

    def test_code_button_inserts_code(self, page):
        inp = page.locator("#input")
        inp.click()
        inp.type("hello")
        page.keyboard.press("Control+a")
        # Click the inline code button (the one with backtick styling)
        code_btns = page.locator(".fmt-btn.mono")
        code_btns.first.click()
        html = inp.inner_html()
        assert "<code>" in html

    def test_codeblock_button_inserts_pre(self, page):
        inp = page.locator("#input")
        inp.click()
        inp.type("some code")
        page.keyboard.press("Control+a")
        code_btns = page.locator(".fmt-btn.mono")
        code_btns.nth(1).click()
        html = inp.inner_html()
        assert "<pre>" in html


class TestPasteBehavior:
    def test_paste_strips_html(self, page):
        """Pasting rich text should result in plain text only."""
        inp = page.locator("#input")
        inp.click()
        # Simulate a plain text paste via keyboard
        page.evaluate("""() => {
            const el = document.getElementById('input');
            el.focus();
            const dt = new DataTransfer();
            dt.setData('text/plain', 'plain text only');
            dt.setData('text/html', '<b>rich</b> text');
            const event = new ClipboardEvent('paste', {
                clipboardData: dt,
                bubbles: true,
                cancelable: true
            });
            el.dispatchEvent(event);
        }""")
        text = inp.text_content()
        assert "plain text only" in text
        assert "<b>" not in inp.inner_html()


class TestKeyboardShortcuts:
    def test_enter_sends_message(self, page, base_url):
        """Pressing Enter should trigger sendMessage."""
        # Just check that pressing Enter with empty input doesn't crash
        page.locator("#input").click()
        page.keyboard.press("Enter")
        # No user bubble should appear (empty message)
        assert page.locator(".user-bubble").count() == 0

    def test_shift_enter_adds_newline(self, page):
        """Shift+Enter should add a line break, not send."""
        inp = page.locator("#input")
        inp.click()
        inp.type("line one")
        page.keyboard.press("Shift+Enter")
        inp.type("line two")
        text = page.evaluate("getInputText()")
        assert "line one" in text
        assert "line two" in text

    def test_ctrl_a_selects_all(self, page):
        """Ctrl+A should select all text in the input."""
        inp = page.locator("#input")
        inp.click()
        inp.type("select me")
        page.keyboard.press("Control+a")
        # After select-all, typing should replace all text
        inp.type("replaced")
        text = inp.text_content()
        assert "replaced" in text
        assert "select me" not in text


class TestInputTextConversion:
    def test_plain_text_extraction(self, page):
        """getInputText() should return plain text from the contenteditable."""
        page.locator("#input").click()
        page.locator("#input").type("Hello world")
        text = page.evaluate("getInputText()")
        assert text == "Hello world"

    def test_bold_text_converted_to_markdown(self, page):
        """Bold text should be converted to **markdown** on send."""
        inp = page.locator("#input")
        inp.click()
        inp.type("word")
        page.keyboard.press("Control+a")
        page.locator('.fmt-btn >> text="B"').click()
        text = page.evaluate("getInputText()")
        assert "**" in text

    def test_italic_text_converted_to_markdown(self, page):
        inp = page.locator("#input")
        inp.click()
        inp.type("word")
        page.keyboard.press("Control+a")
        page.locator('.fmt-btn >> text="I"').click()
        text = page.evaluate("getInputText()")
        assert "*" in text


class TestModelDropdown:
    def test_no_model_loaded_message(self, page):
        """When no models are loaded, dropdown shows appropriate message."""
        text = page.locator("#model-select").text_content()
        # Could be "No model loaded" or "Loading…" depending on timing
        assert "No model" in text or "Loading" in text

    def test_model_dropdown_populated(self, page, base_url):
        """When models are available, dropdown should list them."""
        page.unroute(f"{base_url}/v1/models")
        page.route(f"{base_url}/v1/models", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body='{"data": [{"id": "qwen2.5-7b"}, {"id": "llama-3.3-70b"}]}',
        ))
        page.evaluate("refreshModelPicker()")
        page.wait_for_function(
            "document.getElementById('model-select').options.length === 2"
        )
        options = page.locator("#model-select option")
        assert options.count() == 2
