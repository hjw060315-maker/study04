"""1단계: 냉장고 사진에서 식재료를 인식한다."""
import base64
import io
import json
import re
import time
from typing import Callable, NamedTuple

from PIL import Image, ImageOps

from openrouter import ModelError, chat

VISION_MODEL = "nvidia/nemotron-3.5-super-vl:free"
# 1순위가 막히면 순서대로 넘어간다. 모두 OpenRouter 무료 비전 모델이며, minimax는 직접 시험해 성공을 확인했다.
FALLBACK_MODELS = [
    "minimax/minimax-m3.1-flash:free",
    "google/gemma-4-26b-a4b-it:free",
    "google/gemma-4-31b-it:free",
]
RETRY_WAIT = 2.0  # 같은 모델로 다시 시도하기 전 대기(초). 일시적 혼잡(429)이 풀리길 기다린다.
MODEL_TIMEOUT = 60  # 모델 한 번의 호출 제한 시간(초)
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_SIDE = 1280
MAX_ITEMS = 50
MAX_NAME_LEN = 30

PROMPT = (
    "냉장고 사진에서 식재료만 찾아 한국어로 JSON 객체로만 답하세요. "
    '형식: {"ingredients": [{"name": "재료명", "quantity": "대략적 수량 또는 null"}]} '
    "용기, 선반, 서랍 같은 재료가 아닌 것은 넣지 마세요. 확실하지 않으면 넣지 마세요. "
    "재료가 하나도 보이지 않으면 빈 배열을 반환하세요. JSON 외의 설명은 쓰지 마세요."
)


class AppError(Exception):
    """사용자에게 보여 줄 오류. code는 PRD 8절의 오류 코드."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def detect_mime(data: bytes) -> str | None:
    """확장자/헤더를 믿지 않고 파일 시그니처로 형식을 판별한다."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def validate_image(data: bytes) -> str:
    if not data:
        raise AppError("invalid_image", "이미지가 비어 있습니다.")
    if len(data) > MAX_IMAGE_BYTES:
        raise AppError("too_large", "이미지는 10MB 이하여야 합니다.", 413)
    mime = detect_mime(data)
    if mime is None:
        raise AppError("invalid_image", "JPG, PNG, WEBP 이미지만 올릴 수 있습니다.")
    return mime


def shrink_image(data: bytes, max_side: int = MAX_SIDE) -> bytes:
    """긴 변을 max_side px로 줄인 JPEG로 변환한다 (전송량·응답 시간 절약, 스마트폰 회전 정보 반영)."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            img = ImageOps.exif_transpose(img)
            img.thumbnail((max_side, max_side))
            if img.mode in ("RGBA", "LA", "P"):  # 투명 배경은 흰색으로
                img = img.convert("RGBA")
                bg = Image.new("RGB", img.size, "white")
                bg.paste(img, mask=img.getchannel("A"))
                img = bg
            else:
                img = img.convert("RGB")
            out = io.BytesIO()
            img.save(out, "JPEG", quality=85)
            return out.getvalue()
    except Exception:
        raise AppError("invalid_image", "이미지를 읽을 수 없습니다. 다른 사진을 골라 주세요.")


def parse_ingredients(text: str) -> list[dict]:
    """모델 응답에서 JSON을 꺼내 검증·정규화한다. 형식이 틀리면 ValueError."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("JSON을 찾지 못했습니다.")
    obj = json.loads(m.group(0))
    items = obj.get("ingredients")
    if not isinstance(items, list):
        raise ValueError("ingredients 배열이 없습니다.")
    return normalize_items(items)


def normalize_items(items: list) -> list[dict]:
    """재료 목록을 {name, quantity} 형태로 정리한다 (공백·중복·길이·개수 제한). 모델 출력과 사용자 편집 모두에 쓴다."""
    result, seen = [], set()
    for item in items:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        name = re.sub(r"\s+", " ", item["name"]).strip()[:MAX_NAME_LEN]
        key = name.replace(" ", "").lower()
        if not name or key in seen:
            continue
        seen.add(key)
        qty = item.get("quantity")
        if isinstance(qty, float) and qty != qty:  # pandas의 빈 칸(NaN)
            qty = None
        qty = str(qty).strip()[:30] if qty not in (None, "", "null") else None
        qty = qty or None
        result.append({"name": name, "quantity": qty})
        if len(result) >= MAX_ITEMS:
            break
    return result


class Recognition(NamedTuple):
    items: list[dict]
    model: str  # 실제로 응답한 모델 (폴백이면 1순위와 다름)


def recognize(image: bytes, on_progress: Callable[[str], None] | None = None) -> Recognition:
    """1순위 모델로 2번 시도하고(429 등 일시 오류면 RETRY_WAIT초 대기), 그래도 안 되면 폴백 모델을 순서대로 1번씩 시도한다."""
    mime = validate_image(image)
    b64 = base64.b64encode(image).decode()
    content = [
        {"type": "text", "text": PROMPT},
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
    ]
    say = on_progress or (lambda _msg: None)

    last_error: Exception | None = None
    for index, model in enumerate([VISION_MODEL, *FALLBACK_MODELS]):
        attempts = 2 if index == 0 else 1
        for attempt in range(1, attempts + 1):
            say(f"{attempt}차 시도: {model}" if index == 0 else f"폴백 모델로 전환: {model}")
            try:
                return Recognition(parse_ingredients(chat(model, content, timeout=MODEL_TIMEOUT)), model)
            except ModelError as e:
                last_error = e
                if e.status == 401:  # 키 문제는 모델을 바꿔도 소용없다
                    raise AppError("server_config", "서비스 설정 문제로 인식할 수 없어요. 관리자에게 알려 주세요.", 500)
                say(f"  → 실패 ({e})")
                if not e.retryable:  # 402/403 등: 이 모델은 건너뛴다
                    break
            except ValueError as e:  # json.JSONDecodeError 포함
                last_error = e
                say("  → 응답 형식이 올바르지 않음")
            if attempt < attempts:
                say(f"  {RETRY_WAIT:g}초 뒤 같은 모델로 다시 시도")
                time.sleep(RETRY_WAIT)

    if isinstance(last_error, ModelError):
        raise AppError(
            "model_unavailable",
            "무료 모델이 지금 혼잡하거나 응답하지 않아요. 잠시 후 다시 시도해 주세요.",
            503,
        )
    raise AppError("parse_failed", "인식 결과를 해석하지 못했습니다. 다시 시도해 주세요.", 502)
