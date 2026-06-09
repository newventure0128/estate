# 딜 스크리너 (Deal Screener)

부동산금융론 개인 프로젝트 — 여러 부동산 투자 후보(딜)를 한 번에 입력받아 **NOI → 현금흐름 → IRR/NPV → 민감도**를
정확하게 산출하고, 투자 허들 통과 여부를 판정해 **Equity IRR 기준으로 랭킹**하는 심사 보조 대시보드.

> **대상:** 안정화된 수익형 자산(임대가 정상 궤도에 오른 건물)의 1차 스크리닝.
> **핵심 가치:** 화려함이 아니라 **정합성** — 모든 계산은 결정론적이고 손계산·교차검증 가능.

---

## 빠른 실행

```bash
pip install -r requirements.txt
streamlit run app.py            # 브라우저 대시보드
python test_engine.py           # 손계산 교차검증 (20개)
python test_edge.py             # 엣지·일관성·역산·민감도 QA (87개)
```

> Windows 콘솔에서 테스트 한글 출력이 깨지면: `set PYTHONIOENCODING=utf-8` 후 실행.

---

## 구조 (3 모듈 분리)

| 파일 | 역할 |
|---|---|
| `engine.py` | **순수 계산 모듈.** Streamlit/UI에 의존하지 않는다 → 단위 테스트·엑셀 교차검증 가능. |
| `app.py` | **Streamlit UI.** 엔진을 호출만 한다(계산 로직 없음). 비교 테이블·스크리닝·드릴다운. |
| `test_engine.py` / `test_edge.py` | **교차검증·QA.** 손계산 정답 대조 + 엣지케이스. 총 **107개 통과.** |

핵심 함수(엔진):
- `noi_breakdown` / `project_noi` — 연도별 NOI 빌드업(t = 1 … n+1, Exit용 포워드 NOI 명시 계산)
- `terminal_value` — 수익환원 Exit (NOI₍ₙ₊₁₎ ÷ Exit Cap × (1−매각비용))
- `unlevered_cf` / `debt_schedule` / `levered_cf` — 무차입·차입 현금흐름 (IO / 원리금균등)
- `irr` / `npv` — Newton-Raphson + bisection (다중 부호변화·미수렴 시 `irrValid=False`, **임의값 금지**)
- `evaluate` — 종합 지표(Project/Equity IRR, Equity NPV, Going-in Cap, 캡스프레드, DSCR, CoC(1년/평균), Equity Multiple, Debt Yield, PASS/FAIL)
- `sizing_constraints` — LTV·DSCR 한도 대출액(표시 전용)
- `max_acquisition_price` / `break_even` — 목표 IRR 기준 **최대 입찰가**, 변수별 **손익분기**

---

## 핵심 계산 흐름

```
임대수입(GPR) − 공실 + 기타수입 = EGI
EGI − 운영비(OpEx) = NOI            (t = 1 … n+1 까지 빌드업)
NOI₍ₙ₊₁₎ ÷ Exit Cap × (1−매각비용) = 순매각대금
─────────────────────────────────────────────
무차입 CF  = −매입원가, NOI − 리저브, … , +순매각
차입 CF    = −자기자본, NOI − 부채상환 − 리저브, … , +순매각 − 대출잔액
Project IRR = IRR(무차입) , Equity IRR = IRR(차입) , Equity NPV = Σ 차입CF/(1+허들)^t
─────────────────────────────────────────────
PASS = Equity IRR ≥ 허들  AND  최소 DSCR ≥ 기준  AND  LTV ≤ 상한  (AND irrValid)
랭킹 = irrValid 우선 → Equity IRR 내림차순
```

규약: **모든 % 입력은 퍼센트포인트**(5 = 5%), 내부에서 ÷100. **IRR·수익률은 소수로 계산, 표시 단계에서만 ×100·반올림.**

---

## 배포 (Streamlit Community Cloud)

1. 이 레포를 GitHub에 push.
2. https://share.streamlit.io → **New app** → 레포 선택 → Main file path = `app.py` → Deploy.
3. 공개 URL 발급(`requirements.txt` 자동 설치). matplotlib 라벨은 ASCII라 서버 한글 폰트 이슈 없음.

---

## 범위 & 가정 (정직한 disclosure)

- **안정화 자산 전용**: 임대수입이 g%로 매끄럽게 성장 + 공실 일정. 리스업·밸류애드·개발·임대차 롤오버는 범위 밖.
- **세전** 기준(세금·감가상각 제외), **연 단위** 현금흐름·상환(월 단위 분할상환 제외).
- **자본리저브(CapEx/TI/LC)**: NOI·Cap·DSCR은 NOI 기준 유지(렌더 관행), 자기자본 현금흐름에서만 차감.
- 몬테카를로·확률 시뮬레이션·세후 IRR·외부 시세 API는 범위 밖(정합성·검증 부담 대비 무결점 보증 불가).
