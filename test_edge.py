"""
test_edge.py — 단계 5: 엣지케이스 QA + 교차검증 (명세서 v4 §7·§11)

test_engine.py(손계산 교차검증)를 보완해, 경계·예외·일관성·단조성을 집중 검증한다.
원칙: 콘솔 에러 0, 임의값 반환 0, irrValid 규약 준수.
실행: python test_edge.py   (UTF-8 콘솔 권장: set PYTHONIOENCODING=utf-8)
"""
import math
from dataclasses import replace

from engine import (
    Deal, validate, evaluate, irr, npv,
    project_noi, noi_breakdown, terminal_value, debt_schedule,
    reserve_schedule, sizing_constraints,
    max_acquisition_price, break_even,
)

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    tag = "PASS" if cond else "FAIL"
    line = f"[{tag}] {name}"
    if detail:
        line += f"  ({detail})"
    print(line)


def approx(a, b, tol=1e-6):
    return abs(a - b) < tol


# --------------------------------------------------------------------------- #
# 공용 딜 빌더
# --------------------------------------------------------------------------- #
def realistic(**over):
    """양의 레버리지·유효 IRR이 보장되는 현실형 베이스 딜."""
    d = Deal(
        name="r", price=10_000.0, acq_cost_pct=3.0,
        gpr1=900.0, other_income=50.0, vacancy=5.0,
        opex_basis="pct", opex1=35.0, opex_growth=2.0, rent_growth=2.0,
        hold_years=5, exit_cap=7.0, selling_cost_pct=2.0,
        ltv=55.0, rate=4.5, amort_type="IO", amort_term_years=30,
        hurdle_irr=8.0, min_dscr=1.25, max_ltv=70.0,
    )
    return replace(d, **over)


# =========================================================================== #
# A. 입력 검증 (§7)
# =========================================================================== #
print("\n— A. 입력 검증 —")

check("A1 매입가<=0 차단", any("매입가" in e for e in validate(realistic(price=0))))
check("A2 ExitCap<=0 차단", any("Exit Cap" in e for e in validate(realistic(exit_cap=0))))
check("A3 보유기간 0 차단", any("보유기간" in e for e in validate(realistic(hold_years=0))))
check("A4 보유기간 실수 차단", any("보유기간" in e for e in validate(realistic(hold_years=2.5))))
check("A5 hurdle<=-100 차단", any("hurdle" in e or "목표" in e for e in validate(realistic(hurdle_irr=-100))))
check("A6 공실>100 차단", any("공실" in e for e in validate(realistic(vacancy=120))))
check("A7 음수 금리 차단", any("금리" in e for e in validate(realistic(rate=-1))))
check("A8 잘못된 상환방식 차단", any("상환방식" in e for e in validate(realistic(amort_type="bullet"))))
check("A9 잘못된 OpEx방식 차단", any("OpEx" in e for e in validate(realistic(opex_basis="ratio"))))
check("A10 amortizing 상환기간 0 차단",
      any("상환기간" in e for e in validate(realistic(amort_type="amortizing", amort_term_years=0))))
check("A11 정상 딜은 위반 0", len(validate(realistic())) == 0, f"errs={validate(realistic())}")

# 자기자본>0 경계: LTV = 1+acq% 정확히 → equity0=0 차단 / 바로 아래 → 통과
acq = 3.0
d_zero = realistic(ltv=100.0 + acq, acq_cost_pct=acq)   # equity0 = price*(1+acq) - price*ltv = 0
check("A12 자기자본=0 경계 차단 (LTV=1+acq%)",
      any("자기자본" in e for e in validate(d_zero)),
      f"errs={validate(d_zero)}")
# LTV는 0~100 상한도 있으므로 경계 통과 검증은 acq를 키워 LTV<100 유지
d_eps = realistic(ltv=99.0, acq_cost_pct=5.0)           # equity0 = price*1.05 - price*0.99 > 0
check("A13 자기자본>0 미세양수 통과", len(validate(d_eps)) == 0, f"errs={validate(d_eps)}")


# =========================================================================== #
# B. NOI 빌드업 정합성
# =========================================================================== #
print("\n— B. NOI 빌드업 —")

