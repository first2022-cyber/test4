```python
import json
import re
import requests
import pandas as pd
import plotly.express as px
import streamlit as st


# =========================================================
# 기본 설정
# =========================================================
st.set_page_config(
    page_title="전국 고령화 단계구분도",
    page_icon="🗺️",
    layout="wide"
)

st.title("🗺️ 전국 고령화 단계구분도")
st.caption("2026년 6월 기준 · 시군구별 65세 이상 인구 비율")

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


# =========================================================
# 데이터 불러오기
# =========================================================
@st.cache_data(show_spinner="인구 및 경계 데이터를 불러오는 중...")
def load_population():
    response = requests.get(POPULATION_URL, timeout=120)
    response.raise_for_status()

    # 압축 ZIP 파일을 pandas로 직접 읽기
    from io import BytesIO

    df = pd.read_csv(
        BytesIO(response.content),
        compression="zip",
        dtype={"코드": "string"},
        low_memory=False
    )

    # 연도 자료는 2026년만 사용
    df["연도"] = pd.to_numeric(df["연도"], errors="coerce")
    df = df.loc[df["연도"] == 2026].copy()

    if df.empty:
        raise ValueError("인구 데이터에서 2026년 자료를 찾지 못했습니다.")

    # 코드는 문자열로 유지하고 앞의 5자리로 시군구 코드 생성
    df["코드"] = (
        df["코드"]
        .astype("string")
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
        .str.zfill(10)
    )
    df["시군구코드"] = df["코드"].str[:5]

    # 계_로 시작하는 나이별 인구 열만 사용
    total_cols = [
        col for col in df.columns
        if col.startswith("계_")
    ]

    if not total_cols:
        raise ValueError("'계_'로 시작하는 인구 열이 없습니다.")

    # 65세부터 100세 이상까지의 열
    elderly_cols = [
        col for col in total_cols
        if (
            re.fullmatch(r"계_(?:[6-9]\d|100)세", col)
            or col == "계_100세 이상"
        )
        and int(re.search(r"\d+", col).group()) >= 65
    ]

    if "계_65세" not in elderly_cols:
        raise ValueError("'계_65세' 열을 찾지 못했습니다.")

    # 인구 열을 숫자로 변환
    for col in total_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # 전체 인구 및 65세 이상 인구 계산
    df["전체인구"] = df[total_cols].sum(axis=1)
    df["65세이상인구"] = df[elderly_cols].sum(axis=1)

    # 시군구 코드별 합산
    result = (
        df.groupby("시군구코드", as_index=False)
        .agg(
            전체인구=("전체인구", "sum"),
            65세이상인구=("65세이상인구", "sum")
        )
    )

    result["고령화율"] = (
        result["65세이상인구"]
        .div(result["전체인구"].replace(0, pd.NA))
        .mul(100)
    )

    result["고령화율"] = result["고령화율"].astype(float)

    return result


@st.cache_data(show_spinner="시군구 경계 데이터를 불러오는 중...")
def load_geojson():
    response = requests.get(GEOJSON_URL, timeout=120)
    response.raise_for_status()

    geojson = response.json()

    # GeoJSON의 시군구 코드도 문자열로 통일
    for feature in geojson["features"]:
        properties = feature["properties"]

        code = str(properties["코드"]).strip()
        code = re.sub(r"\.0$", "", code)
        properties["코드"] = code.zfill(5)

    return geojson


# =========================================================
# 앱 실행
# =========================================================
try:
    population = load_population()
    geojson = load_geojson()

except Exception as e:
    st.error(f"데이터를 불러오거나 처리하는 중 오류가 발생했습니다: {e}")
    st.info(
        "GitHub 주소, 데이터 열 이름, ZIP 파일 형식 및 "
        "GeoJSON 속성을 확인해 주세요."
    )
    st.stop()


# =========================================================
# 경계 데이터의 코드와 인구 데이터 결합
# =========================================================
boundary_rows = []

for feature in geojson["features"]:
    properties = feature["properties"]

    boundary_rows.append({
        "코드": str(properties["코드"]).zfill(5),
        "시군구": properties.get("시군구", ""),
        "시도": properties.get("시도", "")
    })

boundaries = pd.DataFrame(boundary_rows)

# 경계 파일을 기준으로 결합하여 지도 경계를 유지
map_df = boundaries.merge(
    population,
    left_on="코드",
    right_on="시군구코드",
    how="left",
    validate="one_to_one"
)

# 코드로 매칭되지 않은 지역 확인
matched_count = map_df["고령화율"].notna().sum()
unmatched_count = map_df["고령화율"].isna().sum()

# 인구 데이터가 없는 경계는 색칠하지 않도록 별도 처리
map_df["표시용고령화율"] = map_df["고령화율"]

# GeoJSON과 지도 데이터가 모두 문자열 코드 기준으로 일치하도록 설정
map_geojson = geojson


# =========================================================
# 지표 카드
# =========================================================
valid = map_df.dropna(subset=["고령화율"]).copy()

if valid.empty:
    st.error(
        "경계 코드와 인구 코드가 일치하는 지역이 없습니다. "
        "두 데이터의 코드 형식을 확인해 주세요."
    )
    st.stop()

national_total = valid["전체인구"].sum()
national_elderly = valid["65세이상인구"].sum()

national_rate = (
    national_elderly / national_total * 100
    if national_total > 0 else 0
)

highest = valid.loc[valid["고령화율"].idxmax()]

c1, c2, c3 = st.columns(3)

c1.metric("분석 연도", "2026년")
c2.metric("전국 고령화율", f"{national_rate:.2f}%")
c3.metric("고령화율 최고 지역", str(highest["시군구"]))

st.divider()


# =========================================================
# Plotly 단계구분도
# =========================================================
fig = px.choropleth(
    map_df,
    geojson=map_geojson,
    locations="코드",
    featureidkey="properties.코드",
    color="표시용고령화율",
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
        "시군구": False,
        "시도": False,
        "표시용고령화율": False,
        "고령화율": ":.2f",
        "전체인구": ":,",
        "65세이상인구": ":,"
    },
    labels={
        "고령화율": "65세 이상 비율(%)",
        "전체인구": "전체 인구",
        "65세이상인구": "65세 이상 인구"
    }
)

# 경계만 표시하고 배경 지도는 숨김
fig.update_geos(
    visible=False,
    fitbounds="locations",
    showcountries=False,
    showsubunits=False,
    showcoastlines=False,
    showland=False,
    showframe=False
)

fig.update_traces(
    marker_line_color="white",
    marker_line_width=0.35,
    hovertemplate=(
        "<b>%{hovertext}</b><br>"
        "고령화율: %{customdata[3]:.2f}%<br>"
        "전체 인구: %{customdata[4]:,.0f}명<br>"
        "65세 이상 인구: %{customdata[5]:,.0f}명"
        "<extra></extra>"
    )
)

# Plotly Express의 customdata 인덱스에 의존하지 않도록
# 명시적인 hover 템플릿으로 다시 지정
fig.update_traces(
    customdata=map_df[
        ["시도", "시군구", "코드", "고령화율",
         "전체인구", "65세이상인구"]
    ].to_numpy(),
    hovertemplate=(
        "<b>%{customdata[1]}</b><br>"
        "고령화율: %{customdata[3]:.2f}%<br>"
        "전체 인구: %{customdata[4]:,.0f}명<br>"
        "65세 이상 인구: %{customdata[5]:,.0f}명"
        "<extra></extra>"
    )
)

fig.update_layout(
    height=850,
    margin=dict(l=0, r=0, t=20, b=0),
    coloraxis_colorbar=dict(
        title="고령화율(%)",
        ticksuffix="%"
    )
)

st.plotly_chart(fig, use_container_width=True)

st.caption(
    "색이 진할수록 65세 이상 인구 비율이 높습니다. "
    "색상 범위는 0~40%로 고정했습니다."
)


# =========================================================
# 데이터 표
# =========================================================
with st.expander("시군구별 고령화율 데이터 보기"):
    display_df = map_df[
        ["시도", "시군구", "코드", "전체인구",
         "65세이상인구", "고령화율"]
    ].copy()

    display_df = display_df.sort_values(
        "고령화율", ascending=False, na_position="last"
    )

    display_df["고령화율"] = display_df["고령화율"].round(2)

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True
    )

    csv = display_df.to_csv(index=False).encode("utf-8-sig")

    st.download_button(
        label="CSV 다운로드",
        data=csv,
        file_name="2026_시군구별_고령화율.csv",
        mime="text/csv"
    )


# =========================================================
# 처리 결과 안내
# =========================================================
st.caption(
    f"경계 지역 {len(map_df):,}개 · "
    f"인구 데이터 매칭 {matched_count:,}개 · "
    f"미매칭 {unmatched_count:,}개"
)

if unmatched_count:
    st.warning(
        "일부 경계 코드에 해당하는 인구 데이터가 없습니다. "
        "지도에서 해당 지역은 색상이 표시되지 않을 수 있습니다."
    )
```
