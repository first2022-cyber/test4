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
    page_title="전국 고령화 단계구분도",
    page_icon="🗺️",
    layout="wide"
)

st.title("🗺️ 전국 고령화 단계구분도")
st.write("2026년 6월 기준 시군구별 65세 이상 인구 비율")


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
# 2. 인구 데이터 불러오기 및 처리
# =====================================================
@st.cache_data(show_spinner="인구 데이터를 불러오는 중입니다...")
def load_population():

    response = requests.get(
        POPULATION_URL,
        timeout=120
    )
    response.raise_for_status()

    # ZIP 압축 CSV 읽기
    df = pd.read_csv(
        BytesIO(response.content),
        compression="zip",
        dtype={"코드": "string"},
        low_memory=False
    )

    # 2026년 자료만 선택
    df["연도"] = (
        df["연도"]
        .astype("string")
        .str.extract(r"(\d{4})", expand=False)
    )

    df = df.loc[df["연도"] == "2026"].copy()

    if df.empty:
        raise ValueError("2026년 인구 자료가 없습니다.")

    # 행정동 코드는 문자열로 유지
    df["코드"] = (
        df["코드"]
        .astype("string")
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
    )

    # 코드 앞 5자리로 시군구 코드 생성
    df["시군구코드"] = df["코드"].str[:5]

    # '계_'로 시작하는 모든 인구 열
    # 남_ 및 여_ 열은 합산하지 않음
    total_cols = [
        col for col in df.columns
        if col.startswith("계_")
    ]

    if not total_cols:
        raise ValueError("'계_'로 시작하는 인구 열이 없습니다.")

    # 65세 이상 인구 열 찾기
    elderly_cols = []

    for col in total_cols:
        match = re.fullmatch(
            r"계_(\d+)세(?:\s*이상)?",
            col
        )

        if match and int(match.group(1)) >= 65:
            elderly_cols.append(col)

    if "계_65세" not in elderly_cols:
        raise ValueError("'계_65세' 열을 찾을 수 없습니다.")

    # 인구 열을 숫자로 변환
    for col in total_cols:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        ).fillna(0)

    # 전체 인구 계산
    df["전체인구"] = df[total_cols].sum(axis=1)

    # 65세 이상 인구 계산
    df["65세이상인구"] = df[elderly_cols].sum(axis=1)

    # 시군구 코드별 인구 합산
    result = (
        df.groupby("시군구코드", as_index=False)
        .agg(
            전체인구=("전체인구", "sum"),
            **{
                "65세이상인구": ("65세이상인구", "sum")
            }
        )
    )

    # 고령화율 계산
    result["고령화율"] = (
        result["65세이상인구"]
        .div(result["전체인구"].replace(0, float("nan")))
        .mul(100)
    )

    return result


# =====================================================
# 3. 시군구 경계 GeoJSON 불러오기
# =====================================================
@st.cache_data(show_spinner="시군구 경계를 불러오는 중입니다...")
def load_geojson():

    response = requests.get(
        GEOJSON_URL,
        timeout=120
    )
    response.raise_for_status()

    geojson = response.json()

    # 경계 코드도 문자열로 통일
    for feature in geojson["features"]:

        properties = feature["properties"]

        code = str(properties["코드"]).strip()
        code = re.sub(r"\.0$", "", code)

        properties["코드"] = code.zfill(5)

    return geojson


# =====================================================
# 4. 데이터 불러오기
# =====================================================
try:
    population = load_population()
    geojson = load_geojson()

except Exception as e:
    st.error(f"데이터 처리 중 오류가 발생했습니다: {e}")
    st.stop()


# =====================================================
# 5. GeoJSON 경계 속성 추출
# =====================================================
boundary_rows = []

for feature in geojson["features"]:

    properties = feature["properties"]

    boundary_rows.append({
        "코드": str(properties["코드"]).zfill(5),
        "시군구": properties["시군구"],
        "시도": properties["시도"]
    })

boundaries = pd.DataFrame(boundary_rows)


# =====================================================
# 6. 코드 기준으로 인구 데이터 결합
# =====================================================
map_df = boundaries.merge(
    population,
    left_on="코드",
    right_on="시군구코드",
    how="left",
    validate="one_to_one"
)

