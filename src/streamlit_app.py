"""냉장고 사진 → 식재료 인식(1단계) → 레시피 추천(2단계).  실행: streamlit run src/streamlit_app.py"""
import hashlib
import re

import pandas as pd
import streamlit as st

from ingredients import AppError, Recognition, normalize_items, recognize, shrink_image, validate_image
from ratelimit import SlidingWindowLimiter
from recipes import MEAL_OPTIONS, SERVING_OPTIONS, TIME_OPTIONS, generate_recipes

# 무료 모델 한도를 방문자 한 명이 다 쓰지 못하게: (횟수, 초). 인식과 레시피 생성이 함께 센다.
SESSION_LIMIT = (10, 3600)
GLOBAL_LIMIT = (60, 3600)

st.set_page_config(page_title="냉장고 레시피 추천", page_icon="🥬")


@st.cache_resource
def global_limiter() -> SlidingWindowLimiter:
    """서버 프로세스 하나를 모든 방문자가 공유한다."""
    return SlidingWindowLimiter(*GLOBAL_LIMIT)


def session_limiter() -> SlidingWindowLimiter:
    if "limiter" not in st.session_state:
        st.session_state.limiter = SlidingWindowLimiter(*SESSION_LIMIT)
    return st.session_state.limiter


def acquire_quota() -> bool:
    """모델을 부르기 전에 호출 한도를 확인한다. 막히면 이유를 화면에 보이고 False."""
    session, shared = session_limiter(), global_limiter()
    if not session.try_acquire():
        st.error(f"요청이 너무 많아요. 약 {session.retry_after() // 60 + 1}분 뒤에 다시 시도해 주세요.")
        return False
    if not shared.try_acquire():
        st.error("지금 이용자가 많아 잠시 쉬고 있어요. 조금 뒤에 다시 시도해 주세요.")
        return False
    return True


def show_failure(error: Exception) -> None:
    """실패를 숨기지 않고 화면에 표시한다. AppError면 어떤 모델이 왜 실패했는지 시도 내역도 보인다.

    st.status 블록 바깥에서 불러야 한다. 안쪽에서 그리면 접힌 상자 속에 들어가 이유가 보이지 않는다.
    """
    if isinstance(error, AppError):
        st.error(error.message)
        if error.details:
            st.caption("시도 내역: " + " / ".join(esc(d) for d in error.details))
    else:  # 내부 오류 내용은 화면에 보이지 않는다
        st.error("예상치 못한 오류가 발생했어요. 다시 시도해 주세요.")


def esc(text: str) -> str:
    """모델이 만든 글이 마크다운(링크, 이미지, 수식 등)으로 해석되지 않게 특수문자를 이스케이프한다."""
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|>~$:<])", r"\\\1", str(text))


# ---------------------------------------------------------------- 1단계: 재료 인식


def identify(data: bytes, on_progress=None) -> Recognition:
    """검증 → 축소 → 모델 호출(재시도·폴백 포함). 실패하면 AppError."""
    say = on_progress or (lambda _msg: None)
    validate_image(data)
    say("사진 크기를 줄이는 중")
    small = shrink_image(data)
    say(f"  → {len(data) / 1024:.0f}KB → {len(small) / 1024:.0f}KB")
    return recognize(small, on_progress)


def run_recognition(data: bytes) -> None:
    if not acquire_quota():
        return
    st.session_state.pop("detected", None)  # 실패했는데 이전 결과가 이번 사진의 결과처럼 보이지 않게
    error = None
    with st.status("재료를 찾고 있어요… (최대 1~2분 걸릴 수 있어요)", expanded=True) as status:
        try:
            result = identify(data, st.write)
        except Exception as e:
            error = e
            status.update(label="인식에 실패했어요 (시도 기록 보기)", state="error", expanded=False)
        else:
            status.update(label="인식 완료", state="complete", expanded=False)
    if error is not None:
        show_failure(error)
        return
    st.session_state["detected"] = result.items
    st.session_state["detected_model"] = result.model
    st.session_state.editor_version = st.session_state.get("editor_version", 0) + 1


def render_step1() -> None:
    st.title("🥬 냉장고 재료 인식")
    st.caption("1단계 · 냉장고 사진을 올리면 안에 있는 식재료를 찾아 드려요.")

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
            st.session_state.pop("restored", None)
        st.image(data, width="stretch")
        if st.button("재료 인식하기", type="primary"):
            run_recognition(data)
    else:
        st.session_state.pop("image_id", None)
        if not st.session_state.get("restored"):  # 2단계에서 돌아온 목록은 사진이 없어도 남겨 둔다
            st.session_state.pop("detected", None)

    if "detected" not in st.session_state:
        return
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
        st.session_state["final_ingredients"] = final  # 2단계의 입력
        st.session_state.pop("recipes", None)
        st.session_state["step"] = 2
        st.rerun()


