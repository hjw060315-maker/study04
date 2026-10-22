"""OpenRouter API 동작 테스트: 텍스트 모델 / 이미지(비전) 모델."""
import base64
import json
import struct
import sys
import urllib.error
import urllib.request
import zlib

from config import OPENROUTER_BASE_URL, get_api_key

TEXT_MODEL = "nvidia/nemotron-3.5-lightning:free"
VISION_MODEL = "nvidia/nemotron-3.5-super-vl:free"

sys.stdout.reconfigure(encoding="utf-8")


def make_test_png(width=200, height=100) -> bytes:
    """왼쪽 빨강 / 오른쪽 파랑 단색 PNG를 외부 라이브러리 없이 생성한다."""
    row = b"\x00" + b"\xff\x00\x00" * (width // 2) + b"\x00\x00\xff" * (width // 2)
    raw = row * height

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def chat(model: str, content) -> str:
    payload = {"model": model, "messages": [{"role": "user", "content": content}], "max_tokens": 300}
    req = urllib.request.Request(
        f"{OPENROUTER_BASE_URL}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {get_api_key()}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        return f"[HTTP {e.code}] {e.read().decode('utf-8', 'replace')[:500]}"
    except Exception as e:
        return f"[오류] {type(e).__name__}: {e}"
    msg = data["choices"][0]["message"]
    return (msg.get("content") or "").strip() or f"[빈 응답] {json.dumps(data, ensure_ascii=False)[:500]}"


if __name__ == "__main__":
    print(f"== 텍스트 테스트: {TEXT_MODEL}")
    print(chat(TEXT_MODEL, "대한민국의 수도는 어디인가요? 한 문장으로 답해 주세요."))

    print(f"\n== 이미지 테스트: {VISION_MODEL}")
    b64 = base64.b64encode(make_test_png()).decode()
    print(chat(VISION_MODEL, [
        {"type": "text", "text": "이 이미지의 왼쪽과 오른쪽 색깔은 각각 무엇인가요? 한국어로 짧게 답해 주세요."},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
    ]))
