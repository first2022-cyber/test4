
import json
from io import BytesIO

import requests
import pandas as pd
import numpy as np
import streamlit as st
import plotly.express as px


# ==========================================
# 기본 설정
# ==========================================
st.set_page_config(
    page_title="전국 고령화 단계구분도",
    page_icon="🗺️",
    layout="wide"
)

st.title("🗺️ 전국 고령화 단계구분도")
st.caption("2026년 6월 기준 · 시군구별 65세 이상 인구 비율")

POPULATION_URL = (
    "https://raw.githubusercontent.com/first2022-cyber/"
    "contents/main/population_ts_yearly_gender.zip"
)

GEOJSON_URL = (
    "https://raw.githubusercontent.com/first2022-cyber/"
    "contents/main/sigungu_kr.geojson"
)


# ==========================================
# 데이터 불러오기
# ==========================================
@st.cache_data(ttl=3600)
def load_population():
    response = requests.get(POPULATION_URL, timeout=120)
    response.raise_for_status()

    # ZIP 압축 파일 직접 읽기
    df = pd.read_csv(BytesIO(response.content), compression="zip")

    # 열 이름의 앞뒤 공백 제거
    df.columns = df.columns.astype(str).str.strip()

    # 2026년 자료만 사용
    df["연도"] = pd.to_numeric(df["연도"], errors="coerce")
    df = df[df["연도"] == 2026].copy()

    if df.empty:
        raise ValueError("인구 자료에서 2026년 데이터를 찾지 못했습니다.")

    # 행정구역 코드 문자열 처리
    df["코드"] = (
        df["코드"]
        .astype("string")
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
        .str.zfill(10)
    )

    # 코드 앞 5자리로 시군구 코드 생성
    df["시군구코드"] = df["코드"].str[:5]

    # 전체 인구: 계_로 시작하는 모든 나이별 열
    total_cols = [
        col for col in df.columns
        if col.startswith("계_")
    ]

    if not total_cols:
        raise ValueError("'계_'로 시작하는 인구 열이 없습니다.")

    # 65세 이상 인구 열
    elderly_cols = [
        col for col in total_cols
        if (
            col == "계_100세 이상"
            or (
                col.startswith("계_")
                and col.endswith("세")
                and col[2:-1].isdigit()
                and int(col[2:-1]) >= 65
            )
        )
    ]

    if "계_65세" not in elderly_cols:
        raise ValueError("'계_65세' 열을 찾지 못했습니다.")

    if "계_100세 이상" not in elderly_cols:
        raise ValueError("'계_100세 이상' 열을 찾지 못했습니다.")

    # 인구 열 숫자 변환
    for col in total_cols:
        df[col] = pd.to_numeric(
            df[col].astype(str).str.replace(",", "", regex=False),
            errors="coerce"
        ).fillna(0)

    # 읍·면·동별 인구 계산
    df["전체인구"] = df[total_cols].sum(axis=1)
    df["65세이상인구"] = df[elderly_cols].sum(axis=1)

    # 시군구별 합계
    # 숫자로 시작하는 열 이름도 안전하게 처리
    result = (
        df.groupby("시군구코드", as_index=False)
        .agg({
            "전체인구": "sum",
            "65세이상인구": "sum"
        })
    )

    # 고령화율 계산
    result["고령화율"] = np.where(
        result["전체인구"] > 0,
        result["65세이상인구"] / result["전체인구"] * 100,
        np.nan
    )

    return result


@st.cache_data(ttl=3600)
def load_geojson():
    response = requests.get(GEOJSON_URL, timeout=60)
    response.raise_for_status()

    geo = response.json()

    if "features" not in geo:
        raise ValueError("GeoJSON에 features 항목이 없습니다.")

    # 경계 코드 문자열 통일
    for feature in geo["features"]:
        props = feature["properties"]

        code = str(props.get("코드", "")).strip()

        if code.endswith(".0"):
            code = code[:-2]

        props["코드"] = code.zfill(5)

    return geo


