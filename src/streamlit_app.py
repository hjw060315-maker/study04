"""1단계 화면: 냉장고 사진 → 식재료 인식 → 목록 수정.  실행: streamlit run src/streamlit_app.py"""
import hashlib

import pandas as pd
import streamlit as st

from ingredients import AppError, Recognition, normalize_items, recognize, shrink_image, validate_image
from ratelimit import SlidingWindowLimiter

# 무료 모델 한도를 방문자 한 명이 다 쓰지 못하게: (횟수, 초)
SESSION_LIMIT = (10, 3600)
GLOBAL_LIMIT = (60, 3600)

st.set_page_config(page_title="냉장고 재료 인식", page_icon="🥬")


@st.cache_resource
def global_limiter() -> SlidingWindowLimiter:
    """서버 프로세스 하나를 모든 방문자가 공유한다."""
    return SlidingWindowLimiter(*GLOBAL_LIMIT)


def session_limiter() -> SlidingWindowLimiter:
    if "limiter" not in st.session_state:
        st.session_state.limiter = SlidingWindowLimiter(*SESSION_LIMIT)
    return st.session_state.limiter


def identify(data: bytes, on_progress=None) -> Recognition:
    """검증 → 축소 → 모델 호출(재시도·폴백 포함). 실패하면 AppError."""
    say = on_progress or (lambda _msg: None)
    validate_image(data)
    say("사진 크기를 줄이는 중")
    small = shrink_image(data)
    say(f"  → {len(data) / 1024:.0f}KB → {len(small) / 1024:.0f}KB")
    return recognize(small, on_progress)


def run_recognition(data: bytes) -> None:
    session, shared = session_limiter(), global_limiter()
    if not session.try_acquire():
        st.error(f"요청이 너무 많아요. 약 {session.retry_after() // 60 + 1}분 뒤에 다시 시도해 주세요.")
        return
    if not shared.try_acquire():
        st.error("지금 이용자가 많아 잠시 쉬고 있어요. 조금 뒤에 다시 시도해 주세요.")
        return
    st.session_state.pop("detected", None)  # 실패했는데 이전 결과가 이번 사진의 결과처럼 보이지 않게
    with st.status("재료를 찾고 있어요… (최대 1~2분 걸릴 수 있어요)", expanded=True) as status:
        try:
            result = identify(data, st.write)
        except AppError as e:
            status.update(label="인식에 실패했어요", state="error")
            st.error(e.message)
            return
        except Exception:  # 내부 오류 내용은 화면에 보이지 않는다
            status.update(label="인식에 실패했어요", state="error")
            st.error("예상치 못한 오류가 발생했어요. 다시 시도해 주세요.")
            return
        status.update(label="인식 완료", state="complete", expanded=False)
    st.session_state["detected"] = result.items
    st.session_state["detected_model"] = result.model
    st.session_state.editor_version = st.session_state.get("editor_version", 0) + 1


st.title("🥬 냉장고 재료 인식")
st.caption("냉장고 사진을 올리면 안에 있는 식재료를 찾아 드려요.")

use_camera = st.toggle("카메라로 직접 촬영")
if use_camera:
    source = st.camera_input("냉장고를 촬영하세요", label_visibility="collapsed")
else:
    source = st.file_uploader(
        "냉장고 사진 (JPG · PNG · WEBP, 10MB 이하)", type=["jpg", "jpeg", "png", "webp"]
    )
st.caption("올린 사진은 재료 인식에만 쓰이며 저장되지 않아요.")

if source is not None:
    data = source.getvalue()
    image_id = hashlib.md5(data).hexdigest()
    if st.session_state.get("image_id") != image_id:  # 다른 사진이면 이전 결과를 지운다
        st.session_state.image_id = image_id
        st.session_state.pop("detected", None)
    st.image(data, width="stretch")
    if st.button("재료 인식하기", type="primary"):
        run_recognition(data)
else:
    st.session_state.pop("image_id", None)
    st.session_state.pop("detected", None)

if "detected" in st.session_state:
    st.subheader("인식된 재료")
    if not st.session_state["detected"]:
        st.info("재료를 찾지 못했어요. 더 밝고 선명한 사진으로 다시 시도하거나, 아래 표에 직접 추가해 주세요.")
    st.caption(
        f"인식 모델: {st.session_state.get('detected_model', '-')} · AI 인식 결과는 부정확할 수 있어요. "
        "틀린 재료는 고치거나 행을 지우고, 표 맨 아래 빈 줄에서 재료를 추가할 수 있어요."
    )
    edited = st.data_editor(
        pd.DataFrame(
            [{"name": i["name"], "quantity": i["quantity"] or ""} for i in st.session_state["detected"]],
            columns=["name", "quantity"],
        ),
        key=f"editor_{st.session_state.get('editor_version', 0)}",  # 새로 인식하면 편집 상태 초기화
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        column_config={
            "name": st.column_config.TextColumn("재료", max_chars=30),
            "quantity": st.column_config.TextColumn("수량(선택)", max_chars=30),
        },
    )
    final = normalize_items(edited.to_dict("records"))
    st.caption(f"재료 {len(final)}개")
    if st.button("레시피 추천받기", type="primary", disabled=not final):
        st.session_state.final_ingredients = final  # 2단계의 입력
        st.success("재료 목록을 저장했어요.")
        st.info("레시피 추천은 2단계에서 구현됩니다.")
