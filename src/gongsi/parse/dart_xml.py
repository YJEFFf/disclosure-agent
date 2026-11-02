"""DART 고유 XML(major · holding · periodic) 파서.

핵심: 표의 값은 <TD> 가 아니라 <TE>/<TU> 에 들어있고 기계가 읽는 코드가 붙어 있다.

    <TE ACODE="STK_RT">32.05</TE>
    <TU AUNIT="RPT_RSP_DT" AUNITVALUE="20250317">2025년 03월 17일</TU>

그래서 라벨 문자열을 찾아다닐 필요가 없다. ACODE 로 바로 집는다.
AUNITVALUE 는 이미 YYYYMMDD 로 정규화돼 있어 날짜 파싱도 불필요하다.

ACODE 사전은 아직 전량 확인 전이다. 그래서 이 모듈은
  (1) 문서의 ACODE/AUNIT 를 **전부** raw 로 담고
  (2) 확인된 코드만 공통 컬럼에 매핑한다.
새 코드가 나오면 아래 사전에 한 줄 추가하면 된다 (parse_event.py --acode-report 로 목록 확인).
"""
from __future__ import annotations

import re

# ── 공통 컬럼 매핑 ──────────────────────────────────────────────
# 값이 여러 코드에 흩어질 수 있어 amount 는 별도 우선순위 목록으로 처리한다.
ACODE_MAP: dict[str, str] = {
    # major · 자기주식 직접 취득 (삼성전자 20241118000171 실측)
    "ACQ_OSTK":      "취득예정주식_보통",
    "ACQ_ESTK":      "취득예정주식_기타",
    "ACQ_ESTK_PRC":  "취득예정금액_기타",
    "ACQ_PPS":       "reason",
    "ACQ_MTH":       "취득방법",
    "CI_BRK":        "위탁투자중개업자",
    "ATD_CNT":       "사외이사참석",
    "NATD_CNT":      "사외이사불참",
    # major · 자기주식 처분
    "SEL_OSTK":      "처분예정주식_보통",
    "SEL_ESTK":      "처분예정주식_기타",
    "SEL_OSTK_SPRC": "처분주식가격_보통",
    "SEL_PPS":       "reason",
    "SEL_MTH_MRK":   "처분방법_시장매도",
    # major · 자기주식 신탁계약 (체결/해지) ― ACQ_* 가 없고 SIG_* 를 쓴다
    "SIG_PPS":       "reason",
    "SIG_ORG":       "counterparty",         # 체결 서식의 계약 상대 증권사
    # 해지 서식은 접두사가 두 갈래다 — 금액만 CLN_, 나머지는 CNL_ (채굴 라벨로 확인)
    "CNL_ORG":       "counterparty",         # 4. 해지기관 (SIG_ORG 는 해지 서식에 없다)
    "CNL_PPS":       "reason",               # 3. 해지목적
    "CNL_BCN":       "신탁재산반환방법",         # 6. 해지 후 신탁재산의 반환방법
    "HLD_OSTK":      "보유자기주식_보통",
    "HLD_OSTK_RT":   "보유자기주식비율_보통",
    # major · 사채 발행 (전환사채 · 교환사채 · 상각형조건부자본증권 · 자본인정채무증권 공통)
    "PL_KND":        "사채종류",
    "PRFT_RATE":     "표면이자율",
    "LST_RTN_RT":    "만기이자율",
    "FND_USE1":      "자금용도_시설",
    "FND_USE2":      "자금용도_영업양수",
    "FND_USE3":      "자금용도_기타",
    "FND_USE_SQ":    "자금용도_채무상환",
    "FND_USE_RD":    "자금용도_운영",
    "FRN_AMT":       "외화금액",
    "EXC_RT":        "적용환율",
    "ISSU_AREA":     "발행지역",
    # major · 유상증자 / 무상증자 / 감자
    "CST_CNT":       "신주수_보통",
    "PST_CNT":       "신주수_기타",
    "CST_ISS_VAL":   "발행가_보통",
    "PST_ISS_VAL":   "발행가_기타",
    "FVAL":          "액면가",
    "BFR_CST_CNT":   "증자전주식수_보통",
    "NEW_ASN_CST":   "무상신주배정비율",
    "ROC_BAMT":      "감자전자본금",
    "ROC_AAMT":      "감자후자본금",
    "ROC_MTH":       "감자방법",
    "ROC_RSN":       "reason",
    # holding · 5% 보고 ― 요약정보(SUM_*)가 우리가 쓸 값이다. SPC_* 는 상세표라 첫 행만 잡힌다.
    #
    # ★ BMT = Before(직전 보고서) · TMT = This(이번 보고서). 2026-08-16 원문 실측으로
    #   확정했다. 그 전까지 SUM_BMT_* 를 '현재 보유'로, SUM_TMT_* 를 '의결권'으로 읽고
    #   있었다 — holding 1,083건 전부에 걸린 오류였고 표본 400건 중 **60%에서 직전≠이번**
    #   이었다. 즉 절반 넘게 옛 수치를 현재 보유로 답하고 있었다.
    #   근거 마크업(삼성E&A 20260303001699 · 블랙록):
    #       <TD>직전 보고서</TD><TE SUM_BMT_CNT>9,808,838</TE><TE SUM_BMT_RT>5.00</TE>
    #       <TD>이번 보고서</TD><TE SUM_TMT_CNT>7,724,495</TE><TE SUM_TMT_RT>3.94</TE>
    #   의결권은 별도 표(VR_*)이고, 표본의 78%가 '-' 로 비어 있다 — 비었으면 비운다.
    "IFR_NM":        "counterparty",         # 보고자(투자자)
    "CRP_NM":        "발행회사",
    "CRP_CD":        "종목코드",
    "SUM_TMT_CNT":   "보유주식등_합계",       # 이번 보고서 = 현재 보유
    "SUM_TMT_RT":    "보유비율",
    "SUM_BMT_CNT":   "직전보고서_보유주식등",
    "SUM_BMT_RT":    "직전보고서_보유비율",
    "VR_TMT_CNT":    "의결권있는주식수",
    "VR_TMT_RT":     "의결권비율",
    "SUM_CHN_RWN":   "보고사유",
    "FLT_STK_CNT":   "발행주식총수",
    # ★ 2026-09-02 (R71 · R11-028) — SPC_NM 이 사전 3종(채굴·수기·폴백) 모두에 없어
    #   컨텍스트에 코드명이 그대로 나갔다(함정 23 꼴 — 값 'Octave LifeSciences LLC' 가
    #   있는데 모델이 못 쓴다). 원문 대조(디앤디파마텍 20250109000306): 특별관계자 개요 ·
    #   종류별 보유내역 · 세부변동내역의 열 머리글이 전부 「성 명(명칭)」이다.
    #   보고자 행과 특별관계자 행이 **같은 코드**를 쓰므로(평평 사전엔 마지막 행만 남는다)
    #   「특별관계자 성명」 같은 해석을 얹지 않고 원문 열 이름 그대로 둔다.
    #   모집단: SPC_NM 은 대량보유 1,083건 전용 — 다른 유형과 충돌 없음(events 전수 확인).
    "SPC_NM":        "성명(명칭)",
    # periodic · 주주에 관한 사항
    "SH5_NM_T":      "주주명",
    "SH5_PER":       "지분율",
}

