"""
Tests for dark/light theme toggle and persistence.
"""



class TestThemeToggle:
    def test_default_theme_is_light(self, page):
        """By default (no localStorage), body should not have 'dark' class."""
        # Clear any stored preference
        page.evaluate("localStorage.removeItem('loca-theme')")
        page.reload()
        page.wait_for_load_state("networkidle")
        assert "dark" not in (page.locator("body").get_attribute("class") or "")

    def test_toggle_to_dark(self, page):
        page.evaluate("localStorage.removeItem('loca-theme')")
        page.reload()
        page.wait_for_load_state("networkidle")
        page.locator("#theme-btn").click()
        assert "dark" in page.locator("body").get_attribute("class")

    def test_toggle_back_to_light(self, page):
        page.evaluate("localStorage.removeItem('loca-theme')")
        page.reload()
        page.wait_for_load_state("networkidle")
        page.locator("#theme-btn").click()
        assert "dark" in page.locator("body").get_attribute("class")
        page.locator("#theme-btn").click()
        assert "dark" not in (page.locator("body").get_attribute("class") or "")

    def test_theme_persists_across_reload(self, page):
        """Toggling to dark should persist after page reload."""
        page.evaluate("localStorage.removeItem('loca-theme')")
        page.reload()
        page.wait_for_load_state("networkidle")
        page.locator("#theme-btn").click()
        assert "dark" in page.locator("body").get_attribute("class")

        # Reload and check
        page.reload()
        page.wait_for_load_state("networkidle")
        assert "dark" in page.locator("body").get_attribute("class")

    def test_theme_saves_to_localstorage(self, page):
        page.evaluate("localStorage.removeItem('loca-theme')")
        page.reload()
        page.wait_for_load_state("networkidle")
        page.locator("#theme-btn").click()
        val = page.evaluate("localStorage.getItem('loca-theme')")
        assert val == "dark"

    def test_light_theme_saves_to_localstorage(self, page):
        page.evaluate("localStorage.setItem('loca-theme', 'dark')")
        page.reload()
        page.wait_for_load_state("networkidle")
        page.locator("#theme-btn").click()
        val = page.evaluate("localStorage.getItem('loca-theme')")
        assert val == "light"

    def test_prism_css_switches_on_dark(self, page):
        """Toggling dark should switch Prism CSS to the dark variant."""
        page.evaluate("localStorage.removeItem('loca-theme')")
        page.reload()
        page.wait_for_load_state("networkidle")
        page.locator("#theme-btn").click()
        href = page.locator("#prism-css").get_attribute("href")
        assert "dark" in href

    def test_prism_css_switches_on_light(self, page):
        page.evaluate("localStorage.setItem('loca-theme', 'dark')")
        page.reload()
        page.wait_for_load_state("networkidle")
        page.locator("#theme-btn").click()
        href = page.locator("#prism-css").get_attribute("href")
        assert "light" in href

    def test_theme_icon_changes(self, page):
        """The theme icon SVG should change between sun and moon."""
        page.evaluate("localStorage.removeItem('loca-theme')")
        page.reload()
        page.wait_for_load_state("networkidle")
        # In light mode, icon should have circle (sun)
        light_html = page.locator("#theme-icon").inner_html()
        assert "circle" in light_html

        page.locator("#theme-btn").click()
        # In dark mode, icon should have path (moon)
        dark_html = page.locator("#theme-icon").inner_html()
        assert "circle" not in dark_html or "21 12.79" in dark_html
