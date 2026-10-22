# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

사용자 요청에 따라 모든 답변과 보고는 한국어로 한다.

## 개요

OpenRouter API(OpenAI 호환 `/chat/completions`)로 텍스트·이미지 인식을 호출하는 소규모 Python 실습 프로젝트. 외부 라이브러리 없이 표준 라이브러리(`urllib`)만 사용하며, 의존성 설치 단계와 빌드·린트·테스트 프레임워크는 없다.

## 명령어

```bash
python src/config.py      # 키 로드 확인 (마스킹된 키만 출력)
python src/test_api.py    # 텍스트 + 이미지 모델 호출 테스트
```

폴더: 코드는 `src/`, 기획 문서(`PRD_step1~3.md`)는 `docs/`. `.env`와 `.env.example`은 저장소 루트에 둔다(`config.py`가 `src/`의 상위 폴더에서 찾음).

- Windows에서 한글 출력이 깨지면 `PYTHONIOENCODING=utf-8`을 앞에 붙인다 (`test_api.py`는 stdout도 utf-8로 재설정함).
- `python -I`(isolated)로 실행하지 말 것: 현재 폴더가 import 경로에서 빠져 `from config import ...`가 실패한다.

## 구조

- `config.py`: `.env`를 직접 파싱(python-dotenv 미사용)해 `OPENROUTER_API_KEY`를 반환하는 `get_api_key()`, 출력용 `mask()`, `OPENROUTER_BASE_URL`을 제공. 이미 설정된 환경 변수는 `.env`보다 우선한다.
- `test_api.py`: `config`를 import해 `chat(model, content)`로 호출. 이미지 테스트는 외부 파일 없이 빨강/파랑 반반 PNG를 코드로 생성(`make_test_png`)해 base64 data URL로 보낸다. 모델 ID는 파일 상단 `TEXT_MODEL`, `VISION_MODEL`로 관리.
- `.env`는 `.gitignore`로 제외되고, 커밋 대상 템플릿은 `.env.example`. 키 값을 출력·로그에 남길 때는 반드시 `mask()`를 거친다.

## 모델 관련 주의

- 현재 `TEXT_MODEL`은 `nvidia/nemotron-3.5-lightning:free`. 이전의 `nvidia/nemotron-3-super-120b-a12b:free`는 Nvidia 백엔드 404로 동작하지 않았다.
- 현재 `VISION_MODEL`은 `nvidia/nemotron-3.5-super-vl:free`. 이전의 `google/gemma-4-26b-a4b-it:free`는 Google 무료 공유 한도 때문에 HTTP 429가 반복됐고, `google/gemma-4-31b-it:free`도 같은 한도를 공유한다. `minimax/minimax-m3.1-flash:free`도 이미지 인식이 정상 동작했다.
- `:free` 모델은 제공자 사정으로 404/429가 잦다. 오류가 나면 인증(401)이 아닌지 먼저 구분하고, 모델 목록(`GET /api/v1/models`)에서 `input_modalities`에 `image`가 있는 모델을 골라 교체한다.
- 응답 일부가 간헐적으로 `choices` 없이 올 수 있다. `chat()`은 이 경우를 처리하지 않으므로 수정할 때 방어 코드를 고려한다.