# amount 후보 코드 ― 앞에서부터 먼저 잡히는 것을 쓴다 (유형별로 다른 코드를 쓰므로 순서가 중요).
AMOUNT_CODES: tuple[str, ...] = (
    "DNM_SUM",        # 사채 권면총액 (CB·EB·조건부자본증권)
    "ACQ_OSTK_PRC",   # 자기주식 취득예정금액
    "SEL_OSTK_PRC",   # 자기주식 처분예정금액
    "SIG_PRC",        # 자기주식 신탁계약 **체결** 금액
    # 신탁계약 **해지**는 SIG_PRC 가 아예 없다. 해지 전 계약금액이 그 공시의 금액이다.
    # (2026-08-16 실측: 해지 43건 전부 amount=None 이었다. 추출률 표엔 100%로 적혀 있었다.)
    "CLN_PRC_PRE",    # 자기주식 신탁계약 해지 전 계약금액
    "ROC_BAMT",       # 감자 전 자본금
)

# 비율 후보 코드
RATIO_CODES: tuple[str, ...] = (
    "SUM_TMT_RT",     # 5% 보고 · **이번 보고서** 보유비율 (직전 SUM_BMT_RT 이 아니다)
    "STK_RT",         # 상세표 보유비율 (요약이 없을 때만)
)

