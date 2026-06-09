# CLAUDE.md — 딜 스크리너 (Deal Screener)

부동산금융론 개인 프로젝트. 여러 부동산 투자 후보(딜)를 입력받아 수익성 지표를 산출하고,
투자 허들 통과 여부를 판정해 **Equity IRR 기준으로 랭킹**하는 의사결정 지원 웹 앱.
상세 스펙은 `딜스크리너_요구사항명세서.md`(v4) 참조. 발표일: 6월 10일.

## 최우선 원칙 (이 프로젝트의 헌법)
**"올바르게 못 만들 거면 넣지 않는다."**
마감을 이유로 결함·임시 제약을 안고 가지 않는다. 채점 핵심(NOI·IRR·민감도)만 검증된
무결점 상태로 유지한다. 새 기능보다 정합성·검증이 항상 우선. 기능을 추가하기보다,
의심스러우면 범위를 줄이고 테스트를 늘린다.

## 아키텍처
- `engine.py` — 순수 계산 모듈. **Streamlit/UI에 절대 의존하지 않는다.** (단위 테스트·교차검증 가능 유지)
- `app.py` — Streamlit UI. 엔진을 호출만 한다. (구현 완료)
- `test_engine.py` — 손계산 교차검증. **현재 20/20 통과.**
- `test_edge.py` — 엣지·일관성·단조성·검증완전성·역산/손익분기·평균CoC/캡스프레드 QA. **현재 87/87 통과.**
- `requirements.txt` — streamlit, matplotlib, pandas, numpy.

## 잠긴 규약 (변경 금지, 변경 시 테스트부터)
- **모든 % 입력은 퍼센트포인트**(5 == 5%). 엔진 내부에서 `_pct()`로 ÷100. UI도 동일 규약.
- **IRR·금리·수익률은 소수로 반환**(0.11 == 11%). 표시 단계에서만 ×100·반올림.
- **숫자는 내부 풀 정밀도 유지, 표시할 때만 반올림.**

## 확정된 재무 로직 결정 (재논의 금지)
- **Exit 환원**: 포워드 NOI. `NOI_(n+1)`을 빌드업으로 **명시 계산**(`×(1+g)` 근사 금지). 수익환원법(직접환원) 단일 방식.
- **NPV**: Equity NPV = Σ LeveredCF_t / (1+hurdleIRR)^t. 무차입 흐름을 차입 허들로 할인하지 않음.
  Project NPV는 무차입 요구수익률 필드가 없어 범위 밖.
- **Equity Multiple**: Σ(양의 CF) / |Σ(음의 CF)| — 캐피탈 콜 방어식.
- **IRR 솔버**: Newton-Raphson + bisection 폴백. **부호변화 ≠ 1회면 `irrValid=False`**(다중해 무효).
  수렴 실패·해 없음도 `irrValid=False`. **임의값 반환 절대 금지.**
- **LTV 검증**: `LTV ∈ [0, 100%]` AND `Equity_0 > 0`. 후자는 "LTV 100%+취득비 0%" 코너만 추가 차단.
- **상환**: 데모 딜은 IO 사용. 원리금균등은 구현하되 테스트로 스폿 검증(잠재 결함 방지).
  연 단위 상환. `rate=0`이면 payment=Loan/N. 상환기간 N<n이면 완납 후 DS=0·payoff=0.
- **랭킹**: `irrValid` 플래그 우선(유효 먼저) → Equity IRR 내림차순. **매직넘버 미사용.**
- **역산·손익분기**: `max_acquisition_price`(목표IRR/최소DSCR 만족 최대 입찰가), `break_even`(변수별 IRR=허들 임계값).
  **IRR 솔버 대신 NPV(목표할인율)=0 지점을 이분탐색** — NPV는 솔버 없이 항상 계산되고 입력에 단조라 견고.
  DSCR 기준 최대가는 `DSCR ∝ 1/price` 닫힌식. 가격에 NOI·순매각가 불변, 대출·자기자본만 비례.
- **자본리저브(CapEx/TI/LC)**: 딜별 `capexReservePct`(EGI 대비 %pt, default 0). `Reserve_t = EGI_t × pct`.
  **현금흐름에서만 차감**(Unlevered·Levered CF, CoC) — **NOI·Going-in Cap·DSCR·TV는 불변**(렌더 관행).
  CoC = `(NOI_1 − DS_1 − Reserve_1) / Equity_0`. default 0이면 기존과 완전 동일(후방호환).
- **Debt Yield**: `NOI_1 / Loan` 산출(무차입은 ∞). 렌더 핵심지표로 표시.
- **대출 사이징(표시 전용)**: 실제 대출은 여전히 `Price×LTV`. `sizing_constraints()`가
  `LTV한도(Price×maxLTV)` vs `DSCR한도(IO: NOI_1/(minDSCR×rate), 분할: ÷연금계수)`와 binding 제약만 **표시**.
  사이징을 적용하지 않음(스크리닝 설계 유지).

## 범위 밖 (넣지 말 것)
몬테카를로·확률 시뮬레이션, 토네이도 차트, 확률 기반 리스크조정 랭킹, 세후 IRR,
외부 시세 API, 계정·DB 영속화, **월 단위 현금흐름·분할상환**(연 단위 모델), **DSCR 기반 대출 사이징 적용**
(제약 표시만), 임대 롤오버/마크투마켓. (정합성·검증 부담 대비 무결점 보증 불가)

## 현재 상태 / 다음 단계
- [x] 단계 1: `engine.py` + `test_engine.py` (20/20 통과)
- [x] 단계 2~3: `app.py` Streamlit UI — 비교 테이블·스크리닝/랭킹·드릴다운(NOI 빌드업·현금흐름·1D/2D 히트맵)
- [x] 단계 4: 안정화 자산 합성 프리셋 3종 + 숫자 포맷(억/원). 취득비 ~5~6%, 기본 허들 8% (물류 PASS·나머지 FAIL로 판별 시연)
- [x] 단계 5: 엣지케이스 QA(`test_edge.py` 63/63) + 리저브·Debt Yield·사이징 제약 표시 추가
- [ ] 단계 6: Streamlit Community Cloud 배포 + 발표 스크립트

## 작업 규칙
- 엔진을 수정하면 **반드시 `python test_engine.py`와 `python test_edge.py`를 먼저 통과**시킨 뒤 진행.
- UI 버그가 엔진 버그를 가리지 않도록, 새 계산 로직은 엔진+테스트에 먼저 반영.
- 배포 타깃은 **Streamlit Community Cloud**(무료, GitHub 연결). GitHub Pages는 정적이라 Python 앱 배포 불가.