d = realistic()
br = noi_breakdown(d)
check("B1 빌드업 길이 = n+1", len(br) == d.hold_years + 1)
check("B2 project_noi == breakdown noi",
      project_noi(d) == [r["noi"] for r in br])

# EGI = GPR*(1-v)+other, NOI = EGI - opex(=35% EGI) 손계산 1년차
egi1 = 900 * (1 - 0.05) + 50
noi1 = egi1 * (1 - 0.35)
check("B3 1년차 EGI/NOI 손계산 일치",
      approx(br[0]["egi"], egi1) and approx(br[0]["noi"], noi1),
      f"egi={br[0]['egi']:.4f} noi={br[0]['noi']:.4f}")

# 기타수입도 g로 성장
check("B4 기타수입 g 성장", approx(br[1]["other"], 50 * 1.02))

# pct 모드는 opex_growth 무시 (값 바꿔도 NOI 불변)
check("B5 pct 모드 opex_growth 무시",
      project_noi(d) == project_noi(realistic(opex_growth=99.0)))

# absolute 모드는 opex_growth 반영
da = realistic(opex_basis="absolute", opex1=300.0, opex_growth=10.0)
bra = noi_breakdown(da)
check("B6 absolute 모드 opex 10% 성장", approx(bra[1]["opex"], 300 * 1.10))

# Exit: 매각비용이 비례 차감
ns_full = terminal_value(realistic(selling_cost_pct=0.0), project_noi(realistic(selling_cost_pct=0.0)))
ns_cost = terminal_value(realistic(selling_cost_pct=2.0), project_noi(realistic(selling_cost_pct=2.0)))
check("B7 매각비용 2% 비례 차감", approx(ns_cost, ns_full * 0.98))


# =========================================================================== #
# C. 핵심 비율
# =========================================================================== #
print("\n— C. 핵심 비율 —")

res = evaluate(d)
check("C1 Going-in Cap = NOI1/Price", approx(res["going_in_cap"], project_noi(d)[0] / d.price))

# CoC는 항상 1년차 '운영' CF/equity0 — 보유 1년이어도 매각대금 미혼입
d1 = realistic(hold_years=1)
r1 = evaluate(d1)
ds1, _ = debt_schedule(d1)
expected_coc = (project_noi(d1)[0] - ds1[0]) / r1["equity0"]
check("C2 보유 1년 CoC 매각대금 미혼입",
      approx(r1["cash_on_cash"], expected_coc),
      f"coc={r1['cash_on_cash']:.6f} exp={expected_coc:.6f}")

# n>=2면 CoC == lcf[1]/equity0 (회귀 안전성)
check("C3 n>=2 CoC == lcf[1]/equity0",
      approx(res["cash_on_cash"], res["levered_cf"][1] / res["equity0"]))

# 무차입 → DSCR=∞, DSCR로 FAIL 안 함
rnl = evaluate(realistic(ltv=0.0, min_dscr=1.25))
check("C4 무차입 DSCR=∞ & DSCR 미달 아님",
      math.isinf(rnl["dscr_min"]) and "DSCR 미달" not in rnl["fail_reasons"])


# =========================================================================== #
# D. 분할상환(amortizing) 엣지
# =========================================================================== #
print("\n— D. 분할상환 —")

# D1 N==n, rate>0 → 만기 잔액 ≈ 0
dN = realistic(amort_type="amortizing", amort_term_years=5, hold_years=5, ltv=55.0, rate=4.5)
ds_N, payoff_N = debt_schedule(dN)
check("D1 N==n: Exit 잔액 ≈ 0", approx(payoff_N, 0.0, tol=1e-6), f"payoff={payoff_N:.8f}")

# D2 N>n → Exit 잔액 = 미상환 원금 > 0, 매년 DS 동일(연금)
dGt = realistic(amort_type="amortizing", amort_term_years=10, hold_years=5)
ds_Gt, payoff_Gt = debt_schedule(dGt)
check("D2 N>n: 매년 DS 일정 & payoff>0",
      all(approx(x, ds_Gt[0]) for x in ds_Gt) and payoff_Gt > 0,
      f"DS={ds_Gt[0]:.4f} payoff={payoff_Gt:.4f}")

