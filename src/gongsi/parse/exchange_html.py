"""거래소공시(exchange) 파서.

exchange 만 포맷이 다르다. periodic/major/holding 은 <DOCUMENT> DART XML 이지만
exchange 는 <html> xhtml 이고, 값이 <TE ACODE>가 아니라 <td> 안 평문으로 들어있다.
그래서 라벨 기반으로 뽑는다.

문서 앞부분은 .xforms { ... } CSS 덩어리다. 마지막 '}' 이후부터가 본문.
"""
from __future__ import annotations

import re

_NUM = r'([\d,]+)'
_DATE = r'(\d{4}-\d{2}-\d{2})'

# 코스피(거래소)와 코스닥은 라벨이 미묘하게 다르다. 실측 확인:
#   코스피  "계약금액(원)"      "최근매출액(원)"   "매출액대비(%)"   "3. 계약상대"
#   코스닥  "계약금액 총액(원)"  "최근 매출액(원)"  "매출액 대비(%)"  "3. 계약상대방"
# 그래서 라벨 사이 공백과 '총액'·'방' 을 전부 선택적으로 둔다.
# ★ 2026-08-18 (M-64) — **라벨과 값 사이의 «콜론»을 안 봤다.**
#   표 서식은 「계약금액(원)  1,234」처럼 붙지만, **서술형 본문**은
#   「계약금액 총액(원) **:** 43,867,615,524,480」처럼 콜론을 쓴다.
#   그 한 글자 때문에 에코프로비엠 20231201900749 의 **43.8조** 계약이 `amount` 에
#   안 들어갔고, 답변에는 본문 주입으로 닿지만 **`[코드 계산]` 합계·기업별 비교에서
#   통째로 빠졌다** (대회 참고 질의 ③ 「A와 B 중 설비투자가 더 큰 곳은?」 계열이 위험하다).
#   실측: `amount` 가 비었는데 본문에 금액이 문장으로 있는 공시는 **68건 중 1건**뿐이라
#   발화가 극히 드물다 — 그래서 조용히 남아 있었다.
_COLON        = r'[:：]?\s*'
_AMT_CONTRACT = r'계약금액\s*(?:총액)?\s*(?:\(원\))?\s*' + _COLON + _NUM
_AMT_TERM     = r'해지금액\s*(?:총액)?\s*(?:\(원\))?\s*' + _COLON + _NUM
_REVENUE      = r'최근\s*매출액\s*\(원\)\s*' + _COLON + _NUM
_RATIO        = r'매출액\s*대비\s*\(%\)\s*' + _COLON + r'([\d.]+)'

# ★ 2026-08-18 (M-65) — **서술형 본문의 금액이 `amount` 에 안 들어간다.**
#   `투자판단` 297건은 SPECS 에 `amount` 가 아예 없다(계약명·reason·event_date 뿐).
#   그런데 그중 **27건은 본문에 금액을 «필드처럼» 적는다** — 「계약금액 : 4,082,377,000,000원」.
#   답변에는 본문 주입으로 닿지만 **`[코드 계산]` 합계·기업별 비교에서 통째로 빠진다.**
#   (M-64 가 고친 43.8조는 제목에 「공급계약」이 있어 공급계약체결로 라우팅된 **한 사례**였다.
#    같은 성격의 금액이 제목에 그 낱말이 없다는 이유로 남아 있었다 — 심문 ❺.)
#
#   ★ 두 가지를 «반드시» 배제한다.
#     ① 각주의 총액 — 「**총** 계약금액 3조 6,147억원 **중 당사분(3%)**에 해당하는 금액임」.
#        이걸 집으면 당사분 자리에 총액이 들어간다(M-20 의 거울상). 실측 11건.
#        → «콜론이 있는 필드 꼴»만 받고, 앞 12자에 「총」이 있으면 버린다.
#     ② 「조건부」의 「조」 — 「계약금액 4,150,000,000 **조**건부 …」를 단위로 읽으면 10^12배가 된다.
#        실측 37건이 이 꼴이다. → 단위 뒤에 **원**이 오거나 경계여야 단위로 인정한다.
_NARR_UNIT = {"조": 10 ** 12, "억": 10 ** 8, "만": 10 ** 4, "천": 10 ** 3}
_NARR_AMT = re.compile(r'(계약금액|취득금액|투자금액|해지금액)\s*[:：]\s*'
                       r'([\d,]+(?:\.\d+)?)\s*(?:(조|억|만|천)\s*원?|원)')