# AUNIT → 공통 컬럼 (값은 AUNITVALUE 의 YYYYMMDD)
AUNIT_MAP: dict[str, str] = {
    "SIG_BGN":     "period_start",           # 신탁계약 기간
    "SIG_END":     "period_end",
    "PYM_DT":      "event_date",             # 유상증자 납입일
    "DRC_DT":      "이사회결의일",
    "LST_PLN_DT":  "상장예정일",
    "ACQ_BGN":     "period_start",
    "ACQ_END":     "period_end",
    "ACQ_DATE":    "event_date",            # 이사회 결의일
    "SEL_BGN":     "period_start",
    "SEL_END":     "period_end",
    "RPT_RSP_DT":  "보고의무발생일",
    "PERIODFROM":  "period_start",
    "PERIODTO":    "period_end",
}


def extract_codes(root) -> tuple[list[dict], list[dict]]:
    """문서 안의 모든 <TE>/<TU> 를 (코드, 값) 목록으로."""
    tes, tus = [], []
    for el in root.iter():
        tag = el.tag if isinstance(el.tag, str) else ""
        name = tag.rsplit("}", 1)[-1].upper()
        if name == "TE":
            code = el.get("ACODE")
            if code:
                tes.append({"code": code, "value": " ".join("".join(el.itertext()).split())})
        elif name == "TU":
            unit = el.get("AUNIT")
            if unit:
                tus.append({
                    "code": unit,
                    "value": el.get("AUNITVALUE"),
                    "text": " ".join("".join(el.itertext()).split()),
                })
    return tes, tus


def _to_int(value: str | None) -> int | None:
    if not value:
        return None
    digits = value.replace(",", "").strip()
    return int(digits) if digits.isdigit() else None


def _first(items: list[dict], code: str):
    for it in items:
        if it["code"] == code:
            return it.get("value")
    return None


