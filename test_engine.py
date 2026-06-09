"""
test_engine.py — engine 교차검증 (명세서 v4 §11 수용기준)

손계산으로 정답을 알 수 있는 케이스를 만들어 엔진 결과와 대조한다.
실행: python test_engine.py
"""
import math
from engine import (
    Deal, validate, project_noi, terminal_value,
    unlevered_cf, debt_schedule, levered_cf, irr, npv, evaluate,
)

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name, cond, detail=""):
    results.append((name, cond))
    tag = PASS if cond else FAIL
    line = f"[{tag}] {name}"
    if detail:
        line += f"  ({detail})"
    print(line)


def approx(a, b, tol=1e-4):
    return abs(a - b) < tol


# --------------------------------------------------------------------------- #
# 베이스 딜 빌더: NOI를 정확히 통제하기 위해 모든 성장률 0, 공실 0,
# GPR=8, OpEx absolute=0  →  NOI = 8 (매년 일정).  매입가 100.
# 보유 5년, ExitCap 8%  →  TV = NOI_(n+1)/0.08 = 8/0.08 = 100, 매각비용 0.
# --------------------------------------------------------------------------- #
def bond_like_deal(ltv=0.0, rate=0.0, amort="IO", hurdle=0.0):
    return Deal(
        name="bondlike", price=100.0, acq_cost_pct=0.0,
        gpr1=8.0, other_income=0.0, vacancy=0.0,
        opex_basis="absolute", opex1=0.0, opex_growth=0.0,
        rent_growth=0.0, hold_years=5, exit_cap=8.0, selling_cost_pct=0.0,
        ltv=ltv, rate=rate, amort_type=amort, amort_term_years=30,
        hurdle_irr=hurdle, min_dscr=0.0, max_ltv=100.0,
    )


# 1) NOI 빌드업: 매년 8, n+1년차도 8 (성장 0)
d = bond_like_deal()
noi = project_noi(d)
check("NOI 빌드업 (길이 n+1, 값 8 일정)",
      len(noi) == 6 and all(approx(x, 8.0) for x in noi),
      f"noi={noi}")

# 2) Exit 순매각대금 = 8/0.08 = 100
check("Exit 순매각대금 = 100", approx(terminal_value(d, noi), 100.0),
      f"net_sale={terminal_value(d, noi):.4f}")

# 3) 무차입(All-cash) Project IRR = 8%  (채권형: 매입100, 쿠폰8, 상환100)
res = evaluate(d)
check("무차입 Project IRR = 8%", res["project_irr_valid"] and approx(res["project_irr"], 0.08),
      f"IRR={res['project_irr']*100:.4f}%")

# 4) 무차입이면 Equity IRR == Project IRR
check("무차입 Equity IRR = 8%", res["equity_irr_valid"] and approx(res["equity_irr"], 0.08),
      f"IRR={res['equity_irr']*100:.4f}%")

# 5) Going-in Cap = NOI1/price = 8/100 = 8%
check("Going-in Cap = 8%", approx(res["going_in_cap"], 0.08))

# 6) 레버리지: LTV 50%, IO 5%  →  loan50, equity50, DS=2.5/yr
#    Levered CF: -50, 5.5×4, (5.5+50)=55.5  →  Equity IRR = 11%
dl = bond_like_deal(ltv=50.0, rate=5.0, amort="IO")
resl = evaluate(dl)
check("차입(IO) Equity IRR = 11%", resl["equity_irr_valid"] and approx(resl["equity_irr"], 0.11),
      f"IRR={resl['equity_irr']*100:.4f}%")
check("차입(IO) DSCR = 8/2.5 = 3.2", approx(resl["dscr_min"], 3.2),
      f"DSCR={resl['dscr_min']:.4f}")
check("차입(IO) Cash-on-Cash = 5.5/50 = 11%", approx(resl["cash_on_cash"], 0.11),
      f"CoC={resl['cash_on_cash']*100:.4f}%")

# 7) NPV ↔ IRR 일관성 (§11): Equity IRR이 11%이므로
#    hurdle 11% → NPV≈0 / hurdle 10% → NPV>0 / hurdle 12% → NPV<0
def eq_npv_at(hurdle):
    return evaluate(bond_like_deal(ltv=50.0, rate=5.0, amort="IO", hurdle=hurdle))["equity_npv"]
