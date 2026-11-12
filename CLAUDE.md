# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

사용자 요청에 따라 모든 답변과 보고는 한국어로 한다.

## 개요

냉장고 사진에서 식재료를 인식하고 레시피를 추천하는 웹 앱(OpenRouter 모델 사용). **Streamlit으로 개발하고 Streamlit Community Cloud에 무료 배포**하는 것이 목표다. 단계별 기획은 `docs/PRD_step1~3.md`: 1단계(재료 인식)와 2단계(레시피 생성)는 구현됨, 3단계(프로필·저장)는 미구현. 작업 순서는 노션 노트 6장(6.4절)을 따르며, 단계마다 `screenshot/PRD_0N_0M/`에 화면 캡처를 저장하고 `PRD_0N_0M` 태그로 커밋·푸시한다(현재까지 `PRD_01_01`, 다음은 `PRD_02_01`).
- **모델은 반드시 OpenRouter 무료(`:free`) 모델만 쓴다.** `openrouter.chat()`이 `:free`로 끝나지 않는 모델 ID는 호출 전에 거부한다. 모델을 추가·교체할 때도 가격이 입력·출력 모두 $0인지 확인한다.

- 의존성은 `requirements.txt`(streamlit만; Pillow·pandas는 함께 설치됨). 로컬은 프로젝트 `.venv`(git 제외)를 쓴다. 새 의존성을 넣기 전에 사용자에게 확인한다.
- 배포 환경이 무료 제약을 갖는다: 12시간 무트래픽이면 절전, 파일 시스템은 영구 저장에 쓸 수 없다고 가정. 그래서 3단계(저장)는 SQLite 파일이 아니라 외부 무료 DB(Neon Postgres 등)와 `st.login`(OIDC) 기준으로 설계해야 한다. PRD_step3.md는 아직 SQLite·자체 비밀번호 방식으로 적혀 있어 갱신이 필요하다.

## 명령어

```bash
.venv/Scripts/python -m streamlit run src/streamlit_app.py      # 앱 실행 → http://localhost:8501
.venv/Scripts/python -m unittest discover -s tests              # 전체 단위 테스트 (모델 호출은 mock, 네트워크 불필요)
.venv/Scripts/python -m unittest tests.test_ingredients.ParseTest   # 테스트 클래스 하나만
.venv/Scripts/python src/config.py                              # 키 로드 확인 (마스킹된 키만 출력)
.venv/Scripts/python src/test_api.py                            # 실제 모델 호출 점검: 앱 로직으로 인식·레시피 생성을 한 번씩 실행하고 문제를 보고 (네트워크·키 필요, 실패 시 종료 코드 1)
```

- Windows에서 한글 출력이 깨지면 `PYTHONIOENCODING=utf-8`을 앞에 붙인다.
- `python -I`(isolated)로 실행하지 말 것: 현재 폴더가 import 경로에서 빠져 `from config import ...`가 실패한다.
- 폴더: 코드 `src/`, 테스트 `tests/`, 기획 문서 `docs/`, Streamlit 설정 `.streamlit/`. `.env`·`.env.example`은 저장소 루트.

## 구조

- 화면은 `src/streamlit_app.py` 하나이고 `st.session_state["step"]`(1 또는 2)로 단계를 전환한다.
  - 1단계: 업로드(`st.file_uploader`/`st.camera_input`) → `identify()`(검증 → Pillow로 긴 변 1280px JPEG 축소 → 모델 호출) → `st.data_editor`로 재료 편집 → "레시피 추천받기"가 `final_ingredients`를 저장하고 `step=2`로 넘긴다.
  - 2단계: 재료 칩 + 옵션(인원·조리 시간·식사 종류·제외 재료) → `generate_recipes()` → 레시피 카드(`st.expander` 상세). "← 재료 다시 고치기"는 `detected`와 `restored=True`를 세워 사진 없이도 1단계 목록을 유지한다(`restored`가 없으면 사진이 없을 때 결과를 지운다).