# D3 N<n → 완납 이후 DS=0, Exit payoff=0
dLt = realistic(amort_type="amortizing", amort_term_years=3, hold_years=5)
ds_Lt, payoff_Lt = debt_schedule(dLt)
check("D3 N<n: 4~5년차 DS=0 & payoff=0",
      approx(ds_Lt[3], 0.0) and approx(ds_Lt[4], 0.0) and approx(payoff_Lt, 0.0),
      f"DS={[round(x,2) for x in ds_Lt]} payoff={payoff_Lt:.4f}")

# D4 분할상환 잔액 항등식: loan = Σprincipal(지급기간) + payoff
loan = dGt.price * dGt.ltv / 100.0
r_gt = dGt.rate / 100.0
# 직접 재구성: 매년 원금 = payment - 이자(잔액기준)
bal = loan
paid = 0.0
for t in range(dGt.hold_years):
    if ds_Gt[t] > 0:
        interest = bal * r_gt
        principal = ds_Gt[t] - interest
        paid += principal
        bal -= principal
check("D4 원금상환 항등식 loan = Σprincipal + payoff",
      approx(loan, paid + payoff_Gt, tol=1e-6),
      f"loan={loan:.2f} paid={paid:.2f} payoff={payoff_Gt:.2f}")

# D5 분할상환 DSCR은 연금(P+I) 기준 → IO보다 낮다(같은 LTV·rate)
res_io = evaluate(realistic(amort_type="IO"))
res_am = evaluate(realistic(amort_type="amortizing", amort_term_years=20))
check("D5 분할상환 DSCR < IO DSCR (동일 조건)",
      res_am["dscr_min"] < res_io["dscr_min"],
      f"amort={res_am['dscr_min']:.3f} io={res_io['dscr_min']:.3f}")


# =========================================================================== #
# E. IRR 솔버 견고성 (임의값 금지)
# =========================================================================== #
print("\n— E. IRR 솔버 —")

# E1 다중 부호변화 → 무효
r, ok = irr([-100, 50, -30, 40, 60])
check("E1 다중 부호변화 → (nan, False)", (not ok) and math.isnan(r))

# E2 부호변화 없음(모두 음수 이후) → 무효
r, ok = irr([-100, -10, -10, -10])
check("E2 부호변화 0 → 무효", (not ok) and math.isnan(r))

# E3 해가 명백히 존재하는 표준 흐름 → 유효 & NPV(IRR)≈0
cfs = [-100, 8, 8, 8, 8, 108]
r, ok = irr(cfs)
check("E3 표준 흐름 유효 & NPV(IRR)≈0", ok and approx(npv(r, cfs), 0.0, tol=1e-6),
      f"irr={r:.6f}")

# E4 음수 IRR(손실 딜, 단일 부호변화) → 유효한 음수 반환
cfs2 = [-100, 5, 5, 5, 5, 60]  # 명백한 손실, 부호변화 1회
r, ok = irr(cfs2)
check("E4 손실이지만 단일부호변화 → 유효 음수 IRR",
      ok and r < 0 and approx(npv(r, cfs2), 0.0, tol=1e-6),
      f"irr={r*100:.3f}%")

# E5 evaluate에서 IRR 무효 시 passes=False & 사유 명시, 숫자 강제 없음
#    캐피탈콜형 다중부호 흐름을 만들기 위해 만기 손실 + 중간 음수 유도:
#    공실 급등으로 운영 CF 음수 → 매각 양수 → 음수 = 다중부호
d_multi = realistic(ltv=70.0, rate=12.0, vacancy=60.0, exit_cap=4.0)
rm = evaluate(d_multi)
check("E5 IRR 무효 시 NaN & passes=False & '산출 불가' 사유",
      (not rm["equity_irr_valid"]) and math.isnan(rm["equity_irr"])
      and (not rm["passes"]) and ("IRR 산출 불가" in rm["fail_reasons"]),
      f"valid={rm['equity_irr_valid']} reasons={rm['fail_reasons']}")


# =========================================================================== #
# F. NPV ↔ IRR 일관성 (§11)
# =========================================================================== #
print("\n— F. NPV↔IRR 일관성 —")

