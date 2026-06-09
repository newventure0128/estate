"""
Deal Screener — 계산 엔진 (engine.py)

순수 Python 함수만 포함하며 Streamlit/UI에 의존하지 않는다.
명세서 v4 §5(계산 로직) / §7(검증·엣지케이스)을 그대로 구현한다.

규약:
- 모든 % 입력은 '퍼센트포인트'로 받는다 (예: 5 == 5%). 내부에서 _pct()로 ÷100.
- IRR 등은 소수(decimal)로 반환한다 (0.11 == 11%). 표시 단계에서만 ×100·반올림.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import List, Tuple, Dict, Any


# --------------------------------------------------------------------------- #
# 데이터 모델 (§6)
# --------------------------------------------------------------------------- #
@dataclass
class Deal:
    name: str
    price: float            # 매입가 (원)
    acq_cost_pct: float     # 취득부대비용 (%pt)
    gpr1: float             # 1년차 잠재총임대수입 (원/년)
    other_income: float     # 1년차 기타수입 (원/년) — 이후 g로 성장
    vacancy: float          # 공실·대손율 (%pt)
    opex_basis: str         # 'pct' | 'absolute'
    opex1: float            # absolute: 원/년 | pct: EGI 대비 %pt
    opex_growth: float      # 비용상승률 (%pt) — absolute 모드 전용
    rent_growth: float      # 임대료 성장률 g (%pt)
    hold_years: int         # 보유기간 n (정수)
    exit_cap: float         # 청산 Cap Rate (%pt, >0)
    selling_cost_pct: float # 매각비용 (%pt)
    ltv: float              # 대출비율 (%pt)
    rate: float             # 대출금리 (%pt)
    amort_type: str         # 'IO' | 'amortizing'
    amort_term_years: int   # 원리금균등 상환기간 (정수)
    hurdle_irr: float       # 목표 Equity IRR = NPV 할인율 (%pt)
    min_dscr: float         # 최소 DSCR (배)
    max_ltv: float          # LTV 상한 (%pt)
    capex_reserve_pct: float = 0.0  # 자본리저브(CapEx/TI/LC), EGI 대비 %pt.
    #                                 현금흐름에서만 차감 — NOI·Cap·DSCR·TV는 불변(렌더 관행).


def _pct(x: float) -> float:
    """퍼센트포인트 → 소수."""
    return x / 100.0


# --------------------------------------------------------------------------- #
# 입력 검증 (§7)
# --------------------------------------------------------------------------- #
def validate(d: Deal) -> List[str]:
    """위반 사유 리스트 반환. 빈 리스트면 통과."""
    errs: List[str] = []

    if d.price <= 0:
        errs.append("매입가는 0보다 커야 합니다.")
    if d.exit_cap <= 0:
        errs.append("Exit Cap은 0보다 커야 합니다.")
    if not isinstance(d.hold_years, int) or d.hold_years < 1:
        errs.append("보유기간은 1년 이상의 정수여야 합니다.")
    if d.hurdle_irr <= -100:
        errs.append("목표 IRR(hurdle)은 -100%보다 커야 합니다.")

    # 범위 (%pt 0~100)
    for val, name in [
        (d.vacancy, "공실률"), (d.ltv, "LTV"),
        (d.max_ltv, "최대 LTV"), (d.selling_cost_pct, "매각비용"),
    ]:
        if val < 0 or val > 100:
            errs.append(f"{name}은(는) 0~100% 범위여야 합니다.")

    # 음수 차단
    for val, name in [
        (d.gpr1, "GPR"), (d.other_income, "기타수입"),
        (d.acq_cost_pct, "취득부대비용"), (d.rate, "대출금리"),
        (d.capex_reserve_pct, "자본리저브"), (d.opex1, "운영비용(OpEx)"),
    ]:
        if val < 0:
            errs.append(f"{name}은(는) 음수일 수 없습니다.")
    if d.min_dscr < 0:
        errs.append("최소 DSCR은 음수일 수 없습니다.")

    # 성장률 하한: (1+g) <= 0 이면 기하급수가 부호 진동 → 무의미한 NOI 빌드업 차단.
    if d.rent_growth <= -100:
        errs.append("임대료 성장률은 -100%보다 커야 합니다.")
    if d.opex_basis == "absolute" and d.opex_growth <= -100:
        errs.append("비용상승률은 -100%보다 커야 합니다.")

    # 자기자본 > 0 직접 강제 (= Loan < Price×(1+acq%))  ⟺  LTV < 1 + acq%
    loan = d.price * _pct(d.ltv)
    equity0 = d.price * (1 + _pct(d.acq_cost_pct)) - loan
    if equity0 <= 0:
        errs.append("자기자본이 0 이하입니다. LTV < (1 + 취득부대비용%) 이어야 합니다.")

    if d.amort_type == "amortizing":
        if not isinstance(d.amort_term_years, int) or d.amort_term_years < 1:
            errs.append("원리금균등 상환기간은 1년 이상의 정수여야 합니다.")

    if d.amort_type not in ("IO", "amortizing"):
        errs.append("상환방식은 'IO' 또는 'amortizing'이어야 합니다.")
    if d.opex_basis not in ("pct", "absolute"):
        errs.append("OpEx 방식은 'pct' 또는 'absolute'여야 합니다.")

    return errs


# --------------------------------------------------------------------------- #
# NOI 빌드업 (t = 1 .. n+1) — Exit 포워드 NOI를 명시 계산
# --------------------------------------------------------------------------- #
def noi_breakdown(d: Deal) -> List[Dict[str, float]]:
    """t = 1 .. n+1 각 연차의 NOI 구성요소(GPR·기타·EGI·OpEx·NOI).

    UI의 NOI 빌드업 표가 계산 로직을 중복하지 않도록 엔진이 단일 출처로 제공한다.
    """
    g = _pct(d.rent_growth)
    v = _pct(d.vacancy)
    i = _pct(d.opex_growth)
    rows: List[Dict[str, float]] = []
    for t in range(1, d.hold_years + 2):  # 1 .. n+1
        gpr = d.gpr1 * (1 + g) ** (t - 1)
        other = d.other_income * (1 + g) ** (t - 1)
        egi = gpr * (1 - v) + other
        if d.opex_basis == "pct":
            opex = egi * _pct(d.opex1)          # EGI 추종 (opex_growth 무시)
        else:
            opex = d.opex1 * (1 + i) ** (t - 1)  # 절대금액 성장
        rows.append({
            "year": t, "gpr": gpr, "other": other,
            "egi": egi, "opex": opex, "noi": egi - opex,
        })
    return rows


def project_noi(d: Deal) -> List[float]:
    """길이 n+1 리스트. index 0 = 1년차 NOI ... index n = (n+1)년차 NOI."""
    return [row["noi"] for row in noi_breakdown(d)]


def terminal_value(d: Deal, noi: List[float]) -> float:
    """순매각대금 = (NOI_(n+1) / ExitCap) × (1 - 매각비용%)."""
    noi_n1 = noi[d.hold_years]            # (n+1)년차
    tv = noi_n1 / _pct(d.exit_cap)
    return tv * (1 - _pct(d.selling_cost_pct))


# --------------------------------------------------------------------------- #
# 자본리저브 (CapEx/TI/LC) — 현금흐름에서만 차감. NOI/Cap/DSCR/TV는 불변.
# --------------------------------------------------------------------------- #
def reserve_schedule(d: Deal, breakdown: List[Dict[str, float]] | None = None) -> List[float]:
    """t = 1 .. n 각 운영연도의 자본리저브 = EGI_t × capexReserve%."""
    if breakdown is None:
        breakdown = noi_breakdown(d)
    p = _pct(d.capex_reserve_pct)
    return [breakdown[t]["egi"] * p for t in range(d.hold_years)]


# --------------------------------------------------------------------------- #
# 현금흐름
# --------------------------------------------------------------------------- #
def unlevered_cf(d: Deal, noi: List[float], net_sale: float,
                 reserve: List[float] | None = None) -> List[float]:
    n = d.hold_years
    if reserve is None:
        reserve = [0.0] * n
    cf = [-d.price * (1 + _pct(d.acq_cost_pct))]   # CF_0
    for t in range(1, n):                          # 1 .. n-1
        cf.append(noi[t - 1] - reserve[t - 1])
    cf.append(noi[n - 1] - reserve[n - 1] + net_sale)  # n년차 + Exit
    return cf


def debt_schedule(d: Deal) -> Tuple[List[float], float]:
    """(연도별 부채상환액[길이 n], Exit 시점 잔액상환) 반환."""
    n = d.hold_years
    loan = d.price * _pct(d.ltv)
    r = _pct(d.rate)

    if loan == 0:
        return [0.0] * n, 0.0

    if d.amort_type == "IO":
        return [loan * r] * n, loan

    # 원리금균등 (연 단위)
    N = d.amort_term_years
    payment = loan / N if r == 0 else loan * r / (1 - (1 + r) ** (-N))

    ds: List[float] = []
    balance = loan
    for t in range(1, n + 1):
        if t <= N and balance > 1e-9:
            interest = balance * r
            principal = payment - interest
            ds.append(payment)
            balance -= principal
        else:
            ds.append(0.0)        # 만기 전 완납(N<n) 이후
            balance = 0.0
    payoff = max(balance, 0.0)    # 부동소수 음수 클램프
    return ds, payoff


def levered_cf(d: Deal, noi: List[float], net_sale: float,
               ds: List[float], payoff: float,
               reserve: List[float] | None = None) -> Tuple[List[float], float]:
    n = d.hold_years
    if reserve is None:
        reserve = [0.0] * n
    loan = d.price * _pct(d.ltv)
    equity0 = d.price * (1 + _pct(d.acq_cost_pct)) - loan
    cf = [-equity0]                                # CF_0
    for t in range(1, n):                          # 1 .. n-1
        cf.append(noi[t - 1] - ds[t - 1] - reserve[t - 1])
    cf.append(noi[n - 1] - ds[n - 1] + net_sale - payoff - reserve[n - 1])  # n년차
    return cf, equity0


# --------------------------------------------------------------------------- #
# IRR / NPV
# --------------------------------------------------------------------------- #
def npv(rate: float, cfs: List[float]) -> float:
    return sum(cf / (1 + rate) ** t for t, cf in enumerate(cfs))


def _sign_changes(cfs: List[float]) -> int:
    signs = [(1 if x > 0 else -1) for x in cfs if x != 0]
    return sum(1 for a, b in zip(signs, signs[1:]) if a != b)


def irr(cfs: List[float]) -> Tuple[float, bool]:
    """(IRR소수, 유효여부). 다중 부호변화·해없음·미수렴 시 (nan, False)."""
    if _sign_changes(cfs) != 1:
        return math.nan, False

    LO, HI = -0.99, 100.0

    # 1) Newton-Raphson
    rate = 0.10
    for _ in range(200):
        f = npv(rate, cfs)
        deriv = sum(-t * cf / (1 + rate) ** (t + 1) for t, cf in enumerate(cfs))
        if deriv == 0:
            break
        new = rate - f / deriv
        if not math.isfinite(new):
            break
        if abs(new - rate) < 1e-9:
            if LO < new < HI:
                return new, True
            break
        rate = new

    # 2) bisection 폴백
    flo, fhi = npv(LO, cfs), npv(HI, cfs)
    if flo * fhi > 0:
        return math.nan, False
    lo, hi = LO, HI
    for _ in range(200):
        mid = (lo + hi) / 2
        fmid = npv(mid, cfs)
        if abs(fmid) < 1e-9:
            return mid, True
        if flo * fmid < 0:
            hi = mid
        else:
            lo, flo = mid, fmid
    return (lo + hi) / 2, True


# --------------------------------------------------------------------------- #
# 대출 사이징 제약 (표시 전용 — 실제 대출은 여전히 Price×LTV)
# --------------------------------------------------------------------------- #
def sizing_constraints(d: Deal) -> Dict[str, Any]:
    """LTV 한도·DSCR 한도 대출액과 binding 제약을 산출(읽기전용).

    실무에서 대출은 min(LTV한도, DSCR한도)로 사이징된다. 본 스크리너는 사이징을
    적용하지 않고 '어느 제약이 binding인지'만 보여준다(현금흐름 엔진 무변경).
    DSCR 한도는 보수적으로 1년차 NOI 기준(최소 DSCR이 통상 1년차에 발생).
    """
    noi1 = project_noi(d)[0]
    r = _pct(d.rate)
    loan_cap_ltv = d.price * _pct(d.max_ltv)

    if d.min_dscr <= 0:
        loan_cap_dscr = math.inf
    elif d.amort_type == "IO":
        loan_cap_dscr = math.inf if r == 0 else noi1 / (d.min_dscr * r)
    else:
        N = d.amort_term_years
        factor = (1.0 / N) if r == 0 else r / (1 - (1 + r) ** (-N))  # 연금계수
        loan_cap_dscr = noi1 / (d.min_dscr * factor)

    binding_loan = min(loan_cap_ltv, loan_cap_dscr)
    binding = "LTV" if loan_cap_ltv <= loan_cap_dscr else "DSCR"
    return {
        "loan_cap_ltv": loan_cap_ltv,
        "loan_cap_dscr": loan_cap_dscr,
        "binding_loan": binding_loan,
        "binding": binding,
        "implied_max_ltv": binding_loan / d.price,
    }


# --------------------------------------------------------------------------- #
# 종합 지표 (§5 핵심 비율 + §3 스크리닝)
# --------------------------------------------------------------------------- #
def evaluate(d: Deal) -> Dict[str, Any]:
    """검증 통과를 전제로 전체 지표를 계산해 dict 반환."""
    breakdown = noi_breakdown(d)
    noi = [row["noi"] for row in breakdown]
    reserve = reserve_schedule(d, breakdown)
    net_sale = terminal_value(d, noi)

    ucf = unlevered_cf(d, noi, net_sale, reserve)
    proj_irr, proj_valid = irr(ucf)

    ds, payoff = debt_schedule(d)
    lcf, equity0 = levered_cf(d, noi, net_sale, ds, payoff, reserve)
    eq_irr, eq_valid = irr(lcf)

    hurdle = _pct(d.hurdle_irr)
    eq_npv = npv(hurdle, lcf)

    going_in_cap = noi[0] / d.price
    cap_spread = _pct(d.exit_cap) - going_in_cap   # +면 캡 확장(가치 하락 위험)

    dscrs = [math.inf if ds[t] == 0 else noi[t] / ds[t] for t in range(d.hold_years)]
    min_dscr = min(dscrs)
    dscr_y1 = dscrs[0]

    # '운영' 차입 현금흐름(Exit 제외) = NOI − DS − Reserve, 연도별.
    op_cf = [noi[t] - ds[t] - reserve[t] for t in range(d.hold_years)]
    # 1년차 CoC: 보유 1년(n=1)일 때 lcf[1]에 매각대금이 섞이는 오염 방지 위해 운영 CF로 명시.
    coc = op_cf[0] / equity0 if equity0 != 0 else math.nan
    # 평균 CoC: 보유기간 운영 현금흐름 평균 / 자기자본 (Exit 제외 — 순수 운영 현금이익률).
    coc_avg = (sum(op_cf) / d.hold_years) / equity0 if equity0 != 0 else math.nan

    loan = d.price * _pct(d.ltv)
    debt_yield = math.inf if loan == 0 else noi[0] / loan  # 렌더 핵심지표 NOI₁/Loan

    pos = sum(x for x in lcf if x > 0)
    neg = sum(x for x in lcf if x < 0)
    eq_mult = pos / abs(neg) if neg != 0 else math.nan    # 캐피탈콜 방어식

    # PASS 판정 (§3.1): IRR 유효 + 허들 + DSCR + LTV 상한
    passes = (
        eq_valid
        and eq_irr >= hurdle
        and min_dscr >= d.min_dscr
        and d.ltv <= d.max_ltv
    )
    reasons: List[str] = []
    if not eq_valid:
        reasons.append("IRR 산출 불가")
    else:
        if eq_irr < hurdle:
            reasons.append("허들 미달")
        if min_dscr < d.min_dscr:
            reasons.append("DSCR 미달")
        if d.ltv > d.max_ltv:
            reasons.append("LTV 초과")

    return {
        "noi": noi,
        "net_sale": net_sale,
        "unlevered_cf": ucf,
        "levered_cf": lcf,
        "debt_service": ds,
        "loan_payoff": payoff,
        "equity0": equity0,
        "project_irr": proj_irr,
        "project_irr_valid": proj_valid,
        "equity_irr": eq_irr,
        "equity_irr_valid": eq_valid,
        "equity_npv": eq_npv,
        "going_in_cap": going_in_cap,
        "cap_spread": cap_spread,
        "dscr_min": min_dscr,
        "dscr_y1": dscr_y1,
        "cash_on_cash": coc,
        "cash_on_cash_avg": coc_avg,
        "equity_multiple": eq_mult,
        "debt_yield": debt_yield,
        "reserve": reserve,
        "passes": passes,
        "fail_reasons": reasons,
    }


# --------------------------------------------------------------------------- #
# 역산 & 손익분기 (스크리닝 보조)
#   NPV(목표할인율) = 0 지점이 곧 Equity IRR = 목표 지점.
#   NPV는 솔버 없이 항상 계산되고 입력에 단조이므로, IRR 솔버보다 견고하게 이분탐색 가능.
# --------------------------------------------------------------------------- #
def _levered_npv(d: Deal, rate: float) -> float:
    """주어진 딜의 차입 현금흐름을 rate(소수)로 할인한 Equity NPV."""
    breakdown = noi_breakdown(d)
    noi = [row["noi"] for row in breakdown]
    reserve = reserve_schedule(d, breakdown)
    net_sale = terminal_value(d, noi)
    ds, payoff = debt_schedule(d)
    lcf, _ = levered_cf(d, noi, net_sale, ds, payoff, reserve)
    return npv(rate, lcf)


def max_acquisition_price(d: Deal, target_irr: float | None = None,
                          min_dscr: float | None = None) -> Dict[str, Any]:
    """목표 Equity IRR(과 최소 DSCR)을 만족하는 '최대 매입가'를 역산한다.

    - price_irr : Equity NPV(목표IRR)=0 이 되는 가격(이분탐색). 이보다 비싸면 목표 IRR 미달.
    - price_dscr: 최소 DSCR을 만족하는 최대 가격. DSCR ∝ 1/price 이므로 닫힌식.
    - max_price : min(둘) — 둘 다 만족하는 최대 입찰가. binding = 더 빡빡한 쪽.
    가격에 따라 NOI·순매각가는 불변, 대출·자기자본·상환만 비례 변동한다는 구조를 이용.
    """
    target = _pct(d.hurdle_irr if target_irr is None else target_irr)
    mind = d.min_dscr if min_dscr is None else min_dscr

    # 1) IRR(=NPV 0) 기준 최대가 — NPV(target)는 price에 단조감소
    npv_at = lambda p: _levered_npv(replace(d, price=p), target)
    lo = 1.0
    if npv_at(lo) <= 0:
        price_irr = 0.0                      # 아무리 싸도 목표 IRR 미달
    else:
        hi = max(d.price, 2.0)
        unbounded = True
        for _ in range(200):
            if npv_at(hi) < 0:
                unbounded = False
                break
            hi *= 2
        if unbounded:
            price_irr = math.inf
        else:
            for _ in range(100):
                mid = (lo + hi) / 2
                if npv_at(mid) > 0:
                    lo = mid
                else:
                    hi = mid
            price_irr = lo

    # 2) DSCR 기준 최대가 — DSCR(p) = DSCR0 × price0/p  →  p = price0 × DSCR0/mind
    r0 = evaluate(d)
    if mind <= 0 or math.isinf(r0["dscr_min"]):
        price_dscr = math.inf
    else:
        price_dscr = d.price * (r0["dscr_min"] / mind)

    max_price = min(price_irr, price_dscr)
    binding = "IRR" if price_irr <= price_dscr else "DSCR"
    return {
        "price_irr": price_irr,
        "price_dscr": price_dscr,
        "max_price": max_price,
        "binding": binding,
        "target_irr": target,
    }


_BREAKEVEN_RANGES = {
    "exit_cap": (0.25, 30.0),      # 상한: 이보다 캡이 높아지면(가치↓) 탈락
    "vacancy": (0.0, 99.0),        # 상한: 공실이 이보다 커지면 탈락
    "rate": (0.0, 30.0),           # 상한: 금리가 이보다 오르면 탈락
    "rent_growth": (-50.0, 50.0),  # 하한: 임대료성장이 이보다 낮으면 탈락
}


def break_even(d: Deal, field: str, target_irr: float | None = None):
    """단일 변수의 손익분기값(Equity IRR=목표가 되는 임계값, %pt). 범위 내 없으면 None.

    NPV(목표IRR)=0 지점을 이분탐색. 각 변수에 대해 NPV가 단조이므로 임계값이 유일.
    """
    rate = _pct(d.hurdle_irr if target_irr is None else target_irr)
    lo, hi = _BREAKEVEN_RANGES[field]

    def npv_at(v):
        d2 = replace(d, **{field: v})
        if validate(d2):
            return None
        return _levered_npv(d2, rate)

    N = 80
    pts = [lo + (hi - lo) * i / N for i in range(N + 1)]
    vals = [npv_at(p) for p in pts]

    bracket = None
    fa = None
    for i in range(N):
        a, b = vals[i], vals[i + 1]
        if a is None or b is None:
            continue
        if a == 0.0:
            return pts[i]
        if a * b < 0:
            bracket = (pts[i], pts[i + 1])
            fa = a
            break
    if bracket is None:
        return None                          # 범위 내 손익분기 없음(항상 통과/항상 미달)

    a, b = bracket
    for _ in range(100):
        mid = (a + b) / 2
        fm = npv_at(mid)
        if fm is None:
            break
        if fa * fm <= 0:
            b = mid
        else:
            a, fa = mid, fm
    return (a + b) / 2