def narrative_amount(text: str) -> int | None:
    """서술형 본문의 「계약금액 : N원 / N억원」을 원 단위로. 각주의 «총액»은 잡지 않는다."""
    for m in _NARR_AMT.finditer(text):
        if "총" in text[max(0, m.start() - 12): m.start()]:
            continue
        try:
            head = float(m.group(2).replace(",", ""))
        except ValueError:
            continue
        return int(head * _NARR_UNIT[m.group(3)]) if m.group(3) else int(head)
    return None


# 이벤트 유형별 필드 명세. (출력키, 정규식) ― 정규식은 첫 그룹만 사용한다.
SPECS: dict[str, list[tuple[str, str]]] = {
    "공급계약체결": [
        ("구분",         r'1\.\s*판매ㆍ공급계약 구분\s*(.*?)\s*-\s*체결계약명'),
        # 코스피는 '- 체결계약명', 코스닥 자율공시는 '1. 판매ㆍ공급계약 내용' 이 계약명이다
        # ★ 2026-09-01 (R43 규명 · R10-008) — 자율공시 «구분+세부내용» 서식이 하나 더 있다:
        #   「1. 판매ㆍ공급계약 구분 [공사수주] - 세부내용 [계약명] 2. 계약내역」.
        #   이 라벨을 몰라 공급계약 58건의 계약명이 결측(함정 40 「해지계약명」과 동형) —
        #   그 탓에 지목어가 못 물고 M-44 보강이 딴 계약을 앞세워 각주 주입이 소진됐다.
        ("계약명",       (r'-\s*체결계약명\s*(.*?)\s*2\.\s*계약내역',
                          r'1\.\s*판매ㆍ공급계약\s*내용\s*(.*?)\s*2\.\s*계약내역',
                          r'-\s*세부내용\s*(.*?)\s*2\.\s*계약내역')),
        ("amount",       _AMT_CONTRACT),
        ("최근매출액",   _REVENUE),
        ("amount_ratio", _RATIO),
        ("대규모법인",   r'대규모법인여부\s*(\S+)'),
        ("counterparty", r'3\.\s*계약상대방?\s*(.*?)\s*-\s*(?:최근|회사와의)'),
        ("지역",         r'4\.\s*판매ㆍ공급지역\s*(.*?)\s*5\.\s*계약기간'),
        ("period_start", r'5\.\s*계약기간\s*시작일\s*' + _DATE),
        ("period_end",   r'5\.\s*계약기간.*?종료일\s*' + _DATE),
        # ★ 2026-09-05 (R52 · 심문 3차 ④) — 자율공시 서식은 「계약(수주)**일**」로 적는다
        #   («일자» 아님). 이 한 글자 차이로 원공시 34건 중 29건의 체결일이 비어 있었다
        #   (event_date=None → 발생일 기준 집계·연도 필터가 «미상»으로 밀었다). 전수 채굴로 확인.
        ("event_date",   r'계약\(수주\)일자?\s*' + _DATE),
        ("유보사유",     r'유보사유\s*(.*?)\s*유보기한'),
    ],
    "공급계약해지": [
        # ★ 2026-08-19 (M-92) — **라벨은 「세부물건」이 아니라 「해지계약명」이다.**
        #   해지 21건을 원문에서 전수 채굴한 결과 **19건이 「해지계약명」**, 1건만 「세부물건」,
        #   1건은 패턴 불일치였다. 그래서 `계약명` 이 **21건 중 20건(95.2%)에서 비어 있었다.**
        #   계약명은 체결↔해지를 잇는 열쇠이고(주최측 참고질의 #5), 실측 H6-028 에서
        #   「Salamanca ULSD Project」가 원문엔 있는데 DB·근거에 한 글자도 없어
        #   제품이 **다른 회사의 해지 공시(SONATRACH)**를 답했다.
        ("구분",         r'1\.\s*판매ㆍ공급계약 해지 구분\s*(.*?)\s*-\s*(?:해지계약명|세부물건)'),
        ("계약명",       r'-\s*(?:해지계약명|세부물건)\s*(.*?)\s*2\.\s*해지내역'),
        ("amount",       _AMT_TERM),
        ("최근매출액",   _REVENUE),
        ("amount_ratio", _RATIO),
        ("대규모법인",   r'대규모법인여부\s*(\S+)'),
        ("counterparty", r'3\.\s*계약상대방?\s*(.*?)\s*-\s*(?:최근|회사와의)'),
        ("period_start", r'4\.\s*계약기간\s*시작일\s*' + _DATE),
        ("period_end",   r'4\.\s*계약기간.*?종료일\s*' + _DATE),
        ("reason",       r'5\.\s*해지 주요사유\s*(.*?)\s*6\.\s*해지일자'),
        ("event_date",   r'6\.\s*해지일자\s*' + _DATE),
        # ★ 2026-08-19 (M-110) — **해지 서식에도 「7. 공시유보 관련내용」이 있다.**
        #   체결 명세에만 있고 해지에는 없어서, 현대건설 NEOM 해지(20260313801644)의
        #   「유보사유 경영상 비밀유지」가 DB 에 안 들어갔다. 홀드아웃06 H6-025 의 gold 다
        #   («왜 금액이 없는가»가 정답 — 평가지표 7).
        #   거래소 1,469건 전수 대조: 원문에 유보사유가 있는 것 120건 · DB 에 120건 ·
        #   **놓친 것은 이 1건뿐**이다. 서식 완결성 보완이지 규칙 확장이 아니다.
        ("유보사유",     r'유보사유\s*(.*?)\s*유보기한'),
    ],
    "신규시설투자": [
        ("구분",         r'1\.\s*투자구분\s*(.*?)\s*-\s*투자대상'),
        ("계약명",       r'-\s*투자대상\s*(.*?)\s*2\.\s*투자내역'),
        ("amount",       r'투자금액\s*\(원\)\s*' + _COLON + _NUM),
        ("자기자본",     r'자기자본\s*\(원\)\s*' + _COLON + _NUM),
        ("amount_ratio", r'자기자본대비\s*\(%\)\s*' + _COLON + r'([\d.]+)'),
        ("대규모법인",   r'대규모법인여부\s*(\S+)'),
        ("reason",       r'3\.\s*투자목적\s*(.*?)\s*4\.\s*투자기간'),
        ("period_start", r'4\.\s*투자기간\s*시작일\s*' + _DATE),
        ("period_end",   r'4\.\s*투자기간.*?종료일\s*' + _DATE),
        ("event_date",   r'5\.\s*이사회결의일\(결정일\)\s*' + _DATE),
    ],
    "투자판단": [
        ("계약명",       r'1\.\s*제목\s*(.*?)\s*2\.\s*주요내용'),
        ("reason",       r'2\.\s*주요내용\s*(.*?)\s*3\.\s*이사회결의일'),
        ("event_date",   r'이사회결의일\(결정일\)(?:\s*또는 사실확인일)?\s*' + _DATE),
    ],
}

