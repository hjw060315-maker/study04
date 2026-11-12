"""Streamlit 화면 스모크 테스트 (AppTest, 네트워크·모델 호출 없음)."""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import recipes  # noqa: E402
from errors import AppError  # noqa: E402
from ratelimit import SlidingWindowLimiter  # noqa: E402
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

    def test_restored_ingredients_are_kept_without_photo(self):
        at = run_app(detected=[{"name": "계란", "quantity": "6개"}], restored=True)
        self.assertFalse(at.exception)
        self.assertEqual(at.subheader[0].value, "인식된 재료")  # 2단계에서 돌아오면 사진 없이도 목록이 남는다


INGREDIENTS = [{"name": "계란", "quantity": "6개"}, {"name": "양파", "quantity": None}]


def make_recipe(title="계란 양파 볶음", **extra):
    return {
        "title": title, "summary": "간단한 요리", "servings": 2, "time_minutes": 15, "difficulty": "쉬움",
        "ingredients": [{"name": "계란", "amount": "2개", "have": True}, {"name": "대파", "amount": "1대", "have": False}],
        "missing_ingredients": ["대파"], "steps": ["재료를 손질한다.", "볶는다."], **extra,
    }


def captions(at) -> str:
    return "\n".join(c.value for c in at.caption)


class Step2Test(unittest.TestCase):
    def run_step2(self, **state):
        return run_app(step=2, final_ingredients=INGREDIENTS, **state)

    def test_renders_options_and_button(self):
        at = self.run_step2()
        self.assertFalse(at.exception)
        self.assertEqual(at.title[0].value, "🍳 레시피 추천")
        self.assertEqual(len(at.selectbox), 3)  # 인원, 조리 시간, 식사 종류
        self.assertEqual(len(at.text_input), 1)  # 제외 재료
        self.assertEqual([b.label for b in at.button], ["← 재료 다시 고치기", "레시피 만들기"])
        self.assertIn("`계란`", at.markdown[0].value)  # 재료 칩

    def test_without_ingredients_goes_back_to_step1(self):
        at = run_app(step=2)
        self.assertEqual(at.title[0].value, "🥬 냉장고 재료 인식")

    def test_generate_shows_recipe_cards_and_model(self):
        result = recipes.RecipeResult([make_recipe(), make_recipe("두 번째 요리")], "baidu/test:free")
        with mock.patch.object(recipes, "generate_recipes", return_value=result) as gen:
            at = self.run_step2()
            at.button[1].click().run()
        self.assertFalse(at.exception)
        self.assertEqual([s.value for s in at.subheader], ["계란 양파 볶음", "두 번째 요리"])
        self.assertIn("🛒 부족한 재료: 대파", captions(at))
        self.assertIn("레시피 모델: baidu/test:free", captions(at))
        self.assertEqual(gen.call_args.kwargs["servings"], 2)
        self.assertEqual([b.label for b in at.button][-1], "다시 추천받기")

    def test_model_text_is_escaped_not_rendered_as_markdown(self):
        evil = make_recipe("[클릭](http://evil.example) $x$", summary="![img](http://evil.example/a.png)")
        with mock.patch.object(recipes, "generate_recipes", return_value=recipes.RecipeResult([evil], "m:free")):
            at = self.run_step2()
            at.button[1].click().run()
        self.assertNotIn("[클릭](", at.subheader[0].value)  # 링크 문법이 이스케이프됨
        self.assertIn("\\[클릭\\]", at.subheader[0].value)

    def test_failure_is_reported_and_old_recipes_are_cleared(self):
        old = [make_recipe("예전 레시피")]
        err = AppError("model_unavailable", "무료 모델이 지금 혼잡하거나 응답하지 않아요.", 503)
        with mock.patch.object(recipes, "generate_recipes", side_effect=err):
            at = self.run_step2(recipes=old, recipes_model="old:free")
            at.button[1].click().run()
        self.assertEqual([e.value for e in at.error], ["무료 모델이 지금 혼잡하거나 응답하지 않아요."])
        self.assertEqual(len(at.subheader), 0)  # 이전 결과가 이번 결과처럼 남지 않는다
        self.assertNotIn("recipes", at.session_state)

    def test_unexpected_error_hides_details(self):
        with mock.patch.object(recipes, "generate_recipes", side_effect=RuntimeError("비밀 내부 정보")):
            at = self.run_step2()
            at.button[1].click().run()
        self.assertEqual(len(at.error), 1)
        self.assertNotIn("비밀 내부 정보", at.error[0].value)

    def test_session_limit_blocks_model_call(self):
        with mock.patch.object(recipes, "generate_recipes") as gen:
            at = self.run_step2(limiter=SlidingWindowLimiter(limit=0, window=60))
            at.button[1].click().run()
        gen.assert_not_called()
        self.assertIn("요청이 너무 많아요", at.error[0].value)

    def test_back_button_returns_to_step1_with_ingredients(self):
        at = self.run_step2()
        at.button[0].click().run()
        self.assertEqual(at.title[0].value, "🥬 냉장고 재료 인식")
        self.assertEqual(at.subheader[0].value, "인식된 재료")
        self.assertEqual([i["name"] for i in at.session_state["detected"]], ["계란", "양파"])


if __name__ == "__main__":
    unittest.main()
