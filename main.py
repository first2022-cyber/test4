import re
from io import BytesIO

import pandas as pd
import plotly.express as px
import requests
import streamlit as st


# =====================================================
# 1. 기본 설정
# =====================================================
st.set_page_config(
    page_title="전국 고령화·유소년 단계구분도",
    page_icon="🗺️",
    layout="wide"
)

st.title("🗺️ 전국 고령화·유소년 단계구분도")
st.caption("읍·면·동 인구를 시군구 단위로 합산하여 지도에 표시합니다.")


POPULATION_URL = (
    "https://raw.githubusercontent.com/"
    "first2022-cyber/contents/main/"
    "population_ts_yearly_gender.zip"
)

GEOJSON_URL = (
    "https://raw.githubusercontent.com/"
    "first2022-cyber/contents/main/"
    "sigungu_kr.geojson"
)


# =====================================================
# 2. 공통 함수
# =====================================================
def normalize_code(value, length=None):
    """행정구역 코드를 문자열로 통일합니다."""
    if pd.isna(value):
        return None

    code = str(value).strip()
    code = re.sub(r"\.0$", "", code)

    if not code or code.lower() == "nan":
        return None

    if length is not None:
        code = code.zfill(length)

    return code


def correct_old_code(code):
    """옛 행정구역 코드를 현재 경계 코드에 맞춥니다."""
    if code is None:
        return None

    # 군위군
    if code.startswith("47720"):
        return "27720" + code[5:]

    # 옛 강원도 코드 42 -> 강원특별자치도 코드 51
    if code.startswith("42"):
        return "51" + code[2:]

    # 옛 전라북도 코드 45 -> 전북특별자치도 코드 52
    if code.startswith("45"):
        return "52" + code[2:]

    return code


def get_age_number(column):
    """계_65세, 계_100세 이상 등에서 나이를 추출합니다."""
    match = re.fullmatch(
        r"계_(\d+)세(?:\s*이상)?",
        str(column).strip()
    )

    if match:
        return int(match.group(1))

    return None


# =====================================================
# 3. 인구 데이터 불러오기
# =====================================================
@st.cache_data(show_spinner="인구 데이터를 불러오는 중입니다...")
def load_population():

    response = requests.get(
        POPULATION_URL,
        timeout=120
    )
    response.raise_for_status()

    df = pd.read_csv(
        BytesIO(response.content),
        compression="zip",
        dtype={"코드": "string"},
        low_memory=False
    )

    required = ["연도", "코드"]

    for col in required:
        if col not in df.columns:
            raise ValueError(
                f"인구 데이터에 '{col}' 열이 없습니다."
            )

    # 연도 정리
    df["연도"] = pd.to_numeric(
        df["연도"].astype("string").str.extract(r"(\d{4})")[0],
        errors="coerce"
    )

    df = df.dropna(subset=["연도", "코드"]).copy()
    df["연도"] = df["연도"].astype(int)

    # 코드 문자열 처리
    df["코드"] = df["코드"].apply(normalize_code)

    # 과거 코드 보정 후 앞 5자리로 시군구 코드 생성
    df["보정코드"] = df["코드"].apply(correct_old_code)
    df["시군구코드"] = df["보정코드"].str[:5]

    # 계_로 시작하는 모든 열만 전체 인구 계산에 사용
    total_cols = [
        col for col in df.columns
        if str(col).startswith("계_")
    ]

    if not total_cols:
        raise ValueError(
            "'계_'로 시작하는 인구 열을 찾을 수 없습니다."
        )

    # 나이별 인구 열 확인
    age_columns = {}

    for col in total_cols:
        age = get_age_number(col)

        if age is not None:
            age_columns[age] = col

    elderly_cols = [
        age_columns[age]
        for age in sorted(age_columns)
        if age >= 65
    ]

    youth_cols = [
        age_columns[age]
        for age in sorted(age_columns)
        if 0 <= age <= 14
    ]

    if 65 not in age_columns:
        raise ValueError("'계_65세' 열을 찾을 수 없습니다.")

    if not youth_cols:
        raise ValueError("0~14세 인구 열을 찾을 수 없습니다.")

    # 인구 열 숫자 변환
    for col in total_cols:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        ).fillna(0)

    # 전체 인구, 고령 인구, 유소년 인구
    df["전체인구"] = df[total_cols].sum(axis=1)
    df["65세이상인구"] = df[elderly_cols].sum(axis=1)
    df["유소년인구"] = df[youth_cols].sum(axis=1)

    # 연도별·시군구별 합산
    result = (
        df.groupby(
            ["연도", "시군구코드"],
            as_index=False
        )
        .agg(
            전체인구=("전체인구", "sum"),
            65세이상인구=("65세이상인구", "sum"),
            유소년인구=("유소년인구", "sum")
        )
    )

    # 비율 계산
    denominator = result["전체인구"].replace(0, float("nan"))

    result["고령화율"] = (
        result["65세이상인구"] / denominator * 100
    )

    result["유소년비율"] = (
        result["유소년인구"] / denominator * 100
    )

    return result