# ---------------------------------------------------------------- 2단계: 레시피 추천


def run_generation(ingredients: list[dict], options: dict) -> bool:
    """레시피를 만들어 세션에 저장한다. 성공하면 True, 실패하면 이유를 화면에 보이고 False."""
    if not acquire_quota():
        return False
    st.session_state.pop("recipes", None)  # 실패했는데 이전 레시피가 이번 결과처럼 보이지 않게
    error = None
    with st.status("레시피를 만들고 있어요… (최대 1~2분 걸릴 수 있어요)", expanded=True) as status:
        try:
            result = generate_recipes(ingredients, on_progress=st.write, **options)
        except Exception as e:
            error = e
            status.update(label="레시피 생성에 실패했어요 (시도 기록 보기)", state="error", expanded=False)
        else:
            status.update(label="레시피 생성 완료", state="complete", expanded=False)
    if error is not None:
        show_failure(error)
        return False
    st.session_state["recipes"] = result.recipes
    st.session_state["recipes_model"] = result.model
    return True


def render_recipe(recipe: dict) -> None:
    with st.container(border=True):
        st.subheader(esc(recipe["title"]))
        if recipe["summary"]:
            st.write(esc(recipe["summary"]))
        st.caption(f"⏱ {recipe['time_minutes']}분 · 난이도 {esc(recipe['difficulty'])} · {recipe['servings']}인분")
        if recipe["missing_ingredients"]:
            st.caption("🛒 부족한 재료: " + ", ".join(esc(m) for m in recipe["missing_ingredients"]))
        else:
            st.caption("✅ 가진 재료로 만들 수 있어요")
        with st.expander("자세히 보기"):
            st.markdown("**재료**")
            st.markdown(
                "\n".join(
                    f"- {'✅' if i['have'] else '🛒'} {esc(i['name'])}" + (f" · {esc(i['amount'])}" if i["amount"] else "")
                    for i in recipe["ingredients"]
                )
            )
            st.markdown("**조리 순서**")
            st.markdown("\n".join(f"{n}. {esc(s)}" for n, s in enumerate(recipe["steps"], 1)))


def render_step2() -> None:
    ingredients = st.session_state.get("final_ingredients") or []
    if not ingredients:
        st.session_state["step"] = 1
        st.rerun()

    st.title("🍳 레시피 추천")
    st.caption("2단계 · 인식한 재료로 만들 수 있는 레시피를 추천해 드려요.")
    if st.button("← 재료 다시 고치기"):
        st.session_state["detected"] = ingredients
        st.session_state["restored"] = True  # 사진 없이도 목록을 유지한다
        st.session_state.editor_version = st.session_state.get("editor_version", 0) + 1
        st.session_state["step"] = 1
        st.rerun()

    # 재료를 회색 칩처럼 한 줄로 (코드 표기가 회색 알약 모양으로 보인다)
    st.markdown(" ".join(f"`{i['name'].replace('`', chr(39))}`" for i in ingredients))

    c1, c2 = st.columns(2)
    servings = c1.selectbox("인원", SERVING_OPTIONS, index=1, format_func=lambda n: f"{n}인분")
    time_label = c2.selectbox("조리 시간", list(TIME_OPTIONS))
    meal = c1.selectbox("식사 종류", MEAL_OPTIONS)
    exclude = c2.text_input("제외할 재료 (쉼표로 구분)", placeholder="예: 땅콩, 새우", max_chars=100)
    options = {"servings": servings, "max_minutes": TIME_OPTIONS[time_label], "meal": meal, "exclude": exclude}

    has_result = "recipes" in st.session_state
    if st.button("다시 추천받기" if has_result else "레시피 만들기", type="primary"):
        if run_generation(ingredients, options):
            st.rerun()  # 성공하면 화면을 새로 그려 버튼 이름 등을 갱신한다 (실패 땐 오류 메시지가 사라지지 않게 그대로 둔다)

    if "recipes" in st.session_state:
        st.caption(
            f"레시피 모델: {st.session_state.get('recipes_model', '-')} · "
            "AI가 만든 레시피이므로 알레르기와 조리 과정은 직접 확인하세요."
        )
        for recipe in st.session_state["recipes"]:
            render_recipe(recipe)


if st.session_state.get("step", 1) == 2:
    render_step2()
else:
    render_step1()
