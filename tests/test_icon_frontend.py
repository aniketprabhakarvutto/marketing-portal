from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class IconFrontendTests(unittest.TestCase):
    def test_dynamic_icons_use_the_internal_svg_set(self) -> None:
        script = (ROOT / "portal/web/app.js").read_text(encoding="utf-8")

        self.assertIn('const ICONS = {', script)
        self.assertIn('label: "Scheduled data sync", icon: "refresh"', script)
        self.assertIn('label: "Data-quality checks", icon: "quality"', script)
        self.assertIn('ICONS[type.icon] || ICONS.rules', script)
        self.assertNotRegex(script, r'icon:\s*"[↻▤✦◎✓⇧◫⌘]"')

    def test_decorative_icons_are_hidden_and_icon_buttons_are_named(self) -> None:
        index = (ROOT / "portal/web/index.html").read_text(encoding="utf-8")
        invitations = (ROOT / "portal/web/invitations.html").read_text(encoding="utf-8")

        self.assertIn('id="automationChoiceIcon" aria-hidden="true"><svg', index)
        self.assertIn('class="feature-icon" aria-hidden="true"><svg', index)
        self.assertIn('id="assistantClose" type="button" aria-label="Close AI assistant"><svg', index)
        self.assertIn('class="success-mark" aria-hidden="true"><svg', invitations)

        icon_only_buttons = re.findall(
            r'<button[^>]+class="[^"]*(?:icon-only-action|password-toggle)[^"]*"[^>]*>',
            index,
        )
        self.assertTrue(icon_only_buttons)
        self.assertTrue(all('aria-label=' in button for button in icon_only_buttons))

    def test_no_flaticon_asset_or_runtime_dependency_was_added(self) -> None:
        web_files = list((ROOT / "portal/web").glob("*.html"))
        web_files += list((ROOT / "portal/web").glob("*.js"))
        web_files += list((ROOT / "portal/web").glob("*.css"))
        combined = "\n".join(path.read_text(encoding="utf-8") for path in web_files)

        self.assertNotIn("flaticon.com", combined.lower())
        self.assertNotIn("cdn.flaticon.com", combined.lower())

    def test_brand_logo_links_to_overview(self) -> None:
        index = (ROOT / "portal/web/index.html").read_text(encoding="utf-8")

        self.assertIn('class="brand-lockup brand-home-link" href="#overview"', index)
        self.assertIn('aria-label="Vortex Veri home"', index)


if __name__ == "__main__":
    unittest.main()
