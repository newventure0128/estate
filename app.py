"""
Deal Screener — Streamlit UI (app.py)

엔진(engine.py)을 호출만 한다. 계산 로직은 절대 여기 두지 않는다.
규약: 모든 % 입력은 퍼센트포인트(5 == 5%). 표시 단계에서만 ×100·반올림.
실행: streamlit run app.py
"""
from __future__ import annotations

import math
from dataclasses import replace

import matplotlib

matplotlib.use("Agg")  # 서버(헤드리스) 렌더링
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import streamlit as st

from engine import (
    Deal, validate, evaluate, noi_breakdown, sizing_constraints,
    max_acquisition_price, break_even,
)

# --------------------------------------------------------------------------- #
# 전역 설정
# --------------------------------------------------------------------------- #
st.set_page_config(page_title="딜 스크리너", page_icon="🏢", layout="wide")

# 딜 1건이 보유하는 필드(전역 투자기준 3종은 제외 — 패널에서 일괄 적용).
PER_DEAL_FIELDS = [
    "name", "price", "acq_cost_pct", "gpr1", "other_income", "vacancy",
    "opex_basis", "opex1", "opex_growth", "rent_growth", "hold_years",
    "exit_cap", "selling_cost_pct", "ltv", "rate", "amort_type",
    "amort_term_years", "capex_reserve_pct",
]

# 1D/2D 민감도 대상 변수 (라벨 → 엔진 필드). matplotlib 라벨은 폰트 안전상 ASCII.
SENS_FIELDS = {
    "Exit Cap": "exit_cap",
    "임대료 성장률": "rent_growth",
    "공실률": "vacancy",
    "대출금리": "rate",
}
SENS_LABEL_EN = {
    "exit_cap": "Exit Cap (%)",
    "rent_growth": "Rent Growth (%)",
    "vacancy": "Vacancy (%)",
    "rate": "Loan Rate (%)",
}


# --------------------------------------------------------------------------- #
# 표시용 포맷터 (표시 단계에서만 반올림)
# --------------------------------------------------------------------------- #
def fmt_won(x: float) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "N/A"
    sign = "-" if x < 0 else ""
    a = abs(x)
    if a >= 1e8:
        return f"{sign}{a / 1e8:,.2f}억"
    return f"{sign}{a:,.0f}원"


def fmt_pct(x: float, dp: int = 2) -> str:
    if x is None or (isinstance(x, float) and (math.isnan(x))):
        return "N/A"
    if math.isinf(x):
        return "∞"
    return f"{x * 100:.{dp}f}%"


def fmt_mult(x: float) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "N/A"
    if math.isinf(x):
        return "∞"
    return f"{x:.2f}x"


def fmt_dscr(x: float) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "N/A"
    if math.isinf(x):
        return "∞"
    return f"{x:.2f}"