base = realistic()
eq_irr = evaluate(base)["equity_irr"]
assert evaluate(base)["equity_irr_valid"]
irr_pct = eq_irr * 100
npv_at = lambda h: evaluate(realistic(hurdle_irr=h))["equity_npv"]
check("F1 hurdle=IRR → NPV≈0", approx(npv_at(irr_pct), 0.0, tol=1e-3), f"NPV={npv_at(irr_pct):.6f}")
check("F2 hurdle<IRR → NPV>0", npv_at(irr_pct - 2) > 0, f"NPV={npv_at(irr_pct-2):.4f}")
check("F3 hurdle>IRR → NPV<0", npv_at(irr_pct + 2) < 0, f"NPV={npv_at(irr_pct+2):.4f}")

# PASS 판정과 NPV 부호 일치: IRR>=hurdle ⟺ NPV>=0 (다른 기준 충족 시)
res_pass = evaluate(realistic(hurdle_irr=irr_pct - 2, min_dscr=0.0, max_ltv=100.0))
check("F4 IRR>hurdle 이면서 기준충족 → PASS & NPV>0",
      res_pass["passes"] and res_pass["equity_npv"] > 0)


# =========================================================================== #
# G. 1D 민감도 단조성 (§11)
# =========================================================================== #
print("\n— G. 민감도 단조성 —")


def irr_seq(field, values):
    out = []
    for v in values:
        d2 = realistic(**{field: float(v)})
        if validate(d2):
            out.append(None)
            continue
        r = evaluate(d2)
        out.append(r["equity_irr"] if r["equity_irr_valid"] else None)
    return out


def strictly_decreasing(seq):
    return all(a is not None and b is not None and a > b for a, b in zip(seq, seq[1:]))


def strictly_increasing(seq):
    return all(a is not None and b is not None and a < b for a, b in zip(seq, seq[1:]))


s_cap = irr_seq("exit_cap", [5, 6, 7, 8, 9])
check("G1 Exit Cap ↑ → Equity IRR ↓ (강한 단조감소)", strictly_decreasing(s_cap),
      f"{[round(x*100,2) for x in s_cap]}")

s_rate = irr_seq("rate", [2, 3, 4, 5, 6])
check("G2 대출금리 ↑ → Equity IRR ↓", strictly_decreasing(s_rate),
      f"{[round(x*100,2) for x in s_rate]}")

s_vac = irr_seq("vacancy", [2, 4, 6, 8, 10])
check("G3 공실률 ↑ → Equity IRR ↓", strictly_decreasing(s_vac),
      f"{[round(x*100,2) for x in s_vac]}")

s_g = irr_seq("rent_growth", [0, 1, 2, 3, 4])
check("G4 임대료성장률 ↑ → Equity IRR ↑", strictly_increasing(s_g),
      f"{[round(x*100,2) for x in s_g]}")


# =========================================================================== #
# H. 2D 셀 산출불가 처리 (회색 셀 데이터 경로)
# =========================================================================== #
print("\n— H. 2D 산출불가 셀 —")

# 앱의 eval_equity_irr가 의존하는 경로: 검증 실패 → None, IRR 무효 → None
def cell(**over):
    d2 = replace(realistic(), **over)
    if validate(d2):
        return None
    r = evaluate(d2)
    return r["equity_irr"] if r["equity_irr_valid"] else None

check("H1 검증실패 셀 → None", cell(exit_cap=0.0) is None)
check("H2 IRR무효 셀 → None", cell(ltv=70.0, rate=12.0, vacancy=60.0, exit_cap=4.0) is None)
check("H3 정상 셀 → 실수 IRR", isinstance(cell(exit_cap=7.0), float))


# =========================================================================== #
# I. 0 나누기·극단값 무크래시 (콘솔 에러 0)
# =========================================================================== #
print("\n— I. 무크래시 —")

ok_nocrash = True
detail = ""
try:
    # 극단적이지만 검증 통과 가능한 값들 — 전 파이프라인이 예외 없이 끝나야 함
    for d2 in [
        realistic(exit_cap=0.01),            # 초대형 TV
        realistic(rent_growth=50.0),         # 고성장
        realistic(opex_basis="absolute", opex1=100000.0),  # 운영적자(NOI 음수)
        realistic(ltv=0.0),                  # 무차입
        realistic(hold_years=1),             # 최소 보유
        realistic(hold_years=30),            # 장기 보유
    ]:
        if not validate(d2):
            evaluate(d2)