matched_count = int(map_df["고령화율"].notna().sum())
unmatched_count = int(map_df["고령화율"].isna().sum())

valid = map_df.dropna(subset=["고령화율"]).copy()

if valid.empty:
    st.error(
        "인구 데이터와 경계 데이터의 코드가 일치하지 않습니다. "
        "두 파일의 코드 형식을 확인해 주세요."
    )
    st.stop()


# =====================================================
# 7. 주요 지표
# =====================================================
total_population = valid["전체인구"].sum()
elderly_population = valid["65세이상인구"].sum()

if total_population > 0:
    national_rate = (
        elderly_population / total_population * 100
    )
else:
    national_rate = 0

highest = valid.loc[valid["고령화율"].idxmax()]

col1, col2, col3 = st.columns(3)

col1.metric(
    "분석 연도",
    "2026년"
)

col2.metric(
    "전국 고령화율",
    f"{national_rate:.2f}%"
)

col3.metric(
    "고령화율이 가장 높은 시군구",
    str(highest["시군구"]),
    f"{highest['고령화율']:.2f}%"
)


st.divider()


# =====================================================
# 8. Plotly 단계구분도
# =====================================================
fig = px.choropleth(
    map_df,
    geojson=geojson,
    locations="코드",
    featureidkey="properties.코드",
    color="고령화율",
    color_continuous_scale=[
        "#fff5f0",
        "#fcbba1",
        "#fb6a4a",
        "#cb181d",
        "#67000d"
    ],
    range_color=(0, 40),
    hover_name="시군구",
    hover_data={
        "코드": False,
        "시도": False,
        "고령화율": ":.2f",
        "전체인구": ":,",
        "65세이상인구": ":,"
    },
    labels={
        "고령화율": "고령화율(%)",
        "전체인구": "전체 인구",
        "65세이상인구": "65세 이상 인구"
    }
)

# 배경 지도 타일 없이 경계만 표시
fig.update_geos(
    visible=False,
    fitbounds="locations",
    showcountries=False,
    showcoastlines=False,
    showland=False,
    showframe=False
)

# 시군구 경계선
fig.update_traces(
    marker_line_color="white",
    marker_line_width=0.35
)

# 지도 모양과 범례
fig.update_layout(
    height=800,
    margin={
        "l": 0,
        "r": 0,
        "t": 20,
        "b": 0
    },
    coloraxis_colorbar={
        "title": "고령화율(%)",
        "ticksuffix": "%"
    }
)

st.plotly_chart(
    fig,
    use_container_width=True
)

st.caption(
    "색이 진할수록 65세 이상 인구 비율이 높습니다. "
    "지도에서 시군구에 마우스를 올리면 인구와 고령화율을 확인할 수 있습니다."
)


# =====================================================
# 9. 시군구별 데이터 표
# =====================================================
with st.expander("시군구별 고령화율 데이터 확인"):

    display_df = map_df[
        [
            "시도",
            "시군구",
            "코드",
            "전체인구",
            "65세이상인구",
            "고령화율"
        ]
    ].copy()

    display_df = display_df.sort_values(
        "고령화율",
        ascending=False,
        na_position="last"
    )

    display_df["고령화율"] = (
        display_df["고령화율"].round(2)
    )

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True
    )

    csv_data = display_df.to_csv(
        index=False
    ).encode("utf-8-sig")

    st.download_button(
        label="CSV 다운로드",
        data=csv_data,
        file_name="2026_시군구별_고령화율.csv",
        mime="text/csv"
    )


# =====================================================
# 10. 데이터 매칭 결과
# =====================================================
st.caption(
    f"전체 경계: {len(map_df):,}개 | "
    f"매칭 성공: {matched_count:,}개 | "
    f"매칭 실패: {unmatched_count:,}개"
)

if unmatched_count > 0:
    st.warning(
        "일부 시군구는 인구 데이터와 연결되지 않았습니다. "
        "코드 형식 또는 데이터에 포함된 지역 범위를 확인해 주세요."
    )
