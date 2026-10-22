# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

사용자 요청에 따라 모든 답변과 보고는 한국어로 한다.

## 개요

냉장고 사진에서 식재료를 인식하고 레시피를 추천하는 웹 앱(OpenRouter 모델 사용). **Streamlit으로 개발하고 Streamlit Community Cloud에 무료 배포**하는 것이 목표다. 단계별 기획은 `docs/PRD_step1~3.md`: 1단계(재료 인식)는 구현됨, 2단계(레시피 생성)·3단계(프로필·저장)는 미구현.

- 의존성은 `requirements.txt`(streamlit만; Pillow·pandas는 함께 설치됨). 로컬은 프로젝트 `.venv`(git 제외)를 쓴다. 새 의존성을 넣기 전에 사용자에게 확인한다.
- 배포 환경이 무료 제약을 갖는다: 12시간 무트래픽이면 절전, 파일 시스템은 영구 저장에 쓸 수 없다고 가정. 그래서 3단계(저장)는 SQLite 파일이 아니라 외부 무료 DB(Neon Postgres 등)와 `st.login`(OIDC) 기준으로 설계해야 한다. PRD_step3.md는 아직 SQLite·자체 비밀번호 방식으로 적혀 있어 갱신이 필요하다.

## 명령어

```bash
.venv/Scripts/python -m streamlit run src/streamlit_app.py      # 앱 실행 → http://localhost:8501
.venv/Scripts/python -m unittest discover -s tests              # 전체 단위 테스트 (모델 호출은 mock, 네트워크 불필요)
.venv/Scripts/python -m unittest tests.test_ingredients.ParseTest   # 테스트 클래스 하나만
.venv/Scripts/python src/config.py                              # 키 로드 확인 (마스킹된 키만 출력)
.venv/Scripts/python src/test_api.py                            # 실제 모델 호출 스모크 테스트 (네트워크·키 필요)
```

- Windows에서 한글 출력이 깨지면 `PYTHONIOENCODING=utf-8`을 앞에 붙인다.
- `python -I`(isolated)로 실행하지 말 것: 현재 폴더가 import 경로에서 빠져 `from config import ...`가 실패한다.
- 폴더: 코드 `src/`, 테스트 `tests/`, 기획 문서 `docs/`, Streamlit 설정 `.streamlit/`. `.env`·`.env.example`은 저장소 루트.

## 구조

- 화면은 `src/streamlit_app.py` 하나. 업로드(`st.file_uploader`/`st.camera_input`) → `identify()`(검증 → Pillow로 긴 변 1280px JPEG 축소 → 모델 호출) → `st.data_editor`로 재료 편집 → "레시피 추천받기"가 `st.session_state["final_ingredients"]`에 최종 목록을 남긴다(2단계의 입력).
- 화면과 무관한 로직은 별도 모듈이라 Streamlit 없이 테스트된다: `ingredients.py`(모델 ID `VISION_MODEL`·`FALLBACK_MODELS`, 프롬프트, 파일 시그니처 검증, 축소, 응답 파싱·정규화 `normalize_items`, `AppError`), `openrouter.py`(`chat()`이 `content`만 반환, 404/429/5xx/`choices` 없음은 `ModelError(retryable=True)`, HTTP 상태는 `ModelError.status`), `ratelimit.py`(메모리 슬라이딩 윈도우 제한).
- 재시도·폴백(`ingredients.recognize`): 1순위 모델을 최대 2번(일시 오류면 `RETRY_WAIT`초 대기 후), 그래도 실패하면 `FALLBACK_MODELS`를 순서대로 1번씩 시도한다. 응답 형식 오류도 실패로 센다. 403 등 비재시도 오류는 그 모델만 건너뛰고, 401(키 문제)은 즉시 중단한다. `on_progress` 콜백으로 진행 상황을 화면(`st.status`)에 보여 주고, 실제로 응답한 모델은 `Recognition.model`로 돌려준다.
- 호출 제한: 세션당 10회/시간(`st.session_state`), 서버 전체 60회/시간(`@st.cache_resource` 공유 객체). 무료 모델 한도를 방문자 한 명이 다 쓰지 못하게 하려는 것이므로 2단계 호출에도 같은 제한을 건다.
- API 키는 `config.get_api_key()`가 환경 변수 → `st.secrets` → 루트 `.env` 순으로 찾는다. 배포 시에는 Community Cloud의 Secrets에 `.streamlit/secrets.toml.example` 형식으로 넣는다. 키를 출력·로그에 남길 때는 `mask()`를 거친다.
- `.streamlit/config.toml`의 `maxUploadSize = 10`이 업로드 10MB 제한이다(기본값은 200MB).
- `test_api.py`: 화면 없이 텍스트·이미지 모델을 한 번씩 호출하는 스모크 테스트. 재시도가 없어서 제공자의 일시 오류(예: 502)로 실패할 수 있다(앱 본체는 1회 재시도함).

## 주의할 점

- `st.session_state`에 `items`, `keys`, `values`, `get` 같은 딕셔너리 메서드 이름을 키로 쓰면 `st.session_state.items`가 저장값이 아니라 메서드로 읽힌다. 키 이름을 피하고 `st.session_state["키"]`로 접근한다(1단계 구현 중 실제로 겪음).
- 화면 코드는 AppTest(`tests/test_streamlit_app.py`)로 스모크만 확인된다. 업로드·모델 호출 흐름은 AppTest가 지원하지 않아 앱을 띄워 직접 확인해야 한다.

## 모델 관련

- `TEXT_MODEL`은 `nvidia/nemotron-3.5-lightning:free`, `VISION_MODEL`은 `nvidia/nemotron-3.5-super-vl:free`(폴백: `minimax/minimax-m3.1-flash:free` → `google/gemma-4-26b-a4b-it:free` → `google/gemma-4-31b-it:free`). 이전의 `nvidia/nemotron-3-super-120b-a12b:free`는 Nvidia 백엔드 404, `google/gemma-4-26b-a4b-it:free`·`google/gemma-4-31b-it:free`는 Google 무료 공유 한도(429)로 막혔다. `minimax/minimax-m3.1-flash:free`도 이미지 인식이 정상 동작했다.
- `:free` 모델은 제공자 사정으로 404/429/502가 잦다. 오류가 나면 인증(401)이 아닌지 먼저 구분하고, 모델 목록(`GET /api/v1/models`)에서 `input_modalities`에 `image`가 있는 모델을 골라 교체한다.
