"""
Tests for input area: mode switching, context selector, research toggle,
formatting toolbar, paste, keyboard shortcuts, and model dropdown.
"""


class TestModeAndContextSelectors:
    def test_mode_switching(self, page):
        """Switch between modes, verify active state and description updates."""
        page.locator('.mode-tab[data-mode="code"]').click()
        assert "active" in page.locator('.mode-tab[data-mode="code"]').get_attribute("class")
        assert "active" not in page.locator('.mode-tab[data-mode="general"]').get_attribute("class")
        desc = page.locator("#model-desc").text_content()
        assert "Code" in desc or "debug" in desc.lower()

        page.locator('.mode-tab[data-mode="reason"]').click()
        assert "active" in page.locator('.mode-tab[data-mode="reason"]').get_attribute("class")

        page.locator('.mode-tab[data-mode="general"]').click()
        assert "active" in page.locator('.mode-tab[data-mode="general"]').get_attribute("class")
        assert "Vision" in page.locator("#model-desc").text_content()

    def test_context_window_selector(self, page):
        """Default 32k, can change, has all expected options."""
        assert page.locator("#ctx-select").input_value() == "32768"
        page.locator("#ctx-select").select_option("65536")
        assert page.locator("#ctx-select").input_value() == "65536"
        values = [page.locator("#ctx-select option").nth(i).get_attribute("value")
                  for i in range(page.locator("#ctx-select option").count())]
        assert "8192" in values
        assert "131072" in values
        assert "262144" in values


class TestResearchToggle:
    def test_toggle_on_off(self, page):
        btn = page.locator("#research-btn")
        assert "active" not in (btn.get_attribute("class") or "")

        page.locator("#research-btn").click()
        assert "active" in btn.get_attribute("class")
        assert "ON" in btn.get_attribute("title")

        page.locator("#research-btn").click()
        assert "active" not in btn.get_attribute("class")


class TestFormattingAndInput:
    def test_bold_italic_code_codeblock(self, page):
        """Formatting buttons wrap selected text in correct tags."""
        inp = page.locator("#input")

        # Bold
        inp.click()
        inp.type("hello")
        page.keyboard.press("Control+a")
        page.locator('.fmt-btn >> text="B"').click()
        assert "<strong>" in inp.inner_html() or "<b>" in inp.inner_html()

        # Clear and test italic
        inp.evaluate("el => el.innerHTML = ''")
        inp.type("hello")
        page.keyboard.press("Control+a")
        page.locator('.fmt-btn >> text="I"').click()
        assert "<em>" in inp.inner_html() or "<i>" in inp.inner_html()

        # Clear and test inline code
        inp.evaluate("el => el.innerHTML = ''")
        inp.type("hello")
        page.keyboard.press("Control+a")
        page.locator(".fmt-btn.mono").first.click()
        assert "<code>" in inp.inner_html()

        # Clear and test code block
        inp.evaluate("el => el.innerHTML = ''")
        inp.type("code")
        page.keyboard.press("Control+a")
        page.locator(".fmt-btn.mono").nth(1).click()
        assert "<pre>" in inp.inner_html()

    def test_paste_strips_html(self, page):
        page.locator("#input").click()
        page.evaluate("""() => {
            const el = document.getElementById('input');
            el.focus();
            const dt = new DataTransfer();
            dt.setData('text/plain', 'plain text only');
            dt.setData('text/html', '<b>rich</b> text');
            el.dispatchEvent(new ClipboardEvent('paste', {
                clipboardData: dt, bubbles: true, cancelable: true
            }));
        }""")
        assert "plain text only" in page.locator("#input").text_content()
        assert "<b>" not in page.locator("#input").inner_html()

    def test_keyboard_shortcuts(self, page):
        """Enter with empty input does nothing, Shift+Enter adds newline, Ctrl+A selects all."""
        inp = page.locator("#input")

        # Enter on empty = no bubble
        inp.click()
        page.keyboard.press("Enter")
        assert page.locator(".user-bubble").count() == 0

        # Shift+Enter adds newline
        inp.type("line one")
        page.keyboard.press("Shift+Enter")
        inp.type("line two")
        text = page.evaluate("getInputText()")
        assert "line one" in text
        assert "line two" in text

        # Ctrl+A + type replaces
        page.keyboard.press("Control+a")
        inp.type("replaced")
        assert "replaced" in inp.text_content()
        assert "line one" not in inp.text_content()

    def test_text_to_markdown_conversion(self, page):
        """Bold/italic formatted text converts to markdown on getInputText()."""
        inp = page.locator("#input")
        inp.click()
        inp.type("word")
        page.keyboard.press("Control+a")
        page.locator('.fmt-btn >> text="B"').click()
        assert "**" in page.evaluate("getInputText()")

        inp.evaluate("el => el.innerHTML = ''")
        inp.type("word")
        page.keyboard.press("Control+a")
        page.locator('.fmt-btn >> text="I"').click()
        assert "*" in page.evaluate("getInputText()")


class TestModelDropdown:
    def test_populated_dropdown(self, page, base_url):
        page.unroute(f"{base_url}/v1/models")
        page.route(f"{base_url}/v1/models", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body='{"data": [{"id": "qwen2.5-7b"}, {"id": "llama-3.3-70b"}]}',
        ))
        page.evaluate("refreshModelPicker()")
        page.wait_for_function(
            "document.getElementById('model-select').options.length === 2"
        )
        assert page.locator("#model-select option").count() == 2