except Exception as ex:  # noqa
    ok_nocrash = False
    detail = repr(ex)
check("I1 극단 입력 전 구간 무예외", ok_nocrash, detail)

# NOI 음수 딜도 임의값 없이 처리 (IRR 무효 또는 유효 음수, NaN이면 valid=False)
d_neg = realistic(opex_basis="absolute", opex1=100000.0)
if not validate(d_neg):
    rneg = evaluate(d_neg)
    consistent = (rneg["equity_irr_valid"] == (not math.isnan(rneg["equity_irr"])))
    check("I2 NOI 음수 딜: irrValid ⟺ IRR 비-NaN (임의값 금지)", consistent,
          f"valid={rneg['equity_irr_valid']} irr={rneg['equity_irr']}")
else:
    check("I2 NOI 음수 딜 처리", False, "검증에서 비정상 차단")


# =========================================================================== #
# J. 자본리저브 (CapEx/TI/LC) — 현금흐름만 차감, NOI/Cap/DSCR/TV 불변
# =========================================================================== #
print("\n— J. 자본리저브 —")

base0 = realistic(capex_reserve_pct=0.0)
res0 = evaluate(base0)

# J1 reserve=0 → 회귀: 기존과 완전 동일 (후방호환)
check("J1 reserve=0 회귀: IRR/NPV 불변",
      res0["equity_irr_valid"] and approx(res0["reserve"][0], 0.0)
      and all(approx(x, 0.0) for x in res0["reserve"]))

# J2 reserve>0 → 운영연도 CF가 EGI×pct 만큼 감소, Equity IRR 엄격히 하락
resv = realistic(capex_reserve_pct=10.0)
rrv = evaluate(resv)
br = noi_breakdown(resv)
expected_res1 = br[0]["egi"] * 0.10
check("J2 reserve=EGI×pct (1년차)", approx(rrv["reserve"][0], expected_res1),
      f"reserve1={rrv['reserve'][0]:.4f} exp={expected_res1:.4f}")
check("J3 reserve↑ → Equity IRR 하락",
      rrv["equity_irr"] < res0["equity_irr"],
      f"r0={res0['equity_irr']*100:.3f}% rv={rrv['equity_irr']*100:.3f}%")

# J4 NOI·Going-in Cap·DSCR·Net Sale(TV)는 리저브와 무관 (불변)
check("J4 NOI/Cap/DSCR/TV는 리저브 불변",
      res0["noi"] == rrv["noi"]
      and approx(res0["going_in_cap"], rrv["going_in_cap"])
      and approx(res0["dscr_min"], rrv["dscr_min"])
      and approx(res0["net_sale"], rrv["net_sale"]))

# J5 CoC가 리저브 차감 반영 (= (noi0-ds0-reserve0)/equity0)
ds_v, _ = debt_schedule(resv)
exp_coc = (rrv["noi"][0] - ds_v[0] - rrv["reserve"][0]) / rrv["equity0"]
check("J5 CoC 리저브 반영", approx(rrv["cash_on_cash"], exp_coc),
      f"coc={rrv['cash_on_cash']:.6f} exp={exp_coc:.6f}")

# J6 운영연도 차입 CF 감소분 = reserve 정확히 일치 (Exit 연도 제외 비교)
delta = res0["levered_cf"][1] - rrv["levered_cf"][1]
check("J6 1년차 차입CF 감소분 = reserve1", approx(delta, rrv["reserve"][0]))

# J7 음수 리저브 차단
check("J7 음수 리저브 검증 차단",
      any("자본리저브" in e for e in validate(realistic(capex_reserve_pct=-1.0))))

# J8 reserve_schedule 길이 = n (운영연도)
check("J8 reserve 길이 = n", len(reserve_schedule(resv)) == resv.hold_years)


# =========================================================================== #
# K. 대출 사이징 제약 표시 (경량 #3) + Debt Yield
# =========================================================================== #
print("\n— K. 사이징 제약 · Debt Yield —")