# report_nm → 우리 event_type
def event_type_of(report_nm: str) -> str | None:
    nm = report_nm.replace("[기재정정]", "")
    if "해지" in nm:
        return "공급계약해지"
    if "단일판매" in nm or "공급계약" in nm:
        return "공급계약체결"
    if "시설투자" in nm:
        return "신규시설투자"
    if "투자판단" in nm:
        return "투자판단"
    return None


def body_text(xml_text: str, root=None) -> str:
    """CSS 헤더를 제거한 본문 텍스트. root 는 loader.parse_doc 이 준 lxml 노드."""
    if root is not None:
        text = " ".join(" ".join(root.itertext()).split())
    else:
        text = " ".join(xml_text.split())
    close = list(re.finditer(r'\}', text))
    return text[close[-1].end():].strip() if close else text


def _to_int(value: str | None) -> int | None:
    if not value:
        return None
    digits = value.replace(",", "")
    return int(digits) if digits.isdigit() else None


# 본문 시작 앵커 = 각 서식의 1번 항목. '\d. 계약내역' + marks[-1] 방식은
# 문서 끝 각주("2.계약내역 내 '최근 매출액'은…")를 잡아 body 가 각주 꼬리가 되고,
# 본문 검색 실패 → 전문 폴백 → 정정 표의 옛 값을 집었다 (레인보우로보틱스
# 20241205900548 에서 4,250,000,000 대신 정정전 4,150,000,000 추출, 실측).
# '구분' 은 코스피, 자율공시(코스닥)는 '내용' ― 레인보우로보틱스 20241205900548 실측
_BODY_MARK = re.compile(r'1\.\s*(?:판매ㆍ공급계약(?:\s*해지)?\s*(?:구분|내용)|투자구분|제목)')