check("NPV 일관성: hurdle 11% → NPV≈0", approx(eq_npv_at(11.0), 0.0, tol=1e-2),
      f"NPV={eq_npv_at(11.0):.4f}")
check("NPV 일관성: hurdle 10% → NPV>0", eq_npv_at(10.0) > 0, f"NPV={eq_npv_at(10.0):.4f}")
check("NPV 일관성: hurdle 12% → NPV<0", eq_npv_at(12.0) < 0, f"NPV={eq_npv_at(12.0):.4f}")

# 8) 다중 부호변화(캐피탈 콜) → IRR 무효
r, valid = irr([-50, 5, -10, 5, 55])
check("다중 부호변화 → irrValid=False", (not valid) and math.isnan(r))

# 9) 정상 현금흐름 → 부호변화 1회 → 유효
r2, valid2 = irr([-100, 8, 8, 8, 8, 108])
check("단일 부호변화 → 유효", valid2 and approx(r2, 0.08))

# 10) 검증: Equity_0=0 코너 차단 (LTV 100% + 취득비 0% → equity0 = 100 - 100 = 0)
corner = bond_like_deal(ltv=100.0)
check("검증: LTV 100%·취득비 0% → Equity_0=0 차단",
      any("자기자본" in e for e in validate(corner)))

# 11) 검증: LTV 100% + 취득비 5% → equity0 = 5 > 0, 100% 이내 → 통과
okd = bond_like_deal(ltv=100.0)
okd.acq_cost_pct = 5.0
check("검증: LTV 100%·취득비 5% → 통과", len(validate(okd)) == 0, f"errs={validate(okd)}")

# 12) 검증: LTV 101% → 0~100% 상한 초과 차단
over = bond_like_deal(ltv=101.0)
over.acq_cost_pct = 5.0
check("검증: LTV 101% → 0~100% 상한 차단", any("0~100%" in e for e in validate(over)))

# 13) 검증: ExitCap 0 차단
z = bond_like_deal()
z.exit_cap = 0.0
check("검증: ExitCap 0 차단", any("Exit Cap" in e for e in validate(z)))

# 14) 원리금균등 스폿: loan100, rate0, term5 → payment 20, 5년 후 잔액 0
am = bond_like_deal(ltv=100.0, rate=0.0, amort="amortizing")
am.acq_cost_pct = 1.0          # equity0>0 보장 (LTV 100% < 101%)
am.amort_term_years = 5
ds_am, payoff_am = debt_schedule(am)   # loan = 100×100% = 100
check("원리금균등(rate0): 연 상환 20, 5년 후 잔액 0",
      all(approx(x, 20.0) for x in ds_am) and approx(payoff_am, 0.0),
      f"ds={ds_am}, payoff={payoff_am:.6f}")

# 15) 원리금균등 스폿: loan100, rate10%, term30 → payment ≈ 10.6079
am2 = bond_like_deal(ltv=100.0, rate=10.0, amort="amortizing")
am2.acq_cost_pct = 1.0
am2.amort_term_years = 30
ds_am2, _ = debt_schedule(am2)
check("원리금균등(10%/30y): 연 상환 ≈ 10.6079", approx(ds_am2[0], 10.6079, tol=1e-3),
      f"payment={ds_am2[0]:.4f}")

# 16) 무차입(LTV 0)이면 DSCR = ∞, PASS 차단 안 함
nd = bond_like_deal(ltv=0.0)
nd.min_dscr = 1.25
rnd = evaluate(nd)
check("무차입 DSCR=∞ → DSCR로 FAIL되지 않음",
      math.isinf(rnd["dscr_min"]) and ("DSCR 미달" not in rnd["fail_reasons"]))

# --------------------------------------------------------------------------- #
print("\n" + "=" * 48)
total = len(results)
passed = sum(1 for _, c in results if c)
print(f"결과: {passed}/{total} 통과")
if passed != total:
    print("실패 항목:", [n for n, c in results if not c])
    raise SystemExit(1)
print("전부 통과 ✔")
