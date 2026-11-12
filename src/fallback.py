"""모델 호출의 재시도·폴백 공통 로직 (1단계 인식, 2단계 레시피 생성이 함께 쓴다)."""
import time
from typing import Callable, TypeVar

from errors import AppError
from openrouter import ModelError

T = TypeVar("T")

RETRY_WAIT = 2.0  # 같은 모델로 다시 시도하기 전 대기(초). 일시적 혼잡(429)이 풀리길 기다린다.


def run_with_fallback(
    models: list[str],
    call: Callable[[str], T],
    on_progress: Callable[[str], None] | None = None,
    parse_failed_message: str = "모델 응답을 해석하지 못했습니다. 다시 시도해 주세요.",
) -> tuple[T, str]:
    """models[0]을 최대 2번(일시 오류면 RETRY_WAIT초 대기 후), 실패하면 나머지 모델을 순서대로 1번씩 시도한다.

    call(model)이 ModelError나 ValueError(응답 형식 오류)를 내면 실패로 센다. 성공하면 (결과, 응답한 모델)을 돌려준다.
    403 등 재시도해도 소용없는 오류는 그 모델만 건너뛰고, 401(키 문제)은 즉시 중단한다. 모두 실패하면 AppError.
    """
    say = on_progress or (lambda _msg: None)
    failures: list[str] = []  # 모두 실패했을 때 화면에 보일 시도 내역
    had_model_error = False
    for index, model in enumerate(models):
        attempts = 2 if index == 0 else 1
        for attempt in range(1, attempts + 1):
            say(f"{attempt}차 시도: {model}" if index == 0 else f"폴백 모델로 전환: {model}")
            try:
                return call(model), model
            except ModelError as e:
                had_model_error = True
                if e.status == 401:  # 키 문제는 모델을 바꿔도 소용없다
                    raise AppError("server_config", "서비스 설정 문제로 처리할 수 없어요. 관리자에게 알려 주세요.", 500)
                say(f"  → 실패 ({e})")
                failures.append(f"{model}: {e}")
                if not e.retryable:  # 402/403 등: 이 모델은 건너뛴다
                    break
            except ValueError:  # json.JSONDecodeError 포함
                say("  → 응답 형식이 올바르지 않음")
                failures.append(f"{model}: 응답 형식 오류")
            if attempt < attempts:
                say(f"  {RETRY_WAIT:g}초 뒤 같은 모델로 다시 시도")
                time.sleep(RETRY_WAIT)

    # 같은 모델을 두 번 시도해 같은 이유로 실패했으면 한 줄로 합친다
    details = list(dict.fromkeys(failures))
    if had_model_error:  # 응답 자체를 못 받은 모델이 하나라도 있으면 '형식 오류'로만 보이지 않게 한다
        raise AppError(
            "model_unavailable",
            f"무료 모델 {len(models)}개를 모두 시도했지만 응답을 받지 못했어요. 잠시 후 다시 시도해 주세요.",
            503,
            details,
        )
    raise AppError("parse_failed", parse_failed_message, 502, details)