# =====================================================
# 4. GeoJSON 경계 불러오기
# =====================================================
@st.cache_data(show_spinner="시군구 경계를 불러오는 중입니다...")
def load_geojson():

    response = requests.get(
        GEOJSON_URL,
        timeout=120
    )
    response.raise_for_status()

    geojson = response.json()

    for feature in geojson["features"]:
        props = feature["properties"]

        props["코드"] = normalize_code(
            props.get("코드"),
            5
        )

    return geojson


try:
    population = load_population()
    geojson = load_geojson()

except Exception as e:
    st.error(f"데이터를 불러오는 중 오류가 발생했습니다: {e}")
    st.stop()


# =====================================================
# 5. 경계 속성 추출 및 코드 보정
# =====================================================
boundary_rows = []

for feature in geojson["features"]:
    props = feature["properties"]

    boundary_rows.append({
        "코드": normalize_code(props.get("코드"), 5),
        "시군구": props.get("시군구", "이름 없음"),
        "시도": props.get("시도", "시도 미상")
    })

boundaries = pd.DataFrame(boundary_rows)
boundaries = boundaries.dropna(subset=["코드"]).copy()

# 인구 코드도 5자리 문자열로 통일
population["시군구코드"] = (
    population["시군구코드"]
    .apply(normalize_code)
    .str.zfill(5)
)

# 경계 코드 기준으로 결합
map_all = boundaries.merge(
    population,
    left_on="코드",
    right_on="시군구코드",
    how="left",
    validate="one_to_many"
)

# 지도 경계는 시군구별 한 개씩 유지해야 하므로
# 연도 선택 후 다시 결합합니다.
# 연도별 자료를 미리 경계와 결합하지 않습니다.


# =====================================================
# 6. 선택 UI
# =====================================================
available_years = sorted(
    population["연도"].dropna().astype(int).unique().tolist()
)

if not available_years:
    st.error("선택할 수 있는 연도 자료가 없습니다.")
    st.stop()

default_year = 2026 if 2026 in available_years else available_years[-1]

selected_year = st.slider(
    "연도 선택",
    min_value=int(min(available_years)),
    max_value=int(max(available_years)),
    value=int(default_year),
    step=1,
    format="%d년"
)

col_region, col_metric = st.columns(2)

with col_region:
    province_options = ["전국"] + sorted(
        boundaries["시도"].dropna().astype(str).unique().tolist()
    )

    selected_province = st.selectbox(
        "시도 선택",
        options=province_options,
        index=0
    )

with col_metric:
    selected_metric_label = st.selectbox(
        "지도 지표 선택",
        options=[
            "고령화율 (65세 이상 비율)",
            "유소년 비율 (0~14세 비율)"
        ],
        index=0
    )

