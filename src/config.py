"""환경 변수/.env 에서 OpenRouter API 키를 안전하게 불러온다."""
import os
from pathlib import Path

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def _load_env_file(path: Path) -> None:
    """간단한 .env 파서. 이미 설정된 환경 변수는 덮어쓰지 않는다."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


def _streamlit_secret(name: str) -> str:
    """Streamlit Community Cloud의 Secrets(st.secrets)에서 읽는다. 없거나 Streamlit이 없으면 빈 문자열."""
    try:
        import streamlit as st

        return str(st.secrets.get(name, ""))
    except Exception:  # secrets.toml 없음, streamlit 미설치 등
        return ""


def get_api_key() -> str:
    # 우선순위: 환경 변수 > st.secrets > 로컬 .env
    key = os.environ.get("OPENROUTER_API_KEY", "") or _streamlit_secret("OPENROUTER_API_KEY")
    if not key:
        _load_env_file(Path(__file__).resolve().parent.parent / ".env")
        key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key or key == "여기에_키_입력":
        raise RuntimeError(
            "OPENROUTER_API_KEY 가 설정되지 않았습니다. .env 파일을 확인하세요."
        )
    return key


def mask(key: str) -> str:
    """로그/출력용으로 키를 가린다."""
    return key[:6] + "..." + key[-4:] if len(key) > 12 else "***"


if __name__ == "__main__":
    print("키 로드 성공:", mask(get_api_key()))
