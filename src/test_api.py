"""실제 OpenRouter 무료 모델 호출 점검 (네트워크·키 필요).  실행: python src/test_api.py

앱이 쓰는 코드(ingredients.recognize, recipes.generate_recipes)를 그대로 호출해서
API와 AI 모델이 문제없이 동작했는지 보고한다. 1순위 모델이 막혀 폴백이 쓰인 것도 '문제'로 보고하며,
인식 또는 레시피 생성이 실패하면 종료 코드 1로 끝난다.
"""
import io
import sys
import time

from PIL import Image, ImageDraw, ImageFont

import ingredients
import recipes
from config import get_api_key, mask
from errors import AppError

sys.stdout.reconfigure(encoding="utf-8")

WORDS = ["계란 6개", "양파", "우유", "두부", "김치"]


def make_test_image() -> bytes:
    """냉장고 사진 대신, 식재료 이름을 적은 이미지를 코드로 만든다 (외부 파일 불필요)."""
    img = Image.new("RGB", (900, 500), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/malgunbd.ttf", 60)
    except OSError:  # 한글 폰트가 없는 환경
        font = ImageFont.load_default(size=60)
    for i, text in enumerate(WORDS):
        draw.text((60, 30 + i * 90), text, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def run_step(title: str, primary: str, call):
    """call(on_progress) 하나를 실행하고 (결과, 문제 목록)을 돌려준다."""
    print(f"\n== {title}")
    log: list[str] = []

    def progress(message: str) -> None:
        log.append(message)
        print("  " + message)

    started = time.time()
    try:
        result = call(progress)
    except AppError as e:
        print(f"  ✗ 실패: [{e.code}] {e.message} ({time.time() - started:.1f}초)")
        return None, [f"{title} 실패: {e.message}"]
    problems = [m.strip() for m in log if "실패" in m or "형식이 올바르지" in m]
    if result.model != primary:
        problems.append(f"{title}: 1순위 {primary} 대신 폴백 {result.model}이(가) 응답")
    print(f"  ✓ 성공: {result.model} ({time.time() - started:.1f}초)")
    return result, problems


def main() -> int:
    print(f"API 키: {mask(get_api_key())}")
    problems: list[str] = []

    vision, p = run_step(
        "1단계 이미지 인식", ingredients.VISION_MODEL,
        lambda cb: ingredients.recognize(ingredients.shrink_image(make_test_image()), cb),
    )
    problems += p
    if vision:
        names = [i["name"] for i in vision.items]
        print("  인식된 재료:", ", ".join(names) or "(없음)")
        hits = sum(any(w.split()[0] in n for n in names) for w in WORDS)
        if hits < 3:
            problems.append(f"인식 정확도 낮음: 정답 {len(WORDS)}개 중 {hits}개만 인식")

    sample = vision.items if vision and vision.items else [{"name": w.split()[0], "quantity": None} for w in WORDS]
    recipe, p = run_step(
        "2단계 레시피 생성", recipes.TEXT_MODEL,
        lambda cb: recipes.generate_recipes(sample, servings=2, on_progress=cb),
    )
    problems += p
    if recipe:
        for r in recipe.recipes:
            print(f"  - {r['title']} ({r['time_minutes']}분, 부족: {', '.join(r['missing_ingredients']) or '없음'})")

    print("\n== 점검 결과")
    failed = vision is None or recipe is None
    if not problems:
        print("문제 없음: API와 AI 모델이 모두 정상 동작했습니다.")
    else:
        print("문제 있음:" if failed else "동작은 했지만 문제가 있었습니다:")
        for item in dict.fromkeys(problems):  # 중복 제거, 순서 유지
            print(f"  - {item}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