def fmt_spread(x: float) -> str:
    """캡 스프레드 등 %p 차이값 — 부호 포함 표기 (+면 캡 확장=가치 위험)."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "N/A"
    return f"{x * 100:+.2f}%p"


# --------------------------------------------------------------------------- #
# 프리셋 — 합성 데이터(실거래 아님), 한국 시장 범위로 보정
# --------------------------------------------------------------------------- #
def preset_deals() -> list[dict]:
    return [
        # 모두 '안정화된 수익형 자산' 예시 — 합성 데이터(실거래 아님), 한국 시장 범위로 보정.
        # 자산유형 간 대비로 NOI→IRR→민감도와 양/음의 레버리지를 시연한다.
        # 1) 프라임 오피스: 캡(~4%) < 금리(4.5%) → 음의 레버리지 사례
        dict(
            name="프라임 오피스(도심)", price=50_000_000_000, acq_cost_pct=5.5,
            gpr1=3_000_000_000, other_income=300_000_000, vacancy=4.0,
            opex_basis="pct", opex1=38.0, opex_growth=2.0, rent_growth=2.5,
            hold_years=5, exit_cap=4.5, selling_cost_pct=2.0,
            ltv=50.0, rate=4.5, amort_type="IO", amort_term_years=30,
            capex_reserve_pct=7.0,   # 오피스 TI/LC 부담 큼
        ),
        # 2) 물류센터: 캡(~6.6%) > 금리(5%) → 양의 레버리지, PASS 후보
        dict(
            name="물류센터(수도권)", price=20_000_000_000, acq_cost_pct=5.0,
            gpr1=1_900_000_000, other_income=100_000_000, vacancy=6.0,
            opex_basis="pct", opex1=30.0, opex_growth=2.0, rent_growth=1.5,
            hold_years=5, exit_cap=6.5, selling_cost_pct=2.0,
            ltv=60.0, rate=5.0, amort_type="IO", amort_term_years=30,
            capex_reserve_pct=3.0,   # 물류 TI/LC 경미
        ),
        # 3) 임대주택 리츠: 중간 캡(~5%), 저변동 — 안정형
        dict(
            name="임대주택 리츠", price=24_000_000_000, acq_cost_pct=5.5,
            gpr1=1_900_000_000, other_income=50_000_000, vacancy=5.0,
            opex_basis="pct", opex1=36.0, opex_growth=2.0, rent_growth=2.0,
            hold_years=10, exit_cap=5.0, selling_cost_pct=2.0,
            ltv=50.0, rate=4.0, amort_type="IO", amort_term_years=30,
            capex_reserve_pct=5.0,
        ),
    ]


def blank_deal() -> dict:
    return dict(
        name="새 딜", price=10_000_000_000, acq_cost_pct=5.5,
        gpr1=800_000_000, other_income=0, vacancy=5.0,
        opex_basis="pct", opex1=35.0, opex_growth=2.0, rent_growth=2.0,
        hold_years=5, exit_cap=6.0, selling_cost_pct=2.0,
        ltv=50.0, rate=4.5, amort_type="IO", amort_term_years=30,
        capex_reserve_pct=5.0,
    )


# --------------------------------------------------------------------------- #
# 세션 상태
# --------------------------------------------------------------------------- #
def init_state():
    if "deals" not in st.session_state:
        st.session_state.deals = preset_deals()
    if "sel" not in st.session_state:
        st.session_state.sel = 0
    if "criteria" not in st.session_state:
        st.session_state.criteria = dict(hurdle=8.0, min_dscr=1.25, max_ltv=70.0)
    # 옛 세션 자가 치유: 코드 업데이트로 신규 선택 필드가 생겨도 기존 세션 딜에 백필.
    for dd in st.session_state.deals:
        for k, v in _OPTIONAL_DEFAULTS.items():
            dd.setdefault(k, v)


# 선택(나중에 추가된) 필드의 안전 기본값. 엔진 introspection에 의존하지 않으므로
# Streamlit이 engine 모듈을 재로딩하지 않아도, 옛 세션 딜에 키가 없어도 안전하다.
_OPTIONAL_DEFAULTS = {
    "capex_reserve_pct": 0.0,
}


def build_deal(dd: dict, crit: dict) -> Deal:
    """딜 dict + 전역 투자기준 → Deal 객체. 누락된 선택 필드는 안전 기본값으로 보강."""
    vals = {k: dd.get(k, _OPTIONAL_DEFAULTS.get(k)) for k in PER_DEAL_FIELDS}
    return Deal(
        hurdle_irr=crit["hurdle"], min_dscr=crit["min_dscr"], max_ltv=crit["max_ltv"],
        **vals,
    )


# --------------------------------------------------------------------------- #
# 민감도 헬퍼 (엔진 호출만 — 계산 로직 없음)
# --------------------------------------------------------------------------- #
def eval_equity_irr(base: Deal, **override):
    """필드 오버라이드 후 검증·평가 → Equity IRR(소수) 또는 None(산출 불가)."""
    d2 = replace(base, **override)
    if validate(d2):
        return None
    res = evaluate(d2)
    return res["equity_irr"] if res["equity_irr_valid"] else None


def sweep_values(field: str, cur: float, n: int = 5, span: float = 2.0) -> np.ndarray:
    vals = np.linspace(cur - span, cur + span, n)
    if field == "exit_cap":
        vals = np.clip(vals, 0.25, None)        # ExitCap > 0
    elif field == "vacancy":
        vals = np.clip(vals, 0.0, 100.0)
    elif field == "rate":
        vals = np.clip(vals, 0.0, None)
    return vals


# --------------------------------------------------------------------------- #
# 사이드바: 투자기준 패널 + 딜 관리 + 편집 폼
# --------------------------------------------------------------------------- #
def sidebar():
    st.sidebar.header("📐 투자 기준 (전 딜 공통)")
    crit = st.session_state.criteria
    crit["hurdle"] = st.sidebar.number_input(
        "목표 Equity IRR / 허들 (%)", value=float(crit["hurdle"]), step=0.5, format="%.2f")
    crit["min_dscr"] = st.sidebar.number_input(
        "최소 DSCR (배)", value=float(crit["min_dscr"]), step=0.05, format="%.2f")
    crit["max_ltv"] = st.sidebar.number_input(
        "최대 LTV (%)", value=float(crit["max_ltv"]), step=1.0, format="%.1f")

    st.sidebar.divider()
    st.sidebar.header("🗂️ 딜 관리")

    deals = st.session_state.deals
    names = [f"{i + 1}. {d['name']}" for i, d in enumerate(deals)]
    st.session_state.sel = st.sidebar.selectbox(
        "편집할 딜", range(len(deals)),
        format_func=lambda i: names[i],
        index=min(st.session_state.sel, len(deals) - 1),
    )

    c1, c2, c3 = st.sidebar.columns(3)
    if c1.button("➕ 추가", use_container_width=True):
        deals.append(blank_deal())
        st.session_state.sel = len(deals) - 1
        st.rerun()
    if c2.button("⧉ 복제", use_container_width=True):
        dup = dict(deals[st.session_state.sel])
        dup["name"] += " (복제)"
        deals.insert(st.session_state.sel + 1, dup)
        st.session_state.sel += 1
        st.rerun()
    if c3.button("🗑 삭제", use_container_width=True, disabled=len(deals) <= 1):
        deals.pop(st.session_state.sel)
        st.session_state.sel = max(0, st.session_state.sel - 1)
        st.rerun()

    st.sidebar.divider()
    deal_form()


def deal_form():
    deals = st.session_state.deals
    sel = st.session_state.sel
    dd = deals[sel]
    st.sidebar.subheader(f"✏️ 편집: {dd['name']}")

    with st.sidebar.form("deal_form"):
        name = st.text_input("딜 이름", value=dd["name"])
        price = st.number_input("매입가 (원)", value=float(dd["price"]), step=1e8, format="%.0f")
        acq = st.number_input("취득부대비용 (%)", value=float(dd["acq_cost_pct"]), step=0.5, format="%.2f",
                              help="한국 상업용 통상 ~5~6% (취득세 4.6% + 중개·법무·실사·취득수수료).")

        st.markdown("**수입**")
        gpr1 = st.number_input("1년차 GPR (원/년)", value=float(dd["gpr1"]), step=1e7, format="%.0f")
        other = st.number_input("1년차 기타수입 (원/년)", value=float(dd["other_income"]), step=1e7, format="%.0f")
        vacancy = st.number_input("공실·대손율 (%)", value=float(dd["vacancy"]), step=0.5, format="%.2f")
        rent_g = st.number_input("임대료 성장률 g (%)", value=float(dd["rent_growth"]), step=0.5, format="%.2f")

        st.markdown("**운영비용 (OpEx)**")
        opex_basis = st.selectbox(
            "OpEx 방식", ["pct", "absolute"],
            index=0 if dd["opex_basis"] == "pct" else 1,
            format_func=lambda x: "EGI 대비 %" if x == "pct" else "절대금액(원/년)")
        opex1 = st.number_input(
            "OpEx (pct: %, absolute: 원/년)", value=float(dd["opex1"]),
            step=1.0 if dd["opex_basis"] == "pct" else 1e7, format="%.2f")
        opex_g = st.number_input("비용상승률 (%, absolute 전용)", value=float(dd["opex_growth"]), step=0.5, format="%.2f")
        reserve = st.number_input(
            "자본리저브 CapEx/TI/LC (EGI 대비 %)", value=float(dd.get("capex_reserve_pct", 0.0)),
            step=0.5, format="%.2f",
            help="NOI는 그대로 두고 자기자본 현금흐름에서만 차감 — IRR/CoC를 현실화. 0이면 미반영.")

        st.markdown("**보유 / Exit**")
        hold = st.number_input("보유기간 n (년)", value=int(dd["hold_years"]), step=1, min_value=1)
        exit_cap = st.number_input("Exit Cap (%)", value=float(dd["exit_cap"]), step=0.25, format="%.2f")
        sell = st.number_input("매각비용 (%)", value=float(dd["selling_cost_pct"]), step=0.5, format="%.2f")

        st.markdown("**대출 (Financing)**")
        ltv = st.number_input("LTV (%)", value=float(dd["ltv"]), step=1.0, format="%.2f")
        rate = st.number_input("대출금리 (%)", value=float(dd["rate"]), step=0.25, format="%.2f")
        amort = st.selectbox(
            "상환방식", ["IO", "amortizing"],
            index=0 if dd["amort_type"] == "IO" else 1,
            format_func=lambda x: "이자만(IO/만기일시)" if x == "IO" else "원리금균등(연 단위)")
        term = st.number_input("상환기간 (년, amortizing 시)", value=int(dd["amort_term_years"]), step=1, min_value=1)

        if st.form_submit_button("💾 저장", use_container_width=True):
            deals[sel] = dict(
                name=name, price=price, acq_cost_pct=acq, gpr1=gpr1,
                other_income=other, vacancy=vacancy, opex_basis=opex_basis,
                opex1=opex1, opex_growth=opex_g, rent_growth=rent_g,
                hold_years=int(hold), exit_cap=exit_cap, selling_cost_pct=sell,
                ltv=ltv, rate=rate, amort_type=amort, amort_term_years=int(term),
                capex_reserve_pct=reserve,
            )
            st.rerun()


# --------------------------------------------------------------------------- #
# 평가 + 랭킹
# --------------------------------------------------------------------------- #
def evaluate_all() -> list[dict]:
    crit = st.session_state.criteria
    out = []
    for idx, dd in enumerate(st.session_state.deals):
        d = build_deal(dd, crit)
        errs = validate(d)
        res = None if errs else evaluate(d)
        out.append({"idx": idx, "name": dd["name"], "deal": d, "errs": errs, "res": res})
    return out


def rank(evals: list[dict]):
    def key(e):
        ok = (not e["errs"]) and e["res"]["equity_irr_valid"]
        return (0, -e["res"]["equity_irr"]) if ok else (1, 0.0)

    ordered = sorted(evals, key=key)
    r = 0
    for e in ordered:
        computable = (not e["errs"]) and e["res"]["equity_irr_valid"]
        if computable:
            r += 1
            e["rank"] = r
        else:
            e["rank"] = None
    return ordered


# --------------------------------------------------------------------------- #
# 메인: 비교 테이블
# --------------------------------------------------------------------------- #
def comparison_table(ordered: list[dict]):
    st.subheader("📊 딜 비교 · 스크리닝 · 랭킹")
    rows = []
    for e in ordered:
        if e["errs"]:
            rows.append({
                "순위": "—", "딜": e["name"], "Going-in Cap": "N/A", "캡스프레드": "N/A",
                "Project IRR": "N/A", "Equity IRR": "N/A", "Equity NPV": "N/A",
                "최소 DSCR": "N/A", "Debt Yield": "N/A", "CoC(평균)": "N/A", "Eq.Multiple": "N/A",
                "판정": "❌ 입력오류", "사유": " / ".join(e["errs"]),
            })
            continue
        res = e["res"]
        eq_valid = res["equity_irr_valid"]
        passes = res["passes"]
        verdict = "✅ PASS" if passes else "❌ FAIL"
        reason = "—" if passes else (" / ".join(res["fail_reasons"]) or "—")
        rows.append({
            "순위": e["rank"] if e["rank"] is not None else "—",
            "딜": e["name"],
            "Going-in Cap": fmt_pct(res["going_in_cap"]),
            "캡스프레드": fmt_spread(res["cap_spread"]),
            "Project IRR": fmt_pct(res["project_irr"]) if res["project_irr_valid"] else "산출불가",
            "Equity IRR": fmt_pct(res["equity_irr"]) if eq_valid else "산출불가",
            "Equity NPV": fmt_won(res["equity_npv"]),
            "최소 DSCR": fmt_dscr(res["dscr_min"]),
            "Debt Yield": fmt_pct(res["debt_yield"]),
            "CoC(평균)": fmt_pct(res["cash_on_cash_avg"]),
            "Eq.Multiple": fmt_mult(res["equity_multiple"]),
            "판정": verdict,
            "사유": reason,
        })
    df = pd.DataFrame(rows)
    st.dataframe(df, hide_index=True, use_container_width=True)
    st.caption("랭킹: irrValid 유효 딜 우선 → Equity IRR 내림차순. 산출 불가/입력오류 딜은 순위 제외. "
               "캡스프레드(+)=Exit Cap>진입 캡=가치 하락 위험. CoC는 보유기간 평균 운영 현금이익률.")


# --------------------------------------------------------------------------- #
# 드릴다운
# --------------------------------------------------------------------------- #
def drilldown(evals: list[dict]):
    st.divider()
    st.subheader("🔍 드릴다운")
    names = [e["name"] for e in evals]
    pick = st.selectbox("딜 선택", range(len(evals)), format_func=lambda i: names[i],
                        index=min(st.session_state.sel, len(evals) - 1))
    e = evals[pick]
    d = e["deal"]

    if e["errs"]:
        st.error("입력 검증 실패 — 계산을 실행하지 않습니다.")
        for msg in e["errs"]:
            st.write(f"• {msg}")
        return

    res = e["res"]

    # 핵심 지표 카드
    c = st.columns(4)
    c[0].metric("Going-in Cap", fmt_pct(res["going_in_cap"]))
    c[1].metric("Equity IRR", fmt_pct(res["equity_irr"]) if res["equity_irr_valid"] else "산출불가")
    c[2].metric("Equity NPV", fmt_won(res["equity_npv"]))
    c[3].metric("Equity Multiple", fmt_mult(res["equity_multiple"]))
    c = st.columns(4)
    c[0].metric("Project IRR", fmt_pct(res["project_irr"]) if res["project_irr_valid"] else "산출불가")
    c[1].metric("최소 DSCR", fmt_dscr(res["dscr_min"]))
    c[2].metric("CoC (1년차)", fmt_pct(res["cash_on_cash"]))
    c[3].metric("CoC (보유기간 평균)", fmt_pct(res["cash_on_cash_avg"]))
    c = st.columns(4)
    c[0].metric("1년차 DSCR", fmt_dscr(res["dscr_y1"]))
    c[1].metric("캡스프레드 (Exit−진입)", fmt_spread(res["cap_spread"]),
                help="+면 Exit Cap이 진입 캡보다 높음 = 매각 시 가치 하락(캡 확장)을 가정 = 보수적/위험.")

    # 대출 사이징 제약(표시 전용) + Debt Yield
    sc = sizing_constraints(d)
    c = st.columns(4)
    c[0].metric("Debt Yield (NOI₁/Loan)", fmt_pct(res["debt_yield"]))
    c[1].metric("LTV 한도 대출액", fmt_won(sc["loan_cap_ltv"]))
    c[2].metric("DSCR 한도 대출액",
                "제약 없음" if math.isinf(sc["loan_cap_dscr"]) else fmt_won(sc["loan_cap_dscr"]))
    c[3].metric("Binding 제약", f"{sc['binding']}",
                delta=f"≈ {fmt_pct(sc['implied_max_ltv'], 1)} LTV", delta_color="off")
    st.caption("ℹ️ 사이징은 *적용하지 않고* 표시만 합니다. 실제 대출 = Price×LTV. "
               "실무 대출은 min(LTV한도, DSCR한도)로 정해지며, 위 'Binding 제약'이 어느 쪽이 "
               "묶는지를 보여줍니다(DSCR 한도는 보수적으로 1년차 NOI 기준).")

    if res["passes"]:
        st.success("✅ PASS — 모든 투자 기준 충족")
    else:
        st.warning("❌ FAIL — " + (" / ".join(res["fail_reasons"]) or "사유 없음"))

    tab1, tab2, tab3, tab4, tab5 = st.tabs(
        ["NOI 빌드업", "현금흐름", "1D 민감도", "2D 민감도", "최대 매입가·손익분기"])

    with tab1:
        noi_buildup_tab(d, res)
    with tab2:
        cashflow_tab(d, res)
    with tab3:
        sens_1d_tab(d)
    with tab4:
        sens_2d_tab(d)
    with tab5:
        underwriting_tab(d)


def noi_buildup_tab(d: Deal, res: dict):
    st.markdown("**연도별 NOI 빌드업** (t = 1 … n+1, 마지막 행이 Exit 환원용 포워드 NOI)")
    rows = []
    for r in noi_breakdown(d):
        rows.append({
            "연차": int(r["year"]),
            "GPR": fmt_won(r["gpr"]),
            "기타수입": fmt_won(r["other"]),
            "EGI": fmt_won(r["egi"]),
            "OpEx": fmt_won(r["opex"]),
            "NOI": fmt_won(r["noi"]),
        })
    df = pd.DataFrame(rows)
    # 포워드 NOI(마지막 행) 강조
    st.dataframe(df, hide_index=True, use_container_width=True)
    cc = st.columns(2)
    cc[0].metric("Exit 순매각대금 (NOI₍ₙ₊₁₎ ÷ ExitCap, 매각비용 차감)", fmt_won(res["net_sale"]))
    cc[1].metric("포워드 NOI (n+1년차)", fmt_won(noi_breakdown(d)[d.hold_years]["noi"]))


def cashflow_tab(d: Deal, res: dict):
    st.markdown("**연도별 현금흐름** (0년차 = 초기 투자, n년차 = 운영 + Exit)")
    n = d.hold_years
    years = list(range(0, n + 1))
    ucf = res["unlevered_cf"]
    lcf = res["levered_cf"]
    df = pd.DataFrame({"무차입 CF": ucf, "차입 CF (Equity)": lcf}, index=years)
    df.index.name = "연차"
    st.bar_chart(df)

    tbl = pd.DataFrame({
        "연차": years,
        "무차입 CF": [fmt_won(x) for x in ucf],
        "부채상환(DS)": ["—"] + [fmt_won(x) for x in res["debt_service"]],
        "자본리저브": ["—"] + [fmt_won(x) for x in res["reserve"]],
        "차입 CF": [fmt_won(x) for x in lcf],
    })
    st.dataframe(tbl, hide_index=True, use_container_width=True)
    st.caption(f"초기 자기자본 Equity₀ = {fmt_won(res['equity0'])} · "
               f"Exit 대출잔액 상환 = {fmt_won(res['loan_payoff'])} · "
               f"자본리저브(CapEx/TI/LC)는 NOI에서 빼지 않고 현금흐름에서만 차감.")


def sens_1d_tab(d: Deal):
    st.markdown("**1D 민감도** — 단일 변수 변화에 따른 Equity IRR")
    label = st.selectbox("변수", list(SENS_FIELDS.keys()), key="s1d_var")
    field = SENS_FIELDS[label]
    cur = getattr(d, field)
    vals = sweep_values(field, cur, n=9, span=4.0)

    irrs = [eval_equity_irr(d, **{field: float(v)}) for v in vals]
    chart_df = pd.DataFrame(
        {"Equity IRR (%)": [np.nan if x is None else x * 100 for x in irrs]},
        index=[round(float(v), 2) for v in vals],
    )
    chart_df.index.name = f"{label} (%)"
    st.line_chart(chart_df)

    tbl = pd.DataFrame({
        f"{label} (%)": [f"{float(v):.2f}" for v in vals],
        "Equity IRR": [fmt_pct(x) if x is not None else "산출불가" for x in irrs],
    })
    st.dataframe(tbl, hide_index=True, use_container_width=True)
    st.caption(f"기준값(현재 딜): {label} = {cur:.2f}%")


def sens_2d_tab(d: Deal):
    st.markdown("**2D 민감도 히트맵** — 두 변수 조합별 Equity IRR (%). 산출 불가 셀은 회색.")
    c1, c2 = st.columns(2)
    xlabel = c1.selectbox("X 변수", list(SENS_FIELDS.keys()), index=0, key="s2d_x")
    ylabel = c2.selectbox("Y 변수", list(SENS_FIELDS.keys()), index=3, key="s2d_y")
    fx, fy = SENS_FIELDS[xlabel], SENS_FIELDS[ylabel]
    if fx == fy:
        st.info("X·Y 변수를 서로 다르게 선택하세요.")
        return

    X = sweep_values(fx, getattr(d, fx), n=5, span=2.0)
    Y = sweep_values(fy, getattr(d, fy), n=5, span=2.0)
    Z = np.full((len(Y), len(X)), np.nan)
    for iy, yv in enumerate(Y):
        for ix, xv in enumerate(X):
            v = eval_equity_irr(d, **{fx: float(xv), fy: float(yv)})
            if v is not None:
                Z[iy, ix] = v * 100.0

    fig, ax = plt.subplots(figsize=(7, 5))
    cmap = plt.cm.RdYlGn.copy()
    cmap.set_bad(color="lightgray")
    masked = np.ma.masked_invalid(Z)
    im = ax.imshow(masked, cmap=cmap, aspect="auto", origin="lower")
    ax.set_xticks(range(len(X)))
    ax.set_xticklabels([f"{v:.2f}" for v in X])
    ax.set_yticks(range(len(Y)))
    ax.set_yticklabels([f"{v:.2f}" for v in Y])
    ax.set_xlabel(SENS_LABEL_EN[fx])
    ax.set_ylabel(SENS_LABEL_EN[fy])
    ax.set_title("Equity IRR (%) Sensitivity")
    for iy in range(len(Y)):
        for ix in range(len(X)):
            if math.isnan(Z[iy, ix]):
                ax.text(ix, iy, "N/A", ha="center", va="center", fontsize=8, color="dimgray")
            else:
                ax.text(ix, iy, f"{Z[iy, ix]:.1f}", ha="center", va="center", fontsize=8)
    fig.colorbar(im, ax=ax, label="Equity IRR (%)")
    fig.tight_layout()
    st.pyplot(fig)
    plt.close(fig)


def underwriting_tab(d: Deal):
    crit = st.session_state.criteria
    st.markdown(f"**투자 기준:** 목표 Equity IRR(허들) {crit['hurdle']:.2f}% · 최소 DSCR {crit['min_dscr']:.2f} · 최대 LTV {crit['max_ltv']:.1f}%")

    # ── 최대 매입가 역산 ──
    st.markdown("#### 💰 최대 매입가 (목표를 만족하는 walk-away price)")
    mp = max_acquisition_price(d)

    def price_str(p):
        if math.isinf(p):
            return "제약 없음(∞)"
        if p <= 0:
            return "달성 불가"
        return fmt_won(p)

    c = st.columns(3)
    c[0].metric("목표 IRR 기준 최대가", price_str(mp["price_irr"]))
    c[1].metric("최소 DSCR 기준 최대가", price_str(mp["price_dscr"]))
    c[2].metric(f"종합 최대 입찰가 ({mp['binding']} 제약)", price_str(mp["max_price"]))

    cur = d.price
    maxp = mp["max_price"]
    if maxp > 0 and not math.isinf(maxp):
        gap = (cur - maxp) / maxp
        implied_cap = noi_breakdown(d)[0]["noi"] / maxp  # 최대가에서의 going-in cap
        if cur > maxp:
            st.error(f"⚠️ 현재 매입가 {fmt_won(cur)}는 최대 입찰가 {fmt_won(maxp)}를 "
                     f"**{gap*100:.1f}% 초과**(과지불). 목표 미달.")
        else:
            st.success(f"✅ 현재 매입가 {fmt_won(cur)}는 최대 입찰가 {fmt_won(maxp)} 이내 "
                       f"(여유 {-gap*100:.1f}%).")
        st.caption(f"최대 입찰가에서의 Going-in Cap ≈ {fmt_pct(implied_cap)} · "
                   f"{mp['binding']} 제약이 가격 상한을 결정.")
    elif maxp <= 0:
        st.error("어떤 가격에도 목표 IRR을 달성할 수 없는 현금흐름이다(가격 무관 구조적 미달).")

    st.divider()

    # ── 손익분기 ──
    st.markdown("#### ⚖️ 손익분기 (Equity IRR = 허들이 되는 임계값)")
    fields = [
        ("Exit Cap", "exit_cap", d.exit_cap, "상한", "캡이 이보다 오르면(가치↓) 탈락"),
        ("공실률", "vacancy", d.vacancy, "상한", "공실이 이보다 커지면 탈락"),
        ("대출금리", "rate", d.rate, "상한", "금리가 이보다 오르면 탈락"),
        ("임대료 성장률", "rent_growth", d.rent_growth, "하한", "성장이 이보다 낮으면 탈락"),
    ]
    rows = []
    for label, fld, cur_v, kind, note in fields:
        be = break_even(d, fld)
        if be is None:
            be_s, buf_s = "범위 내 없음", "—"
        else:
            be_s = f"{be:.2f}%"
            # 여유: 상한이면 (임계−현재), 하한이면 (현재−임계). 양수=아직 통과 여지
            buf = (be - cur_v) if kind == "상한" else (cur_v - be)
            buf_s = f"{buf:+.2f}%p"
        rows.append({
            "변수": label, "현재값": f"{cur_v:.2f}%", "손익분기": be_s,
            "유형": kind, "여유(buffer)": buf_s, "의미": note,
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    st.caption("여유(buffer) 양수 = 현재 가정이 손익분기 대비 통과 쪽에 있고 그만큼 여력이 있다는 뜻. "
               "음수면 이미 그 변수 기준으로 허들 미달.")


# --------------------------------------------------------------------------- #
# 엔트리
# --------------------------------------------------------------------------- #
def main():
    init_state()
    st.title("🏢 딜 스크리너 (Deal Screener)")
    st.caption("여러 부동산 투자 후보를 비교·스크리닝하고 Equity IRR 기준으로 랭킹합니다. "
               "모든 % 입력은 퍼센트포인트(5 = 5%) 규약.")

    sidebar()

    evals = evaluate_all()
    ordered = rank(evals)
    comparison_table(ordered)
    drilldown(evals)

    st.divider()
    with st.expander("📋 모델 가정 및 한계 (disclosure)"):
        st.markdown(
            "- **대상**: *안정화된 수익형 자산*의 1차 스크리닝. Exit는 수익환원법(포워드 NOI ÷ Exit Cap). "
            "개발·분양·보조금·세후 등은 범위 밖(스크리너는 운영 중인 수익자산을 본다).\n"
            "- **자본리저브(CapEx/TI/LC)**: 딜별 `자본리저브 %`로 반영. NOI·Cap·DSCR·매각가(TV)는 "
            "렌더 관행대로 NOI 기준 그대로 두고, **자기자본 현금흐름에서만 차감**해 IRR/CoC를 현실화. "
            "0으로 두면 미반영(리저브 전 수치).\n"
            "- **연 단위 모델**: 현금흐름·대출 상환을 연 단위로 계산(월 단위 분할상환은 범위 밖). "
            "데모 딜은 전부 IO라 영향이 사실상 없으나, 원리금균등 사용 시 월 모델 대비 미세 오차 존재.\n"
            "- **DSCR은 게이트(심사)이며 대출 사이징에 적용하지 않음**: 실제 대출 = Price×LTV. "
            "드릴다운의 'LTV 한도 / DSCR 한도 / Binding 제약'은 어느 제약이 묶는지를 보여주는 **표시 전용** 정보.\n"
            "- **세전 기준**: 세금·감가상각 미반영(세후 IRR 범위 밖). 취득금융수수료는 Equity₀에 미포함."
        )


if __name__ == "__main__":
    main()