if selected_metric_label.startswith("고령화율"):
    metric_col = "고령화율"
    metric_name = "고령화율"
    metric_population_col = "65세이상인구"

    # 단계 경계는 모든 연도에서 동일
    bins = [-float("inf"), 19, 23, 28, 38, float("inf")]
    stage_labels = [
        "19% 미만",
        "19~23% 미만",
        "23~28% 미만",
        "28~38% 미만",
        "38% 이상"
    ]

    colors = [
        "#fff5f0",
        "#fcbba1",
        "#fc9272",
        "#de2d26",
        "#67000d"
    ]

else:
    metric_col = "유소년비율"
    metric_name = "유소년 비율"
    metric_population_col = "유소년인구"

    # 유소년 비율 전용 단계 경계
    bins = [-float("inf"), 6, 9, 12, 15, float("inf")]
    stage_labels = [
        "6% 미만",
        "6~9% 미만",
        "9~12% 미만",
        "12~15% 미만",
        "15% 이상"
    ]

    colors = [
        "#f7fbff",
        "#c6dbef",
        "#6baed6",
        "#2171b5",
        "#08306b"
    ]


# =====================================================
# 7. 선택한 연도 데이터 결합
# =====================================================
year_df = population.loc[
    population["연도"] == int(selected_year)
].copy()

map_df = boundaries.merge(
    year_df,
    left_on="코드",
    right_on="시군구코드",
    how="left",
    validate="one_to_one"
)

# 시도 선택
if selected_province != "전국":
    map_df = map_df.loc[
        map_df["시도"].astype(str) == selected_province
    ].copy()

    selected_codes = set(map_df["코드"])

    filtered_geojson = {
        "type": geojson["type"],
        "features": [
            feature
            for feature in geojson["features"]
            if normalize_code(
                feature["properties"].get("코드"),
                5
            ) in selected_codes
        ]
    }
else:
    filtered_geojson = geojson


# =====================================================
# 8. 전국 지표 계산
# =====================================================
national_df = boundaries.merge(
    year_df,
    left_on="코드",
    right_on="시군구코드",
    how="left",
    validate="one_to_one"
)

national_total = national_df["전체인구"].sum(skipna=True)

if national_total > 0:
    if metric_col == "고령화율":
        national_rate = (
            national_df["65세이상인구"].sum(skipna=True)
            / national_total * 100
        )
    else:
        national_rate = (
            national_df["유소년인구"].sum(skipna=True)
            / national_total * 100
        )
else:
    national_rate = float("nan")

valid_national = national_df.dropna(subset=[metric_col])

if not valid_national.empty:
    highest = valid_national.loc[valid_national[metric_col].idxmax()]
    lowest = valid_national.loc[valid_national[metric_col].idxmin()]

    highest_text = (
        f"{highest['시군구']} ({highest[metric_col]:.2f}%)"
    )
    lowest_text = (
        f"{lowest['시군구']} ({lowest[metric_col]:.2f}%)"
    )
else:
    highest_text = "자료 없음"
    lowest_text = "자료 없음"


# =====================================================
# 9. 지표 카드
# =====================================================
st.subheader(f"{selected_year}년 전국 지표")

card1, card2, card3 = st.columns(3)

card1.metric(
    f"전국 {metric_name}",
    f"{national_rate:.2f}%"
    if pd.notna(national_rate)
    else "자료 없음"
)

card2.metric(
    f"{metric_name} 최고 시군구",
    highest_text
)

card3.metric(
    f"{metric_name} 최저 시군구",
    lowest_text
)


# =====================================================
# 10. 단계별 색상 분류
# =====================================================
map_df["단계"] = pd.cut(
    map_df[metric_col],
    bins=bins,
    labels=stage_labels,
    right=False
).astype("object")

# 자료가 없는 지역은 회색
map_df["단계"] = map_df["단계"].where(
    map_df[metric_col].notna(),
    "자료 없음"
)