- 화면과 무관한 로직은 별도 모듈이라 Streamlit 없이 테스트된다.
  - `ingredients.py`: 1단계. 모델 ID `VISION_MODEL`·`FALLBACK_MODELS`, 프롬프트, 파일 시그니처 검증, 축소, 응답 파싱·정규화(`normalize_items`는 모델 출력과 사용자 편집 모두에 쓰임).
  - `recipes.py`: 2단계. `TEXT_MODEL`·`FALLBACK_MODELS`, 프롬프트(사용자 입력의 `<`·`>`를 제거해 `<재료>`·`<조건>` 데이터 블록을 닫지 못하게 함), `parse_recipes`(검증·정규화), `reconcile`(아래), `generate_recipes`.
  - `fallback.py`: 재시도·폴백 공통 로직 `run_with_fallback`. `errors.py`: 화면에 보일 `AppError`. `openrouter.py`: `chat()`이 `content`만 반환하고, 404/429/5xx/`choices` 없음은 `ModelError(retryable=True)`, HTTP 상태는 `ModelError.status`. `ratelimit.py`: 메모리 슬라이딩 윈도우 제한.
- 재시도·폴백(`fallback.run_with_fallback`, 1·2단계 공용): 1순위 모델을 최대 2번(일시 오류면 `RETRY_WAIT`초 대기 후), 그래도 실패하면 나머지 모델을 순서대로 1번씩 시도한다. 응답 형식 오류도 실패로 센다. 403 등 비재시도 오류는 그 모델만 건너뛰고, 401(키 문제)은 즉시 중단하며, 예상 밖 예외는 숨기지 않고 그대로 올린다. `on_progress` 콜백으로 진행 상황을 화면(`st.status`)에 보여 주고, 실제로 응답한 모델은 `Recognition.model`/`RecipeResult.model`로 돌려준다.
- `recipes.reconcile`: 모델이 적은 `have`/부족 재료를 믿지 않고 사용자 재료와 직접 비교해 다시 계산한다(양쪽 이름 포함 검사, 기본 양념 `PANTRY`, 물은 `WATER`에 정확히 일치할 때만 — '물' 한 글자는 '나물'·'해물'에도 있어 포함 검사를 쓰면 안 됨). 제외 재료가 든 레시피는 버리며, 전부 버려지면 `ValueError`라서 재시도·폴백 대상이 된다.
- 모델이 만든 글은 `esc()`로 마크다운 특수문자를 이스케이프해서 `st.markdown`/`st.subheader`에 넣는다(링크·이미지·수식으로 해석되지 않게). 사용자·모델 텍스트를 이스케이프 없이 마크다운으로 렌더링하지 말 것.
- 호출 제한: 세션당 10회/시간(`st.session_state`), 서버 전체 60회/시간(`@st.cache_resource` 공유 객체). 무료 모델 한도를 방문자 한 명이 다 쓰지 못하게 하려는 것이므로 2단계 호출에도 같은 제한을 건다.
- API 키는 `config.get_api_key()`가 환경 변수 → `st.secrets` → 루트 `.env` 순으로 찾는다. 배포 시에는 Community Cloud의 Secrets에 `.streamlit/secrets.toml.example` 형식으로 넣는다. 키를 출력·로그에 남길 때는 `mask()`를 거친다.
- `.streamlit/config.toml`의 `maxUploadSize = 10`이 업로드 10MB 제한이다(기본값은 200MB).
- `test_api.py`: 앱과 같은 코드(`ingredients.recognize`, `recipes.generate_recipes`)를 화면 없이 실제 모델로 한 번씩 호출한다. 1순위 모델이 막혀 폴백이 쓰인 것도 '문제'로 보고한다.

## 주의할 점