# ==========================================
# 앱 실행
# ==========================================
try:
    with st.spinner("인구 데이터와 시군구 경계를 불러오는 중입니다..."):
        population = load_population()
        geojson = load_geojson()

    # 경계 파일의 코드 집합
    boundary_codes = {
        feature["properties"]["코드"]
        for feature in geojson["features"]
    }

    # 인구 파일의 코드 집합
    population_codes = set(population["시군구코드"])

    # 코드 기준으로 매칭
    matched_codes = boundary_codes & population_codes

    if not matched_codes:
        st.error(
            "인구 데이터와 경계 데이터의 코드가 일치하지 않습니다. "
            "코드 열의 실제 값을 확인해 주세요."
        )
        st.stop()

    # 인구 자료가 있는 경계만 유지
    geojson["features"] = [
        feature
        for feature in geojson["features"]
        if feature["properties"]["코드"] in matched_codes
    ]

    map_df = population[
        population["시군구코드"].isin(matched_codes)
    ].copy()

    # 경계 GeoJSON의 시군구 이름 사용
    name_by_code = {
        feature["properties"]["코드"]:
        feature["properties"].get("시군구", "")
        for feature in geojson["features"]
    }

    map_df["시군구명"] = map_df["시군구코드"].map(name_by_code)

    # ==========================================
    # 지표 카드
    # ==========================================
    total_population = map_df["전체인구"].sum()
    elderly_population = map_df["65세이상인구"].sum()

    national_rate = (
        elderly_population / total_population * 100
        if total_population > 0
        else 0
    )

    col1, col2, col3 = st.columns(3)

    col1.metric("분석 연도", "2026년")
    col2.metric("분석 시군구", f"{len(map_df):,}곳")
    col3.metric("전국 고령화율", f"{national_rate:.2f}%")

    # ==========================================
    # 단계구분도
    # ==========================================
    st.subheader("시군구별 고령화율")

    fig = px.choropleth(
        map_df,
        geojson=geojson,
        locations="시군구코드",
        featureidkey="properties.코드",
        color="고령화율",
        color_continuous_scale="YlOrRd",
        hover_name="시군구명",
        hover_data={
            "시군구코드": False,
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
        showcountries=False,
        showcoastlines=False,
        showland=False,
        fitbounds="locations",
        bgcolor="rgba(0,0,0,0)"
    )

    # 지역 경계선
    fig.update_traces(
        marker_line_color="white",
        marker_line_width=0.45,
        hovertemplate=(
            "<b>%{hovertext}</b><br>"
            "고령화율: %{z:.2f}%<br>"
            "<extra></extra>"
        )
    )

    fig.update_layout(
        height=750,
        margin=dict(l=0, r=0, t=20, b=0),
        coloraxis_colorbar=dict(
            title="고령화율(%)",
            ticksuffix="%"
        ),
        paper_bgcolor="rgba(0,0,0,0)"
    )

    st.plotly_chart(fig, use_container_width=True)

    st.caption(
        "고령화율 = 65세 이상 인구 ÷ 전체 인구 × 100. "
        "인구 자료와 경계는 시군구 코드 기준으로 연결했습니다."
    )

    # ==========================================
    # 결과 표
    # ==========================================
    with st.expander("시군구별 계산 결과 보기"):
        display_df = (
            map_df[
                [
                    "시군구코드",
                    "시군구명",
                    "전체인구",
                    "65세이상인구",
                    "고령화율"
                ]
            ]
            .sort_values("고령화율", ascending=False)
            .rename(columns={
                "시군구코드": "코드",
                "시군구명": "시군구",
                "전체인구": "전체 인구",
                "65세이상인구": "65세 이상 인구",
                "고령화율": "고령화율(%)"
            })
        )

        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True
        )

        csv_data = display_df.to_csv(
            index=False,
            encoding="utf-8-sig"
        )

        st.download_button(
            label="계산 결과 CSV 다운로드",
            data=csv_data,
            file_name="2026_시군구별_고령화율.csv",
            mime="text/csv"
        )

except Exception as e:
    st.error(f"데이터 처리 중 오류가 발생했습니다: {e}")
    st.info(
        "데이터 URL, CSV 열 이름, GeoJSON 속성 이름 및 "
        "코드 형식을 확인해 주세요."
    )
