import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MotionFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.styles = (ROOT / "portal" / "web" / "styles.css").read_text()

    def test_loading_progress_uses_shared_motion(self):
        self.assertIn("@keyframes portal-progress", self.styles)
        self.assertIn("animation: portal-progress .9s", self.styles)
        self.assertIn("animation:portal-progress 1s", self.styles)

    def test_drawer_and_modal_have_entrance_motion(self):
        self.assertIn("animation: portal-drawer-in", self.styles)
        self.assertIn("animation: portal-modal-in", self.styles)
        self.assertIn("animation-name: portal-sheet-in", self.styles)
        self.assertIn("animation: portal-overlay-in", self.styles)

    def test_reduced_motion_disables_repeated_or_decorative_motion(self):
        self.assertIn("@media (prefers-reduced-motion: reduce)", self.styles)
        self.assertIn(".column-drawer-overlay, .column-drawer { animation: none; }", self.styles)
        self.assertIn("animation-iteration-count: 1 !important", self.styles)
        self.assertIn(".refresh-status::after { animation:none; width:100%; }", self.styles)


if __name__ == "__main__":
    unittest.main()
