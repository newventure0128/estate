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
from dataclasses import dataclass
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
    ]:
        if val < 0:
            errs.append(f"{name}은(는) 음수일 수 없습니다.")

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
def project_noi(d: Deal) -> List[float]:
    """길이 n+1 리스트. index 0 = 1년차 NOI ... index n = (n+1)년차 NOI."""
    g = _pct(d.rent_growth)
    v = _pct(d.vacancy)
    i = _pct(d.opex_growth)
    noi: List[float] = []
    for t in range(1, d.hold_years + 2):  # 1 .. n+1
        gpr = d.gpr1 * (1 + g) ** (t - 1)
        other = d.other_income * (1 + g) ** (t - 1)
        egi = gpr * (1 - v) + other
        if d.opex_basis == "pct":
            opex = egi * _pct(d.opex1)          # EGI 추종 (opex_growth 무시)
        else:
            opex = d.opex1 * (1 + i) ** (t - 1)  # 절대금액 성장
        noi.append(egi - opex)
    return noi


def terminal_value(d: Deal, noi: List[float]) -> float:
    """순매각대금 = (NOI_(n+1) / ExitCap) × (1 - 매각비용%)."""
    noi_n1 = noi[d.hold_years]            # (n+1)년차
    tv = noi_n1 / _pct(d.exit_cap)
    return tv * (1 - _pct(d.selling_cost_pct))


# --------------------------------------------------------------------------- #
# 현금흐름
# --------------------------------------------------------------------------- #
def unlevered_cf(d: Deal, noi: List[float], net_sale: float) -> List[float]:
    n = d.hold_years
    cf = [-d.price * (1 + _pct(d.acq_cost_pct))]   # CF_0
    for t in range(1, n):                          # 1 .. n-1
        cf.append(noi[t - 1])
    cf.append(noi[n - 1] + net_sale)               # n년차 + Exit
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
               ds: List[float], payoff: float) -> Tuple[List[float], float]:
    n = d.hold_years
    loan = d.price * _pct(d.ltv)
    equity0 = d.price * (1 + _pct(d.acq_cost_pct)) - loan
    cf = [-equity0]                                # CF_0
    for t in range(1, n):                          # 1 .. n-1
        cf.append(noi[t - 1] - ds[t - 1])
    cf.append(noi[n - 1] - ds[n - 1] + net_sale - payoff)  # n년차
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
# 종합 지표 (§5 핵심 비율 + §3 스크리닝)
# --------------------------------------------------------------------------- #
def evaluate(d: Deal) -> Dict[str, Any]:
    """검증 통과를 전제로 전체 지표를 계산해 dict 반환."""
    noi = project_noi(d)
    net_sale = terminal_value(d, noi)

    ucf = unlevered_cf(d, noi, net_sale)
    proj_irr, proj_valid = irr(ucf)

    ds, payoff = debt_schedule(d)
    lcf, equity0 = levered_cf(d, noi, net_sale, ds, payoff)
    eq_irr, eq_valid = irr(lcf)

    hurdle = _pct(d.hurdle_irr)
    eq_npv = npv(hurdle, lcf)

    going_in_cap = noi[0] / d.price

    dscrs = [math.inf if ds[t] == 0 else noi[t] / ds[t] for t in range(d.hold_years)]
    min_dscr = min(dscrs)
    dscr_y1 = dscrs[0]

    coc = lcf[1] / equity0 if equity0 != 0 else math.nan  # 1년차 차입 현금흐름 / 자기자본

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
        "dscr_min": min_dscr,
        "dscr_y1": dscr_y1,
        "cash_on_cash": coc,
        "equity_multiple": eq_mult,
        "passes": passes,
        "fail_reasons": reasons,
    }
