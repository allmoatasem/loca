"""
Tests for dark/light theme toggle and persistence.
"""


class TestThemeToggle:
    def test_toggle_and_persistence(self, page):
        """Toggle dark/light, verify class, localStorage, Prism CSS, icon."""
        page.evaluate("localStorage.removeItem('loca-theme')")
        page.reload()
        page.wait_for_load_state("domcontentloaded")

        # Default: light
        assert "dark" not in (page.locator("body").get_attribute("class") or "")
        assert "circle" in page.locator("#theme-icon").inner_html()

        # Toggle to dark
        page.locator("#theme-btn").click()
        assert "dark" in page.locator("body").get_attribute("class")
        assert page.evaluate("localStorage.getItem('loca-theme')") == "dark"
        assert "dark" in page.locator("#prism-css").get_attribute("href")

        # Toggle back to light
        page.locator("#theme-btn").click()
        assert "dark" not in (page.locator("body").get_attribute("class") or "")
        assert page.evaluate("localStorage.getItem('loca-theme')") == "light"
        assert "light" in page.locator("#prism-css").get_attribute("href")

    def test_theme_persists_across_reload(self, page):
        """Dark theme survives a page reload."""
        page.evaluate("localStorage.removeItem('loca-theme')")
        page.reload()
        page.wait_for_load_state("domcontentloaded")

        page.locator("#theme-btn").click()
        assert "dark" in page.locator("body").get_attribute("class")

        page.reload()
        page.wait_for_load_state("domcontentloaded")
        assert "dark" in page.locator("body").get_attribute("class")