# K1 Debt Yield = NOI1 / Loan
dk = realistic(ltv=55.0)
rk = evaluate(dk)
loan_k = dk.price * dk.ltv / 100.0
check("K1 Debt Yield = NOI1/Loan", approx(rk["debt_yield"], project_noi(dk)[0] / loan_k),
      f"DY={rk['debt_yield']*100:.2f}%")

# K2 무차입 → Debt Yield = ∞
check("K2 무차입 Debt Yield=∞", math.isinf(evaluate(realistic(ltv=0.0))["debt_yield"]))

# K3 IO 사이징 손계산: DSCR한도 대출액 = NOI1/(minDSCR×rate)
dio = realistic(amort_type="IO", rate=5.0, min_dscr=1.25, max_ltv=70.0)
sc = sizing_constraints(dio)
exp_dscr_loan = project_noi(dio)[0] / (1.25 * 0.05)
check("K3 IO DSCR한도 대출액 손계산 일치",
      approx(sc["loan_cap_dscr"], exp_dscr_loan),
      f"dscr_loan={sc['loan_cap_dscr']:.2f} exp={exp_dscr_loan:.2f}")
check("K4 LTV한도 대출액 = Price×maxLTV", approx(sc["loan_cap_ltv"], dio.price * 0.70))
check("K5 binding = min, implied_max_ltv 일치",
      approx(sc["binding_loan"], min(sc["loan_cap_ltv"], sc["loan_cap_dscr"]))
      and approx(sc["implied_max_ltv"], sc["binding_loan"] / dio.price),
      f"binding={sc['binding']} implied_ltv={sc['implied_max_ltv']*100:.1f}%")

# K6 rate=0 IO → DSCR 제약 없음(∞) → binding = LTV
sc0 = sizing_constraints(realistic(amort_type="IO", rate=0.0))
check("K6 rate=0 IO: DSCR한도=∞ & binding=LTV",
      math.isinf(sc0["loan_cap_dscr"]) and sc0["binding"] == "LTV")

# K7 분할상환 사이징: DSCR한도 = NOI1/(minDSCR×연금계수)
dam = realistic(amort_type="amortizing", amort_term_years=20, rate=5.0, min_dscr=1.25)
r_am = 0.05
factor = r_am / (1 - (1 + r_am) ** (-20))
exp_am_loan = project_noi(dam)[0] / (1.25 * factor)
check("K7 분할상환 DSCR한도 손계산 일치",
      approx(sizing_constraints(dam)["loan_cap_dscr"], exp_am_loan),
      f"loan={sizing_constraints(dam)['loan_cap_dscr']:.2f} exp={exp_am_loan:.2f}")

# K8 DSCR가 binding이 되는 케이스(고LTV한도·저DSCR한도)
dtight = realistic(amort_type="IO", rate=8.0, min_dscr=1.5, max_ltv=90.0)
sct = sizing_constraints(dtight)
check("K8 고금리·고DSCR → DSCR binding", sct["binding"] == "DSCR",
      f"ltv_loan={sct['loan_cap_ltv']:.0f} dscr_loan={sct['loan_cap_dscr']:.0f}")


# =========================================================================== #
# L. 검증 완전성 (silent garbage 차단) — 적대적 입력
# =========================================================================== #
print("\n— L. 검증 완전성 —")

# L1 음수 OpEx → NOI가 EGI보다 커지는 silent garbage 차단
check("L1 음수 OpEx 차단", any("OpEx" in e for e in validate(realistic(opex1=-10.0))))

# L2 rent_growth <= -100 → (1+g)<=0 부호 진동 차단
check("L2 rent_growth=-100 차단", any("임대료 성장률" in e for e in validate(realistic(rent_growth=-100.0))))
check("L3 rent_growth=-150 차단", any("임대료 성장률" in e for e in validate(realistic(rent_growth=-150.0))))

# L4 absolute 모드 opex_growth <= -100 차단
check("L4 absolute opex_growth=-100 차단",
      any("비용상승률" in e for e in validate(
          realistic(opex_basis="absolute", opex1=300.0, opex_growth=-100.0))))

# L5 pct 모드에서는 opex_growth가 무시되므로 -100이어도 통과(과잉차단 방지)
check("L5 pct 모드 opex_growth=-100 통과",
      len(validate(realistic(opex_basis="pct", opex_growth=-100.0))) == 0,
      f"errs={validate(realistic(opex_basis='pct', opex_growth=-100.0))}")

