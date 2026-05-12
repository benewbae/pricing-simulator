import copy
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(
    page_title="약가 정책 시뮬레이터 v2",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── 상수 ──────────────────────────────────────────────────────
SIM_START   = 2026
SIM_END     = 2036
N_YEARS     = SIM_END - SIM_START  # 10
GROUP_REF   = 0.5355               # 첫 시행 시 그룹 최고가 = 오리지널의 53.55%
DATA_PATH   = Path(__file__).parent / "drug_list_2026_05_01.xlsx"

COMPANY_LABELS = [
    "Patent-off Original",
    "IPC",
    "Quasi-IPC",
    "GX",
]
LABEL_TO_KEY = {
    "Patent-off Original": "우리",
    "IPC":                 "혁신형",
    "Quasi-IPC":           "준혁신형",
    "GX":                  "일반GX",
}
GROUP_COLORS = {
    "Patent-off Original": "#1f77b4",
    "IPC":                 "#2ca02c",
    "Quasi-IPC":           "#ff7f0e",
    "GX":                  "#d62728",
}
PRODUCT_PALETTE = [
    "#e41a1c", "#377eb8", "#4daf4a",
    "#984ea3", "#ff7f00", "#a65628",
]

PRICE_SCHEDULE = {
    "우리":    [0.51, 0.49, 0.47, 0.45, 0.45, 0.45, 0.45, 0.45, 0.45, 0.45, 0.45],
    "일반GX":  [0.51, 0.49, 0.47, 0.45, 0.45, 0.45, 0.45, 0.45, 0.45, 0.45, 0.45],
    "혁신형":  [0.51, 0.49, 0.49, 0.49, 0.49, 0.49, 0.45, 0.45, 0.45, 0.45, 0.45],
    "준혁신형": [0.51, 0.49, 0.47, 0.47, 0.47, 0.47, 0.45, 0.45, 0.45, 0.45, 0.45],
}


# ── 약제급여목록 로드 ─────────────────────────────────────────
@st.cache_data
def load_drug_list() -> pd.DataFrame:
    df = pd.read_excel(DATA_PATH, header=0)
    df.columns = [
        "연번", "투여", "분류", "식약분류", "그룹코드", "주성분코드",
        "주성분수", "주성분명", "제품코드", "제품명", "업체명",
        "규격", "단위", "상한금액", "전일", "비고",
    ]
    df["상한금액"] = pd.to_numeric(df["상한금액"], errors="coerce")
    return df.dropna(subset=["상한금액"]).reset_index(drop=True)


# ── 계산 함수 ─────────────────────────────────────────────────
def get_price_pct(company_key: str, year_offset: int) -> float:
    s = PRICE_SCHEDULE[company_key]
    return s[min(year_offset, len(s) - 1)]


def effective_price_pct(curr_ratio: float, company_key: str, year_offset: int) -> float:
    """정책 목표와 현재가 중 낮은 값 반환 (정책은 가격을 올리지 않음)."""
    return min(curr_ratio, get_price_pct(company_key, year_offset))


def weighted_price(base: float, curr: float, key: str, offset: int, months_pre: int) -> float:
    curr_ratio  = curr / base
    months_post = 12 - months_pre
    h1 = curr_ratio if offset == 0 else effective_price_pct(curr_ratio, key, offset - 1)
    h2 = effective_price_pct(curr_ratio, key, offset)
    return (months_pre / 12) * base * h1 + (months_post / 12) * base * h2


def revenue_series(p: dict, impl_month: int, vol_growth_list: list):
    """연도별 Revenue, AOI 반환 (단위: 백만원). 2026 시작.

    vol_growth_list: 연도별 YoY 성장률(%) 리스트, 인덱스 0=2026, 1=2027, ...
    vol[y] = current_volume × ∏(1 + gr[j]/100) for j in 0..y
    """
    key        = LABEL_TO_KEY[p["company_label"]]
    mp         = impl_month - 1
    aoi        = p["aoi_pct"] / 100
    base_price = p["group_max_price"] / GROUP_REF

    years = list(range(SIM_START, SIM_END + 1))
    revs  = []
    aois  = []

    for y in range(N_YEARS + 1):
        vol_factor = 1.0
        for j in range(y + 1):
            gr = vol_growth_list[j] / 100 if j < len(vol_growth_list) else 0.0
            vol_factor *= (1 + gr)
        wp  = weighted_price(base_price, p["current_price"], key, y, mp)
        vol = p["volume"] * vol_factor
        rev = wp * vol / 1e6
        revs.append(rev)
        aois.append(rev * aoi)

    return years, revs, aois


def req_volume_series(p: dict, impl_month: int, target_mult: float):
    """필요 볼륨 (현재 대비 %) 반환. 2026 시작."""
    key        = LABEL_TO_KEY[p["company_label"]]
    mp         = impl_month - 1
    base_price = p["group_max_price"] / GROUP_REF
    base_rev   = p["current_price"] * p["volume"]
    target_rev = base_rev * target_mult

    years    = list(range(SIM_START, SIM_END + 1))
    req_pcts = []

    for y in range(N_YEARS + 1):
        wp = weighted_price(base_price, p["current_price"], key, y, mp)
        req_pcts.append(target_rev / wp / p["volume"] * 100)

    return years, req_pcts


# ── Session State 초기화 ───────────────────────────────────────
DEFAULT = {
    "name":            "제품 A",
    "company_label":   "Patent-off Original",
    "group_max_price": 5_355,
    "current_price":   5_200,
    "volume":          100_000,
    "aoi_pct":         20.0,
}
if "products" not in st.session_state:
    st.session_state.products = [copy.deepcopy(DEFAULT)]

# 검색 결과 임시 저장용
if "search_results" not in st.session_state:
    st.session_state.search_results = {}


# ── 약가 조회 헬퍼 ────────────────────────────────────────────
def search_group_max(df: pd.DataFrame, ingredient: str, strength: str) -> tuple[int | None, pd.DataFrame]:
    """성분명+함량으로 그룹 내 최고가 반환."""
    mask = (
        df["주성분명"].str.contains(ingredient, case=False, na=False) &
        df["주성분명"].str.contains(strength,   case=False, na=False)
    )
    hits = df[mask]
    if hits.empty:
        return None, hits
    # 첫 번째 매칭 제품의 그룹코드로 같은 그룹 전체 조회
    group_code = hits.iloc[0]["그룹코드"]
    group_df   = df[df["그룹코드"] == group_code]
    max_price  = int(group_df["상한금액"].max())
    return max_price, group_df


def search_product_price(df: pd.DataFrame, product_name: str) -> pd.DataFrame:
    """제품명으로 매칭 제품 목록 반환."""
    mask = df["제품명"].str.contains(product_name, case=False, na=False)
    return df[mask][["제품명", "업체명", "규격", "단위", "상한금액"]].head(20)


# ── 사이드바 ──────────────────────────────────────────────────
drug_df = load_drug_list() if DATA_PATH.exists() else None

with st.sidebar:
    st.header("⚙️ 설정")

    impl_month = st.slider("정책 시행 월", 1, 12, 10, format="%d월")
    mp = impl_month - 1
    st.caption(f"H1 {mp}개월 (구 약가)  /  H2 {12-mp}개월 (신 약가)")

    st.divider()
    n = len(st.session_state.products)
    st.subheader(f"💊 제품 목록  ({n}개)")

    to_delete = None
    for i, p in enumerate(st.session_state.products):
        base_price = p["group_max_price"] / GROUP_REF
        curr_pct   = p["current_price"] / base_price * 100

        with st.expander(f"🔴 {p['name']}  (현재 {curr_pct:.0f}%)", expanded=(i == 0)):

            # ── 약가 자동 조회 ──────────────────────────────
            if drug_df is not None:
                st.markdown("**🔍 약가 자동 조회**")

                # 블록 A: 그룹 최고가
                with st.container():
                    st.caption("그룹 최고가 조회 (성분명 + 함량)")
                    col_a1, col_a2 = st.columns(2)
                    with col_a1:
                        ing  = st.text_input("성분명", key=f"ing_{i}", placeholder="atorvastatin")
                    with col_a2:
                        strn = st.text_input("함량",   key=f"strn_{i}", placeholder="10mg")
                    if st.button("최고가 조회", key=f"gbtn_{i}", use_container_width=True):
                        if ing and strn:
                            max_p, grp_df = search_group_max(drug_df, ing, strn)
                            if max_p is not None:
                                p["group_max_price"] = max_p
                                st.session_state[f"gm_{i}"] = max_p
                                st.session_state.search_results[f"grp_{i}"] = {
                                    "max": max_p, "count": len(grp_df),
                                    "ingredient": grp_df.iloc[0]["주성분명"],
                                }
                                st.rerun()
                            else:
                                st.warning("매칭 결과 없음 — 성분명/함량 확인 필요")
                        else:
                            st.warning("성분명과 함량을 모두 입력하세요")

                if f"grp_{i}" in st.session_state.search_results:
                    r = st.session_state.search_results[f"grp_{i}"]
                    st.success(
                        f"✅ 그룹 최고가 **{r['max']:,}원** (그룹 내 {r['count']}개 제품)\n\n"
                        f"주성분: {r['ingredient']}"
                    )

                st.divider()

                # 블록 B: 내 제품 약가
                with st.container():
                    st.caption("내 제품 약가 조회 (제품명)")
                    prod_q = st.text_input("제품명", key=f"prodq_{i}", placeholder="리피토")
                    if st.button("제품 조회", key=f"pbtn_{i}", use_container_width=True):
                        if prod_q:
                            results = search_product_price(drug_df, prod_q)
                            if not results.empty:
                                st.session_state.search_results[f"prod_{i}"] = results
                                st.rerun()
                            else:
                                st.warning("매칭 결과 없음 — 제품명 확인 필요")
                        else:
                            st.warning("제품명을 입력하세요")

                if f"prod_{i}" in st.session_state.search_results:
                    results = st.session_state.search_results[f"prod_{i}"]
                    options = [
                        f"{row['제품명']}  ({row['상한금액']:,}원)"
                        for _, row in results.iterrows()
                    ]
                    sel_idx  = st.selectbox("제품 선택", range(len(options)),
                                            format_func=lambda x: options[x],
                                            key=f"psel_{i}")
                    sel_row  = results.iloc[sel_idx]
                    # 선택된 제품 정보 미리보기
                    st.info(
                        f"**{sel_row['제품명']}**\n\n"
                        f"업체: {sel_row['업체명']}  |  규격: {sel_row['규격']} {sel_row['단위']}"
                        f"  |  **상한금액: {int(sel_row['상한금액']):,}원**"
                    )
                    if st.button("✅ 이 제품으로 적용", key=f"papply_{i}", use_container_width=True):
                        full_name  = sel_row["제품명"]
                        clean_name = full_name.split("(")[0].strip()
                        new_price  = int(sel_row["상한금액"])
                        # 제품 별칭 & 현재가 — dict + 위젯 state 동시 업데이트
                        p["name"]          = clean_name
                        p["current_price"] = new_price
                        st.session_state[f"nm_{i}"] = clean_name
                        st.session_state[f"cp_{i}"] = new_price
                        # 동일 그룹 최고가 자동 조회 — dict + 위젯 state 동시 업데이트
                        prod_rows = drug_df[drug_df["제품명"] == full_name]
                        if not prod_rows.empty:
                            grp_code = prod_rows.iloc[0]["그룹코드"]
                            grp_df   = drug_df[drug_df["그룹코드"] == grp_code]
                            grp_max  = int(grp_df["상한금액"].max())
                            p["group_max_price"] = grp_max
                            st.session_state[f"gm_{i}"] = grp_max
                            st.session_state.search_results[f"grp_{i}"] = {
                                "max": grp_max, "count": len(grp_df),
                                "ingredient": grp_df.iloc[0]["주성분명"],
                            }
                        del st.session_state.search_results[f"prod_{i}"]
                        st.rerun()

                st.divider()
            else:
                st.caption("⚠️ 약제급여목록 파일 없음 — 수동 입력 모드")

            # ── 직접 입력 ───────────────────────────────────
            p["name"]          = st.text_input("제품명 (별칭)",   p["name"],            key=f"nm_{i}")
            p["company_label"] = st.selectbox("회사 분류", COMPANY_LABELS,
                                               index=COMPANY_LABELS.index(p["company_label"]),
                                               key=f"cl_{i}")
            p["group_max_price"] = st.number_input(
                "그룹 최고가 (원)", 1, value=p["group_max_price"], step=10, key=f"gm_{i}",
                help="같은 성분/용량 그룹 내 최고 상한금액 → 오리지널 역산 기준"
            )
            p["current_price"] = st.number_input(
                "현재 약가 (원)", 1, value=p["current_price"], step=10, key=f"cp_{i}",
                help="내가 분석할 제품의 현재 상한금액"
            )
            p["volume"]  = st.number_input("연간 볼륨",  1, value=p["volume"],  step=1_000, key=f"vl_{i}")
            p["aoi_pct"] = st.number_input("AOI (%)", 0.0, 100.0, value=p["aoi_pct"], step=0.5, key=f"ao_{i}")

            # ── 임팩 요약 ───────────────────────────────────
            base_price2  = p["group_max_price"] / GROUP_REF
            curr_pct2    = p["current_price"] / base_price2 * 100
            target_51    = base_price2 * 0.51
            cut_amt      = p["current_price"] - target_51
            cut_ppt      = curr_pct2 - 51.0

            st.markdown("---")
            st.caption(f"오리지널 역산: **{base_price2:,.0f}원**")
            if cut_ppt > 0:
                st.warning(
                    f"현재 위치 **{curr_pct2:.1f}%** → 51% 목표가 **{target_51:,.0f}원**\n\n"
                    f"인하액 ▼**{cut_amt:,.0f}원** / ▼**{cut_ppt:.1f}%p**"
                )
            else:
                st.success(
                    f"현재 위치 **{curr_pct2:.1f}%** — 이미 51% 이하\n\n"
                    f"1차 시행 시 추가 인하 없음"
                )

            if n > 1 and st.button("🗑 삭제", key=f"dl_{i}"):
                to_delete = i

    if to_delete is not None:
        st.session_state.products.pop(to_delete)
        st.rerun()

    st.divider()
    if n < 6:
        if st.button("＋ 제품 추가", use_container_width=True):
            new      = copy.deepcopy(DEFAULT)
            new["name"] = f"제품 {chr(65 + n)}"
            st.session_state.products.append(new)
            st.rerun()


# ── 메인 ─────────────────────────────────────────────────────
st.title("💊 약가 정책 시뮬레이터  v2")
st.caption("2026년 건정심 약가제도 개선방안  |  복수 제품 동시 비교")

products = st.session_state.products
years    = list(range(SIM_START, SIM_END + 1))

tab1, tab2, tab3 = st.tabs(["📈 약가 궤적", "Mode A — Revenue 비교", "Mode B — 목표 볼륨 비교"])


# ── Tab 1: 약가 궤적 ──────────────────────────────────────────
with tab1:
    st.subheader("그룹별 약가 궤적  +  제품 현재 포지션")

    fig = go.Figure()

    # 4개 그룹 궤적
    for label in COMPANY_LABELS:
        key = LABEL_TO_KEY[label]
        pts = [get_price_pct(key, y - SIM_START) * 100 for y in years]
        fig.add_trace(go.Scatter(
            x=years, y=pts, mode="lines+markers",
            name=label,
            line=dict(color=GROUP_COLORS[label], width=2),
            marker=dict(size=7),
        ))

    # 제품별 현재 포지션 (★)
    for i, p in enumerate(products):
        base_price = p["group_max_price"] / GROUP_REF
        curr_pct   = p["current_price"] / base_price * 100
        fig.add_trace(go.Scatter(
            x=[SIM_START], y=[curr_pct],
            mode="markers+text",
            name=f"{p['name']} 현재",
            marker=dict(size=16, symbol="star",
                        color=PRODUCT_PALETTE[i % len(PRODUCT_PALETTE)]),
            text=[f" {p['name']} ({curr_pct:.0f}%)"],
            textposition="middle right",
        ))

    max_pct = max(p["current_price"] / (p["group_max_price"] / GROUP_REF) * 100 for p in products)
    fig.add_hline(y=53.55, line_dash="dot", line_color="gray",
                  annotation_text="그룹 최고가 기준 53.55%", annotation_position="right")
    fig.add_hline(y=45, line_dash="dash", line_color="black",
                  annotation_text="최종 목표 45%", annotation_position="right")
    fig.update_layout(
        height=460, hovermode="x unified",
        xaxis_title="연도", yaxis_title="약가 (오리지널 대비 %)",
        yaxis=dict(range=[40, max(max_pct + 10, 75)]),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    st.plotly_chart(fig, use_container_width=True)

    # 스케줄 표
    sched_rows = []
    for label in COMPANY_LABELS:
        key = LABEL_TO_KEY[label]
        row = {"구분": label}
        prev = None
        for y in years:
            pct = get_price_pct(key, y - SIM_START) * 100
            row[str(y)] = f"{'▼ ' if prev is not None and pct < prev else ''}{pct:.0f}%"
            prev = pct
        sched_rows.append(row)
    st.dataframe(pd.DataFrame(sched_rows).set_index("구분"), use_container_width=True)


# ── Tab 2: Mode A ─────────────────────────────────────────────
with tab2:
    st.subheader("Mode A — 제품별 Revenue & AOI 동시 비교")

    # ── 연도×제품 볼륨 성장률 입력 ────────────────────────────
    st.markdown("**📊 연도별 볼륨 성장률 입력 (%/년)**")
    st.caption("각 셀에 해당 연도의 전년 대비 볼륨 성장률(%)을 입력하세요. 기본값 0 = 성장 없음.")

    prod_names  = [p["name"] for p in products]
    years_range = list(range(SIM_START, SIM_END + 1))

    # 제품 추가/삭제 시 행 동기화 (rows=제품, cols=연도)
    if ("vg_df" not in st.session_state or
            st.session_state.vg_df.index.tolist() != prod_names):
        prev = st.session_state.get("vg_df", pd.DataFrame())
        new_df = pd.DataFrame(0.0, index=prod_names, columns=years_range)
        for prod in prod_names:
            if prod in prev.index:
                new_df.loc[prod] = prev.loc[prod]
        st.session_state.vg_df = new_df

    col_cfg = {
        yr: st.column_config.NumberColumn(str(yr), min_value=-50.0, max_value=100.0,
                                           step=0.5, format="%.1f%%")
        for yr in years_range
    }
    edited_vg = st.data_editor(
        st.session_state.vg_df,
        column_config=col_cfg,
        use_container_width=True,
        key="vg_editor",
    )
    st.session_state.vg_df = edited_vg

    # ── Revenue / AOI 계산 ────────────────────────────────────
    fig_rev = go.Figure()
    fig_aoi = go.Figure()
    summary = []

    for i, p in enumerate(products):
        color           = PRODUCT_PALETTE[i % len(PRODUCT_PALETTE)]
        vol_growth_list = edited_vg.loc[p["name"]].tolist() if p["name"] in edited_vg.index else [0.0] * (N_YEARS + 1)
        yrs, revs, aois = revenue_series(p, impl_month, vol_growth_list)

        baseline = p["current_price"] * p["volume"] / 1e6  # 정책 前 연간 매출

        fig_rev.add_trace(go.Scatter(
            x=yrs, y=revs, mode="lines+markers",
            name=p["name"],
            line=dict(color=color, width=2.5), marker=dict(size=7),
        ))
        fig_aoi.add_trace(go.Scatter(
            x=yrs, y=aois, mode="lines+markers",
            name=p["name"],
            line=dict(color=color, width=2.5), marker=dict(size=7),
        ))
        summary.append({
            "name":     p["name"],
            "label":    p["company_label"],
            "baseline": baseline,
            "pre_aoi":  baseline * p["aoi_pct"] / 100,
            "yrs":      yrs,
            "revs":     revs,
            "aois":     aois,
        })

    col_r, col_a = st.columns(2)
    with col_r:
        st.markdown("**Revenue (백만원)**")
        fig_rev.update_layout(height=360, hovermode="x unified",
                               yaxis_title="Revenue (백만원)",
                               legend=dict(orientation="h", yanchor="bottom", y=1.02))
        st.plotly_chart(fig_rev, use_container_width=True)
    with col_a:
        st.markdown("**AOI (백만원)**")
        fig_aoi.update_layout(height=360, hovermode="x unified",
                               yaxis_title="AOI (백만원)",
                               legend=dict(orientation="h", yanchor="bottom", y=1.02))
        st.plotly_chart(fig_aoi, use_container_width=True)

    # ── 연도별 통합 테이블 ──────────────────────────────────────
    if summary:
        yrs_ref = summary[0]["yrs"]

        # Revenue / VG / Rev GR 통합 (제품별 3행)
        st.subheader("Revenue 분석")
        st.caption("Revenue: 백만원  |  VG: 볼륨 성장률 입력값  |  Rev GR: 약가인하+VG 통합  (2026=vs정책前, 2027~=YoY)  — Rev GR 행에 색상")

        rows = {}
        for s in summary:
            prod = s["name"]
            vgl = edited_vg.loc[prod].tolist() if prod in edited_vg.index else [0.0] * len(yrs_ref)

            rev_row = {"정책 前": f"{s['baseline']:,.1f}"}
            vg_row  = {"정책 前": "—"}
            gr_row  = {"정책 前": "—"}

            for yi, y in enumerate(yrs_ref):
                rev_row[str(y)] = f"{s['revs'][yi]:,.1f}"
                v = vgl[yi] if yi < len(vgl) else 0.0
                vg_row[str(y)] = f"{v:+.1f}%"
                gr = ((s["revs"][yi] / s["baseline"] - 1) if yi == 0
                      else (s["revs"][yi] / s["revs"][yi - 1] - 1)) * 100
                gr_row[str(y)] = f"{gr:+.1f}%"

            rows[(prod, "Revenue (백만)")] = rev_row
            rows[(prod, "VG (%)")]         = vg_row
            rows[(prod, "Rev GR (%)")]     = gr_row

        df_combined = pd.DataFrame.from_dict(rows, orient="index")
        df_combined.index = pd.MultiIndex.from_tuples(df_combined.index)

        def _color_gr(row):
            if row.name[1] != "Rev GR (%)":
                return [""] * len(row)
            result = []
            for val in row:
                if val == "—":
                    result.append("")
                    continue
                try:
                    num = float(val.replace("%", "").replace("+", ""))
                except ValueError:
                    result.append("")
                    continue
                norm = max(0.0, min(1.0, (num + 15) / 30))
                if norm < 0.5:
                    r, g = 255, int(510 * norm)
                else:
                    r, g = int(510 * (1 - norm)), 255
                result.append(f"background-color: rgb({r},{g},0); color: black")
            return result

        st.dataframe(
            df_combined.style.apply(_color_gr, axis=1),
            use_container_width=True,
        )

        # AOI / VG / AOI GR 통합 (Revenue와 동일 구조)
        st.subheader("AOI 분석")
        st.caption("AOI: 백만원  |  VG: 볼륨 성장률 입력값  |  AOI GR: 약가인하+VG 통합  (2026=vs정책前, 2027~=YoY)  — AOI GR 행에 색상")

        aoi_rows = {}
        for s in summary:
            prod = s["name"]
            vgl = edited_vg.loc[prod].tolist() if prod in edited_vg.index else [0.0] * len(yrs_ref)

            aoi_row = {"정책 前": f"{s['pre_aoi']:,.1f}"}
            vg_row  = {"정책 前": "—"}
            gr_row  = {"정책 前": "—"}

            for yi, y in enumerate(yrs_ref):
                aoi_row[str(y)] = f"{s['aois'][yi]:,.1f}"
                v = vgl[yi] if yi < len(vgl) else 0.0
                vg_row[str(y)] = f"{v:+.1f}%"
                gr = ((s["aois"][yi] / s["pre_aoi"] - 1) if yi == 0
                      else (s["aois"][yi] / s["aois"][yi - 1] - 1)) * 100
                gr_row[str(y)] = f"{gr:+.1f}%"

            aoi_rows[(prod, "AOI (백만)")] = aoi_row
            aoi_rows[(prod, "VG (%)")]     = vg_row
            aoi_rows[(prod, "AOI GR (%)")]  = gr_row

        df_aoi = pd.DataFrame.from_dict(aoi_rows, orient="index")
        df_aoi.index = pd.MultiIndex.from_tuples(df_aoi.index)

        def _color_aoi_gr(row):
            if row.name[1] != "AOI GR (%)":
                return [""] * len(row)
            result = []
            for val in row:
                if val == "—":
                    result.append("")
                    continue
                try:
                    num = float(val.replace("%", "").replace("+", ""))
                except ValueError:
                    result.append("")
                    continue
                norm = max(0.0, min(1.0, (num + 15) / 30))
                if norm < 0.5:
                    r, g = 255, int(510 * norm)
                else:
                    r, g = int(510 * (1 - norm)), 255
                result.append(f"background-color: rgb({r},{g},0); color: black")
            return result

        st.dataframe(
            df_aoi.style.apply(_color_aoi_gr, axis=1),
            use_container_width=True,
        )


# ── Tab 3: Mode B ─────────────────────────────────────────────
with tab3:
    st.subheader("Mode B — 목표 Revenue 유지를 위한 필요 볼륨 비교")

    target_pct  = st.slider("목표 수준 (현재 Revenue 대비 %)", 50, 150, 100, 5, key="tp_b")
    target_mult = target_pct / 100

    fig_b     = go.Figure()
    b_summary = []

    for i, p in enumerate(products):
        color = PRODUCT_PALETTE[i % len(PRODUCT_PALETTE)]
        yrs, req_pcts = req_volume_series(p, impl_month, target_mult)

        fig_b.add_trace(go.Scatter(
            x=yrs, y=req_pcts, mode="lines+markers",
            name=p["name"],
            line=dict(color=color, width=2.5), marker=dict(size=8),
        ))

        def yi(y): return yrs.index(y)

        b_summary.append({
            "제품":         p["name"],
            "분류":         p["company_label"],
            "2026 필요볼륨": f"{req_pcts[yi(2026)]:,.1f}%",
            "2027 필요볼륨": f"{req_pcts[yi(2027)]:,.1f}%",
            "2029 필요볼륨": f"{req_pcts[yi(2029)]:,.1f}%",
            "2032 필요볼륨": f"{req_pcts[yi(2032)]:,.1f}%",
            "2036 필요볼륨": f"{req_pcts[yi(SIM_END)]:,.1f}%",
        })

    fig_b.add_hline(y=100, line_dash="dot", line_color="gray",
                    annotation_text="현재 볼륨 기준 100%")
    fig_b.update_layout(
        height=450, hovermode="x unified",
        yaxis_title="필요 볼륨 (현재 대비 %)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    st.plotly_chart(fig_b, use_container_width=True)

    st.subheader("제품별 요약 테이블")
    st.dataframe(pd.DataFrame(b_summary).set_index("제품"), use_container_width=True)