def parse(text: str, event_type: str) -> dict:
    """본문 텍스트 → 필드 딕셔너리. 못 찾은 필드는 None 으로 둔다(0 으로 채우지 않는다).

    정정공시는 앞쪽에 '정정전/정정후' 표가 붙는다. 거기서 값을 집으면 옛 값을 답하게 되므로
    본문 시작 앵커(1. 판매ㆍ공급계약 구분 등) 이후를 먼저 본다. 앵커는 정정 헤더에 없다.
    """
    out: dict = {"event_type": event_type}
    marks = list(_BODY_MARK.finditer(text))
    body = text[marks[0].start():] if marks else text

    # 정정공시 헤더(정정사유·정정전/정정후 표)를 통째로 보존한다.
    # "정정 사유는?" · "정정 전 값은?" 질의의 유일한 원천이다 (TC 군 실측).
    if marks and "정정" in text[: marks[0].start()]:
        head = text[: marks[0].start()]
        i = head.find("정정")
        # 700자로 잘랐더니 정정후 값 직전에 끊겼다 (NAVER 거래종결일, TC-001 실측) → 1800
        out["정정헤더"] = " ".join(head[i:].split())[:1800]
    for key, pattern in SPECS.get(event_type, []):
        pats = pattern if isinstance(pattern, tuple) else (pattern,)
        m = None
        for p in pats:                              # 양식 변형: 앞 패턴부터 순서대로
            m = re.search(p, body, re.S) or re.search(p, text, re.S)
            if m:
                break
        out[key] = m.group(1).strip() if m else None
    out["amount"] = _to_int(out.get("amount"))
    # 표에서 못 뽑았거나 «억 앞 숫자»만 뽑혔으면 서술형 필드 쪽이 옳다 (M-65).
    _n = narrative_amount(body) or narrative_amount(text)
    if _n is not None and (out["amount"] is None or out["amount"] < _n / 1000):
        out["amount"] = _n
    for key in ("최근매출액", "자기자본"):
        if key in out:
            out[key] = _to_int(out[key])
    if out.get("amount_ratio"):
        try:
            out["amount_ratio"] = float(out["amount_ratio"])
        except ValueError:
            out["amount_ratio"] = None

    # ※ 관련공시 ― 정정·해지 체인의 연결 키. 코퍼스 밖 날짜도 그대로 남긴다.
    related = []
    if "관련공시" in text:
        tail = text.split("관련공시")[-1]
        for dt, nm in re.findall(r'(\d{4}-\d{2}-\d{2})\s*([^\d]{2,40}?)(?=\s*\d{4}-\d{2}-\d{2}|\s*$)', tail):
            related.append({"related_dt": dt, "related_nm": " ".join(nm.split())})
    out["related"] = related
    return out
