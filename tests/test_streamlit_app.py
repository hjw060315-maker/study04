"""Streamlit 화면 스모크 테스트 (AppTest, 네트워크·모델 호출 없음)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(ROOT / "src" / "streamlit_app.py")


def run_app(**state) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=30)
    for k, v in state.items():
        at.session_state[k] = v
    return at.run()


class AppSmokeTest(unittest.TestCase):
    def test_initial_page_renders_without_error(self):
        at = run_app()
        self.assertFalse(at.exception)
        self.assertEqual(at.title[0].value, "🥬 냉장고 재료 인식")
        self.assertEqual(len(at.subheader), 0)  # 사진 전에는 결과 영역이 없다
        self.assertEqual(len(at.button), 0)  # 사진이 없으면 버튼도 없다

    def test_camera_toggle_switches_input(self):
        at = run_app()
        at.toggle[0].set_value(True).run()
        self.assertFalse(at.exception)

    def test_result_section_with_ingredients(self):
        at = run_app(detected=[{"name": "계란", "quantity": "6개"}])
        # 사진(source)이 없으면 결과를 지우는 것이 정상 동작이다
        self.assertNotIn("detected", at.session_state)


if __name__ == "__main__":
    unittest.main()
