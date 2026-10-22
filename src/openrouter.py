"""OpenRouter /chat/completions 호출 (표준 라이브러리만 사용)."""
import json
import urllib.error
import urllib.request

from config import OPENROUTER_BASE_URL, get_api_key


class ModelError(Exception):
    """모델 호출 실패. retryable=True 이면 같은 모델로 한 번 더 시도할 만한 오류, status는 HTTP 상태(없으면 None)."""

    def __init__(self, message: str, retryable: bool = False, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


def chat(model: str, content, max_tokens: int = 1500, timeout: int = 120) -> str:
    """단일 user 메시지를 보내고 응답의 content 문자열만 반환한다 (reasoning은 버림)."""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        f"{OPENROUTER_BASE_URL}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {get_api_key()}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        # 401/402는 재시도해도 소용없고, 404/429/5xx는 무료 모델에서 일시적으로 잦다.
        raise ModelError(
            f"HTTP {e.code}", retryable=e.code in (404, 408, 429) or e.code >= 500, status=e.code
        )
    except (urllib.error.URLError, TimeoutError) as e:
        raise ModelError(f"네트워크 오류: {e}", retryable=True)

    choices = data.get("choices")
    if not choices:
        # 간헐적으로 choices 없이 오는 응답 (error 필드만 있는 경우 포함)
        raise ModelError("응답에 choices가 없습니다.", retryable=True)
    text = (choices[0].get("message", {}).get("content") or "").strip()
    if not text:
        raise ModelError("빈 응답입니다.", retryable=True)
    return text
