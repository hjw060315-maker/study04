import io
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import ingredients  # noqa: E402
from PIL import Image  # noqa: E402
from ingredients import (  # noqa: E402
    AppError, normalize_items, parse_ingredients, recognize, shrink_image, validate_image,
)
from openrouter import ModelError  # noqa: E402
from ratelimit import SlidingWindowLimiter  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
GOOD = '{"ingredients": [{"name": "계란", "quantity": "6개"}, {"name": "양파", "quantity": null}]}'


def make_image(size, fmt="PNG", mode="RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, "red").save(buf, fmt)
    return buf.getvalue()


class ParseTest(unittest.TestCase):
    def test_plain_and_fenced_json(self):
        self.assertEqual([i["name"] for i in parse_ingredients(GOOD)], ["계란", "양파"])
        self.assertEqual(len(parse_ingredients("```json\n" + GOOD + "\n```")), 2)

    def test_dedupe_and_string_items(self):
        out = parse_ingredients('{"ingredients": ["대 파", {"name": "대파"}, {"name": " "}, 3]}')
        self.assertEqual(out, [{"name": "대 파", "quantity": None}])

    def test_empty_list_is_valid(self):
        self.assertEqual(parse_ingredients('{"ingredients": []}'), [])

    def test_bad_format_raises(self):
        for bad in ("죄송합니다", '{"foo": 1}', "{broken"):
            with self.assertRaises(ValueError):
                parse_ingredients(bad)


class NormalizeTest(unittest.TestCase):
    def test_handles_pandas_style_blanks(self):
        # st.data_editor가 돌려주는 빈 칸은 NaN, 이름이 빈 행은 None/NaN
        out = normalize_items([
            {"name": " 두부 ", "quantity": float("nan")},
            {"name": float("nan"), "quantity": "1개"},
            {"name": None, "quantity": None},
            {"name": "우유", "quantity": ""},
        ])
        self.assertEqual(out, [{"name": "두부", "quantity": None}, {"name": "우유", "quantity": None}])

    def test_limits(self):
        out = normalize_items([{"name": f"재료{i}"} for i in range(80)])
        self.assertEqual(len(out), ingredients.MAX_ITEMS)
        self.assertEqual(len(normalize_items([{"name": "가" * 100}])[0]["name"]), ingredients.MAX_NAME_LEN)


class ValidateTest(unittest.TestCase):
    def test_accepts_known_signatures(self):
        self.assertEqual(validate_image(PNG), "image/png")
        self.assertEqual(validate_image(b"\xff\xd8\xff" + b"0" * 10), "image/jpeg")
        self.assertEqual(validate_image(b"RIFF\0\0\0\0WEBPVP8 "), "image/webp")

    def test_rejects_bad_files(self):
        for data, code in ((b"", "invalid_image"), (b"GIF89a....", "invalid_image"),
                           (PNG + b"0" * ingredients.MAX_IMAGE_BYTES, "too_large")):
            with self.assertRaises(AppError) as cm:
                validate_image(data)
            self.assertEqual(cm.exception.code, code)


class ShrinkTest(unittest.TestCase):
    def test_large_image_is_resized_to_jpeg(self):
        out = shrink_image(make_image((3000, 2000)))
        img = Image.open(io.BytesIO(out))
        self.assertEqual(img.format, "JPEG")
        self.assertEqual(max(img.size), ingredients.MAX_SIDE)
        self.assertEqual(img.size, (1280, 853))  # 비율 유지

    def test_small_image_not_enlarged(self):
        img = Image.open(io.BytesIO(shrink_image(make_image((200, 100)))))
        self.assertEqual(img.size, (200, 100))

    def test_transparent_png_gets_white_background(self):
        img = Image.open(io.BytesIO(shrink_image(make_image((50, 50), mode="RGBA"))))
        self.assertEqual(img.mode, "RGB")

    def test_webp_input(self):
        self.assertEqual(Image.open(io.BytesIO(shrink_image(make_image((100, 100), "WEBP")))).format, "JPEG")

    def test_corrupt_image_raises_app_error(self):
        with self.assertRaises(AppError) as cm:
            shrink_image(PNG)  # 시그니처만 맞고 내용이 깨진 파일
        self.assertEqual(cm.exception.code, "invalid_image")


PRIMARY = ingredients.VISION_MODEL
FALLBACKS = ingredients.FALLBACK_MODELS
ALL_MODELS = [PRIMARY, *FALLBACKS]


def models_called(chat_mock) -> list[str]:
    return [c.args[0] for c in chat_mock.call_args_list]


class RecognizeTest(unittest.TestCase):
    def setUp(self):  # 재시도 대기 시간 때문에 테스트가 느려지지 않게
        patcher = mock.patch.object(ingredients.time, "sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def test_success_with_primary_model(self):
        with mock.patch.object(ingredients, "chat", return_value=GOOD) as m:
            result = recognize(PNG)
        self.assertEqual(len(result.items), 2)
        self.assertEqual(result.model, PRIMARY)
        self.assertEqual(models_called(m), [PRIMARY])

    def test_429_retries_same_model_once_then_succeeds(self):
        with mock.patch.object(ingredients, "chat", side_effect=[ModelError("HTTP 429", True, 429), GOOD]) as m:
            result = recognize(PNG)
        self.assertEqual(result.model, PRIMARY)
        self.assertEqual(models_called(m), [PRIMARY, PRIMARY])
        self.sleep.assert_called_once_with(ingredients.RETRY_WAIT)

    def test_falls_back_after_primary_fails_twice(self):
        err = ModelError("HTTP 429", True, 429)
        with mock.patch.object(ingredients, "chat", side_effect=[err, err, GOOD]) as m:
            result = recognize(PNG)
        self.assertEqual(result.model, FALLBACKS[0])
        self.assertEqual(models_called(m), [PRIMARY, PRIMARY, FALLBACKS[0]])

    def test_parse_failure_also_triggers_retry_and_fallback(self):
        with mock.patch.object(ingredients, "chat", side_effect=["JSON 아님", "JSON 아님", GOOD]) as m:
            result = recognize(PNG)
        self.assertEqual(result.model, FALLBACKS[0])
        self.assertEqual(len(models_called(m)), 3)

    def test_each_fallback_is_tried_once(self):
        err = ModelError("HTTP 429", True, 429)
        with mock.patch.object(ingredients, "chat", side_effect=err) as m:
            with self.assertRaises(AppError) as cm:
                recognize(PNG)
        self.assertEqual(cm.exception.code, "model_unavailable")
        self.assertEqual(models_called(m), [PRIMARY, PRIMARY, *FALLBACKS])

    def test_all_models_return_bad_format(self):
        with mock.patch.object(ingredients, "chat", return_value="계속 JSON 아님"):
            with self.assertRaises(AppError) as cm:
                recognize(PNG)
        self.assertEqual(cm.exception.code, "parse_failed")

    def test_forbidden_model_is_skipped_without_retry(self):
        # 403(이 모델은 쓸 수 없음)은 재시도하지 않고 바로 다음 모델로 넘어간다
        with mock.patch.object(ingredients, "chat", side_effect=[ModelError("HTTP 403", False, 403), GOOD]) as m:
            result = recognize(PNG)
        self.assertEqual(models_called(m), [PRIMARY, FALLBACKS[0]])
        self.assertEqual(result.model, FALLBACKS[0])
        self.sleep.assert_not_called()

    def test_bad_api_key_stops_immediately(self):
        with mock.patch.object(ingredients, "chat", side_effect=ModelError("HTTP 401", False, 401)) as m:
            with self.assertRaises(AppError) as cm:
                recognize(PNG)
        self.assertEqual(cm.exception.code, "server_config")
        self.assertEqual(m.call_count, 1)

    def test_progress_messages_show_retry_and_fallback(self):
        err = ModelError("HTTP 429", True, 429)
        log: list[str] = []
        with mock.patch.object(ingredients, "chat", side_effect=[err, err, GOOD]):
            recognize(PNG, on_progress=log.append)
        text = "\n".join(log)
        self.assertIn(f"1차 시도: {PRIMARY}", text)
        self.assertIn(f"2차 시도: {PRIMARY}", text)
        self.assertIn(f"폴백 모델로 전환: {FALLBACKS[0]}", text)


class RateLimitTest(unittest.TestCase):
    def test_blocks_after_limit_and_recovers(self):
        lim = SlidingWindowLimiter(limit=2, window=60)
        self.assertTrue(lim.try_acquire(now=0))
        self.assertTrue(lim.try_acquire(now=10))
        self.assertFalse(lim.try_acquire(now=20))
        self.assertEqual(lim.retry_after(now=20), 41)
        self.assertFalse(lim.try_acquire(now=59))
        self.assertTrue(lim.try_acquire(now=60))  # 첫 호출이 창에서 빠짐


if __name__ == "__main__":
    unittest.main()