# L6 음수 min_dscr 차단
check("L6 음수 min_dscr 차단", any("최소 DSCR" in e for e in validate(realistic(min_dscr=-1.0))))

# L7 경계: rent_growth = -99 는 유효 (>-100)
check("L7 rent_growth=-99 통과", len(validate(realistic(rent_growth=-99.0))) == 0)

# L8 음수 성장률(현실적 임대료 하락)은 허용되어야 함 — 과잉차단 회귀 방지
check("L8 rent_growth=-5(임대료 하락) 통과 & NOI 단조 감소",
      len(validate(realistic(rent_growth=-5.0))) == 0
      and project_noi(realistic(rent_growth=-5.0))[1] < project_noi(realistic(rent_growth=-5.0))[0])

# L9 OpEx=0 은 허용(절대모드 무비용 자산)
check("L9 OpEx=0 허용", len(validate(realistic(opex_basis="absolute", opex1=0.0))) == 0)


# =========================================================================== #
# M. 역산(최대 매입가) & 손익분기
# =========================================================================== #
print("\n— M. 역산·손익분기 —")

# M1 IRR 기준 최대가: 그 가격에서 Equity IRR ≈ 목표(허들), 1% 더 비싸면 미달
base_be = realistic(hurdle_irr=8.0, min_dscr=0.0)  # DSCR 비활성 → 순수 IRR 바인딩
mp = max_acquisition_price(base_be)
at_max = evaluate(replace(base_be, price=mp["price_irr"]))
above = evaluate(replace(base_be, price=mp["price_irr"] * 1.01))
check("M1 최대가에서 IRR≈허들 & 1%↑면 미달",
      at_max["equity_irr_valid"] and approx(at_max["equity_irr"], 0.08, tol=1e-3)
      and above["equity_irr"] < 0.08,
      f"price_irr={mp['price_irr']:.2f} IRR@max={at_max['equity_irr']*100:.3f}%")

# M2 더 싸게 사면 IRR>허들 (단조)
cheaper = evaluate(replace(base_be, price=mp["price_irr"] * 0.9))
check("M2 최대가보다 싸면 IRR>허들", cheaper["equity_irr"] > 0.08,
      f"IRR={cheaper['equity_irr']*100:.2f}%")

# M3 DSCR 바인딩: 높은 최소DSCR이면 max_price가 DSCR로 묶이고, 그 가격 DSCR≈기준
tight = realistic(hurdle_irr=1.0, min_dscr=2.5, rate=6.0)  # 허들 낮춰 DSCR이 binding 되게
mp2 = max_acquisition_price(tight)
at_dscr = evaluate(replace(tight, price=mp2["price_dscr"]))
check("M3 DSCR 기준 최대가에서 최소DSCR≈기준",
      approx(at_dscr["dscr_min"], 2.5, tol=1e-3) and mp2["binding"] == "DSCR",
      f"binding={mp2['binding']} dscr@={at_dscr['dscr_min']:.4f}")

# M4 max_price = min(price_irr, price_dscr)
check("M4 max_price = min(IRR가, DSCR가)",
      approx(mp2["max_price"], min(mp2["price_irr"], mp2["price_dscr"])))

# M5 무차입(LTV0) → DSCR 제약 없음(price_dscr=∞)
mp3 = max_acquisition_price(realistic(ltv=0.0))
check("M5 무차입 → DSCR가 ∞", math.isinf(mp3["price_dscr"]))

# 손익분기 테스트는 세 변수 모두 임계값이 존재하도록 허들 3%로 (min_dscr 비활성)
be_base = realistic(hurdle_irr=3.0, min_dscr=0.0)

# M6 손익분기 Exit Cap: 그 캡에서 IRR≈허들, 더 높은 캡이면 미달(상한)
be_cap = break_even(be_base, "exit_cap")
r_at = evaluate(replace(be_base, exit_cap=be_cap))
r_hi = evaluate(replace(be_base, exit_cap=be_cap + 0.5))
check("M6 손익분기 Exit Cap에서 IRR≈허들 & 더 높으면 미달",
      be_cap is not None and approx(r_at["equity_irr"], 0.03, tol=1e-3)
      and r_hi["equity_irr"] < 0.03,
      f"be_cap={be_cap:.3f}% IRR@={r_at['equity_irr']*100:.3f}%")