color_map = {
    stage_labels[i]: colors[i]
    for i in range(len(stage_labels))
}

color_map["자료 없음"] = "#bdbdbd"

category_orders = {
    "단계": stage_labels + ["자료 없음"]
}


# =====================================================
# 11. Plotly 단계구분도
# =====================================================
if map_df.empty:
    st.warning("선택한 시도에 해당하는 경계가 없습니다.")
    st.stop()

fig = px.choropleth(
    map_df,
    geojson=filtered_geojson,
    locations="코드",
    featureidkey="properties.코드",
    color="단계",
    category_orders=category_orders,
    color_discrete_map=color_map,
    custom_data=[
        "시도",
        "시군구",
        "코드",
        metric_col,
        "전체인구",
        metric_population_col
    ],
    hover_name="시군구"
)

fig.update_traces(
    marker_line_color="white",
    marker_line_width=0.45,
    hovertemplate=(
        "<b>%{customdata[0]} %{customdata[1]}</b><br>"
        "코드: %{customdata[2]}<br>"
        + metric_name + ": %{customdata[3]:.2f}%<br>"
        "전체 인구: %{customdata[4]:,.0f}명<br>"
        + (
            "65세 이상 인구: "
            if metric_col == "고령화율"
            else "0~14세 인구: "
        )
        + "%{customdata[5]:,.0f}명"
        "<extra></extra>"
    )
)

fig.update_geos(
    visible=False,
    fitbounds="locations",
    showcountries=False,
    showcoastlines=False,
    showland=False,
    showframe=False
)

fig.update_layout(
    height=800,
    margin=dict(l=0, r=0, t=20, b=0),
    legend_title_text="단계",
    legend=dict(
        orientation="h",
        yanchor="bottom",
        y=1.02,
        xanchor="left",
        x=0
    )
)

st.plotly_chart(
    fig,
    use_container_width=True
)


# =====================================================
# 12. 지도 안내
# =====================================================
st.caption(
    f"표시 연도: {selected_year}년 · "
    f"지역: {selected_province} · "
    f"지표: {metric_name}"
)

st.caption(
    "고령화율 단계 경계: 19% · 23% · 28% · 38% / "
    "유소년 비율 단계 경계: 6% · 9% · 12% · 15%"
)

unmatched_df = map_df.loc[
    map_df[metric_col].isna(),
    ["시도", "시군구", "코드"]
].copy()

if not unmatched_df.empty:
    st.warning(
        f"회색 지역은 선택한 연도의 인구 자료와 경계 코드가 "
        f"일치하지 않거나 해당 지표를 계산할 수 없는 지역입니다. "
        f"현재 지도에서 {len(unmatched_df)}개 지역에 자료가 없습니다."
    )

    with st.expander("자료가 없는 지역 확인"):
        st.dataframe(
            unmatched_df,
            use_container_width=True,
            hide_index=True
        )


# =====================================================
# 13. 시군구별 데이터 표 및 CSV
# =====================================================
with st.expander("시군구별 인구 및 비율 데이터 보기"):

    table_cols = [
        "시도",
        "시군구",
        "코드",
        "전체인구",
        "65세이상인구",
        "유소년인구",
        "고령화율",
        "유소년비율"
    ]

    table_df = map_df[table_cols].copy()

    for col in ["고령화율", "유소년비율"]:
        table_df[col] = table_df[col].round(2)

    table_df = table_df.sort_values(
        metric_col,
        ascending=False,
        na_position="last"
    )

    st.dataframe(
        table_df,
        use_container_width=True,
        hide_index=True
    )

    csv_data = table_df.to_csv(
        index=False
    ).encode("utf-8-sig")

    st.download_button(
        "CSV 다운로드",
        data=csv_data,
        file_name=f"{selected_year}_시군구_인구지표.csv",
        mime="text/csv"
    )