def parse(root, event_type: str) -> dict:
    tes, tus = extract_codes(root)
    out: dict = {"event_type": event_type}

    for code, column in ACODE_MAP.items():
        value = _first(tes, code)
        if value not in (None, "", "-"):
            out[column] = value

    # amount ― 유형마다 쓰는 코드가 다르다. 우선순위대로 첫 유효값을 쓴다.
    for code in AMOUNT_CODES:
        amount = _to_int(_first(tes, code))
        if amount is not None:
            out["amount"] = amount
            out["amount_src"] = code
            break
    else:
        # 유상증자·무상증자는 금액 필드가 없다. 신주수 × 발행가 로 조달금액을 만든다.
        # (실측 검증: 1,940,200주 × 30,400원 = 58,982,080,000 = 자금용도 합계와 정확히 일치)
        total = 0
        for cnt_code, val_code in (("CST_CNT", "CST_ISS_VAL"), ("PST_CNT", "PST_ISS_VAL")):
            cnt, val = _to_int(_first(tes, cnt_code)), _to_int(_first(tes, val_code))
            if cnt and val:
                total += cnt * val
        if total:
            out["amount"] = total
            out["amount_src"] = "신주수×발행가"

    for code in RATIO_CODES:
        raw = _first(tes, code)
        if raw not in (None, "", "-"):
            try:
                out["amount_ratio"] = float(raw.replace(",", ""))
                break
            except ValueError:
                pass

    for code, column in AUNIT_MAP.items():
        value = _first(tus, code)
        if value and re.fullmatch(r"\d{8}", value):
            out[column] = f"{value[:4]}-{value[4:6]}-{value[6:]}"

    # 정정 헤더 ― major/holding/periodic 공통 양식
    text = " ".join(" ".join(root.itertext()).split())
    m = re.search(r'정정대상 공시서류의 최초제출일\s*:?\s*(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일', text)
    if m:
        out["원본제출일"] = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r'1\.\s*정정대상 공시서류\s*:?\s*(.*?)\s*2\.', text)
    if m:
        out["정정대상서류"] = m.group(1).strip()

    # 미확인 코드까지 전부 보존 ― 스키마 밖 질의는 LLM 이 raw 를 읽어 답한다
    out["raw_te"] = {t["code"]: t["value"] for t in tes}
    # ★ 2026-08-18 M-47. 전에는 `t["value"]`(=AUNITVALUE)만 담았다. **날짜에는 맞고
    #   나머지에는 틀린다.** 원문 셀은 이렇게 생겼다:
    #
    #       <TU AUNIT="ALL_BS_DT" AUNITVALUE="20250610">2025년 06월 10일</TU>   ← 값이 유용
    #       <TU AUNIT="ISSU_MTH"  AUNITVALUE="2">사모</TU>                      ← **값은 기계 코드**
    #
    #   함정 8 의 「날짜는 AUNITVALUE 가 이미 YYYYMMDD 다」는 **날짜 이야기**인데,
    #   그걸 TU 전체로 넓혀 읽는 바람에 제품이 「8. 사채발행방법 = **2**」라고 답했다
    #   (원문은 「사모」). 원문 기반 홀드아웃 RW-003·005·028·035 가 전부 이것이다.
    #   → 8자리 날짜면 AUNITVALUE, 아니면 **사람이 읽는 표시값**을 담는다.
    out["raw_tu"] = {
        t["code"]: (t["value"] if re.fullmatch(r"\d{8}", str(t["value"] or "")) else
                    (t.get("text") or t["value"]))
        for t in tus}
    # ★ 2026-09-01 (R43 규명 · R10-030) — 대량보유의 FLT_CRP_RLT(발행회사와의 관계)는
    #   **보고자 본인** 값(표지·보고자 상세)이 앞에, 특별관계자 60행이 뒤에 온다.
    #   위 dict 는 마지막 등장이 이겨서 **남의 관계**가 저장됐다 — holding 713건(65.8%)
    #   에서 첫값≠끝값(실물 최창걸: 「임원(미등기)」가 「계열회사등」으로 덮임).
    #   첫 등장을 보존한다. 이 코드는 holding 전용이다(비보유 등장 0건 실측)라
    #   유형 분기가 필요 없다. ★ IFR_RLT 는 고치지 않는다 — 행마다 다른 특별관계자의
    #   값이라 «본인» 스칼라가 존재하지 않는다(행 데이터는 holding_tables 버킷 몫).
    for _cells, _key in ((tus, "raw_tu"), (tes, "raw_te")):
        for _t in _cells:
            if _t["code"] == "FLT_CRP_RLT":
                _v = _t["value"]
                out[_key]["FLT_CRP_RLT"] = (
                    _v if re.fullmatch(r"\d{8}", str(_v or "")) else
                    (_t.get("text") or _v))
                break
    return out


def event_type_of(report_nm: str, doc_group: str) -> str:
    nm = report_nm.replace("[기재정정]", "")
    if doc_group == "holding":
        return "대량보유"
    # 자기주식 '취득'은 두 갈래다. 직접 취득은 ACQ_* 코드를 쓰지만
    # 신탁계약 체결/해지는 양식이 완전히 달라 ACQ_* 가 아예 없다. 섞으면 추출률이 반토막 난다.
    if "자기주식취득신탁계약" in nm or ("자기주식" in nm and "신탁" in nm):
        return "자기주식신탁계약해지" if "해지" in nm else "자기주식신탁계약체결"
    if "자기주식취득" in nm:
        return "자기주식취득"
    if "자기주식처분" in nm:
        return "자기주식처분"
    if "주요사항보고서" in nm:
        inner = re.search(r'주요사항보고서\((.*?)\)', nm)
        return inner.group(1) if inner else "주요사항기타"
    return nm