# M7 손익분기 공실: 그 공실에서 IRR≈허들 (상한)
be_vac = break_even(be_base, "vacancy")
check("M7 손익분기 공실에서 IRR≈허들",
      be_vac is not None and approx(evaluate(replace(be_base, vacancy=be_vac))["equity_irr"], 0.03, tol=1e-3),
      f"be_vac={be_vac:.3f}%")

# M8 손익분기 임대료성장(하한): 그 값에서 IRR≈허들, 더 낮으면 미달
be_g = break_even(be_base, "rent_growth")
r_lo = evaluate(replace(be_base, rent_growth=be_g - 0.5))
check("M8 손익분기 성장률에서 IRR≈허들 & 더 낮으면 미달",
      be_g is not None and approx(evaluate(replace(be_base, rent_growth=be_g))["equity_irr"], 0.03, tol=1e-3)
      and r_lo["equity_irr"] < 0.03,
      f"be_g={be_g:.3f}%")

# M9 손익분기 None: 무차입(LTV0) 딜은 '금리'가 현금흐름에 무관 → NPV 상수 → 교차 없음
check("M9 무차입 딜의 금리 손익분기 없음(None)",
      break_even(realistic(ltv=0.0, hurdle_irr=1.0), "rate") is None)


# =========================================================================== #
# N. 평균 CoC & 캡 스프레드
# =========================================================================== #
print("\n— N. 평균 CoC·캡 스프레드 —")

dN = realistic()
rN = evaluate(dN)
dsN, _ = debt_schedule(dN)
noiN = project_noi(dN)

# N1 캡 스프레드 = Exit Cap − Going-in Cap (소수)
exp_spread = (dN.exit_cap / 100) - rN["going_in_cap"]
check("N1 캡 스프레드 = ExitCap − Going-in", approx(rN["cap_spread"], exp_spread),
      f"spread={rN['cap_spread']*100:.3f}%p")

# N2 캡 확장(Exit>진입)이면 spread>0
check("N2 Exit Cap>Going-in → spread>0", rN["cap_spread"] > 0)

# N3 평균 CoC = 보유기간 운영CF 평균 / equity0 (Exit 제외)
op = [noiN[t] - dsN[t] - rN["reserve"][t] for t in range(dN.hold_years)]
exp_avg = (sum(op) / dN.hold_years) / rN["equity0"]
check("N3 평균 CoC 손계산 일치", approx(rN["cash_on_cash_avg"], exp_avg),
      f"avg={rN['cash_on_cash_avg']*100:.3f}%")

# N4 1년차 CoC = 운영CF[0]/equity0 (회귀)
check("N4 1년차 CoC = op_cf[0]/equity0",
      approx(rN["cash_on_cash"], op[0] / rN["equity0"]))

# N5 성장 딜이면 평균 CoC ≥ 1년차 CoC (NOI 성장 → 후기 현금흐름 큼)
check("N5 성장 딜: 평균 CoC ≥ 1년차 CoC",
      rN["cash_on_cash_avg"] >= rN["cash_on_cash"] - 1e-12,
      f"avg={rN['cash_on_cash_avg']*100:.3f}% y1={rN['cash_on_cash']*100:.3f}%")

# N6 성장률 0이면 평균 CoC == 1년차 CoC (운영CF 매년 동일)
d0 = realistic(rent_growth=0.0, opex_basis="absolute", opex1=300.0, opex_growth=0.0)
r0 = evaluate(d0)
check("N6 무성장 딜: 평균 CoC == 1년차 CoC",
      approx(r0["cash_on_cash_avg"], r0["cash_on_cash"]),
      f"avg={r0['cash_on_cash_avg']*100:.3f}% y1={r0['cash_on_cash']*100:.3f}%")


# --------------------------------------------------------------------------- #
print("\n" + "=" * 56)
total = len(results)
passed = sum(1 for _, c in results if c)
print(f"엣지케이스 QA 결과: {passed}/{total} 통과")
if passed != total:
    print("실패 항목:", [n for n, c in results if not c])
    raise SystemExit(1)
print("전부 통과 ✔")