- `st.session_state`에 `items`, `keys`, `values`, `get` 같은 딕셔너리 메서드 이름을 키로 쓰면 `st.session_state.items`가 저장값이 아니라 메서드로 읽힌다. 키 이름을 피하고 `st.session_state["키"]`로 접근한다(1단계 구현 중 실제로 겪음).
- 화면 코드는 AppTest(`tests/test_streamlit_app.py`)로 확인한다. 2단계는 `recipes.generate_recipes`를 mock으로 바꿔 버튼 흐름·오류·호출 제한·이스케이프까지 검증하지만, 파일 업로드는 AppTest가 지원하지 않아 앱을 띄워 직접 확인해야 한다(브라우저에서 JS로 `input[type=file]`에 `DataTransfer`로 파일을 넣고 `change` 이벤트를 보내면 가능).
- 실패 메시지(`st.error`)와 시도 내역은 `st.status` 블록 **바깥**에서 그린다(`show_failure`). 안쪽에서 그리면 접힌 상자 속에 들어가 "실패했어요"라는 제목만 보이고 이유가 안 보인다. AppTest는 접힌 안쪽 요소도 찾아서 이 문제를 놓치므로, `test_error_is_outside_the_collapsed_status_box`가 구조를 직접 검사한다. 모든 모델이 실패하면 `AppError.details`에 모델별 실패 이유가 담기고 화면에 "시도 내역"으로 보인다.
- 성공 시 `st.rerun()`을 부르면 같은 실행에서 그린 `st.error`가 사라진다. 그래서 2단계의 `run_generation`은 성공했을 때만 `st.rerun()`하고, 실패하면 그대로 두어 오류 메시지가 보이게 한다. 실패한 요청의 이전 결과(`detected`, `recipes`)는 시작 시 지워서 이번 결과처럼 보이지 않게 한다.
- 추론(reasoning) 모델은 생각하는 데 토큰을 먼저 써서, `max_tokens`를 작게 잡으면 `content`가 빈 채로 `finish_reason=length`로 끊긴다. 레시피 생성은 `MAX_TOKENS=6000`이며 그래도 모델에 따라 수십 초가 걸린다.

## 모델 관련

- 이미지(1단계) `VISION_MODEL`은 `nvidia/nemotron-3.5-super-vl:free`, 폴백은 `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free` → `dots-studio/dots-3-note-preview:free` → `google/gemma-4-26b-a4b-it:free` → `google/gemma-4-31b-it:free`.
- 텍스트(2단계) `TEXT_MODEL`은 `baidu/ernie-5.5-flash-preview:free`(약 7~12초), 폴백은 `nvidia/nemotron-3-ultra-550b-a55b:free`(약 11초) → `nvidia/nemotron-3.5-lightning:free`(약 50초) → `cohere/north-mini-code:free`(약 19초). 처음 지정했던 lightning은 추론 모델이라 느려서 뒤로 뒀다(2026-11-12 직접 호출해 비교).
- 이전에 쓰던 모델 중 `nvidia/nemotron-3-super-120b-a12b:free`는 Nvidia 백엔드 404, `google/gemma-4-26b-a4b-it:free`·`google/gemma-4-31b-it:free`는 Google 무료 공유 한도(429)가 잦아 뒤로 뒀고, `minimax/minimax-m3.1-flash:free`는 무료 목록에서 사라졌으며, `thinkingmachines/inkling-2-small:free`는 403이었다. 무료 목록은 수시로 바뀌므로 모델을 고를 때마다 `GET /api/v1/models`로 다시 확인하고 직접 호출해 본다.
- `:free` 모델은 제공자 사정으로 404/429/502가 잦다. 오류가 나면 인증(401)이 아닌지 먼저 구분하고, 모델 목록(`GET /api/v1/models`)에서 `input_modalities`에 `image`가 있는 모델을 골라 교체한다.

## 실행 시 확인 사항

- 이 앱은 mock이 아니라 OpenRouter의 실제 AI 모델이 냉장고 사진을 인식하고 레시피를 생성한다.
- 앱을 실행하거나 인식·레시피 기능을 고칠 때마다 실제로 실행해서 API가 정확하게 작동했는지, AI 모델이 문제없이 실행되었는지 확인한다(`src/test_api.py` 또는 앱 화면). 단위 테스트 통과만으로 "정상"이라고 보고하지 않는다.
- 문제가 있었다면 어떤 문제였는지 숨기지 말고 보고한다: 1순위 모델 실패와 폴백 사용, 429·404 같은 오류, 응답 형식 오류, 인식 정확도 저하 등. 확인하지 못한 부분은 확인하지 못했다고 적는다.
- 앱 화면도 같은 원칙이다. 인식이나 레시피 생성이 실패하면 이전에 성공한 결과를 남겨 두지 않고, 실패했다고 정직하게 화면에 표시한다.
