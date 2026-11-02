"""대량보유(holding) 공시의 «표» 사전 추출 — `holding_tables.jsonl`.

★ 왜 필요한가 — **`events.raw` 의 `raw_te` 는 «ACODE → 값» 평평한 dict 이라
  여러 행짜리 표가 «한 행»으로 접힌다. 그것도 실재하지 않는 뒤섞인 한 행으로.**

  ── 착수 지시의 전제 ① 은 실측으로 **기각**했다 (규칙 0-1 「추측하지 말고 실측하라」).
     「특별관계자 수가 raw 에 없다」고 들었으나, 고려아연 20241111000449 의 raw 에는
     `THS_RPT_CNT: "18"` 이 **있다**(전수 1,083/1,083 존재). 라벨 사전에도 있다
     (`MINED_LABELS[("대량보유","THS_RPT_CNT")] = "이번보고서 특별관계자수"`).
     요약 표의 값은 **1,083건 전부 이미 raw 에 있다** — 값이 없는 게 아니었다.
     그래서 `summary_rows` 가 새로 주는 것은 «값»이 아니라 **«인접성»**이다:
        이번보고서 | 2024년 11월 11일 | (주)영풍 | 18 | 8,246,783 | 39.83 | … | 20,703,283
     한 줄 안에서 보고자·특별관계자수·주식수·비율·발행주식총수가 **서로 붙어 있고**,
     직전/이번/증감 세 행이 나란히 서서 대조가 된다. 덤으로 표 밖 주석이 **53건**
     실린다 — 「* 특별관계자수는 보고자를 제외한 수」(한화에어로 20260108000435)처럼
     **그 숫자의 «정의»를 말하는 문장**이라 오답을 막는다.
     ★ 값이 이미 있으므로 `summary_rows` 는 **보조**다. 여기에 큰 기대를 걸지 마라.

  ── 전제 ② 는 **확증됐고, 실은 더 나쁘다.**
     「제2부 2. 나. 계약 내용」 표는 담보·질권·공탁 내역이 여러 행이다. 그런데 `raw_te`
     가 코드→값 평평 dict 이라 **같은 코드를 쓰는 뒷행이 앞행을 덮어써** 한 행만 남고,
     남은 한 행조차 **여러 행에서 조각조각 온 «실재하지 않는 행»**이다.

       삼성전자 20230120000563 원문 1행:
         홍라희 | 특수관계인 | … | 24,123,124 | 서울서부지방법원 | 공탁 |
         2021년 04월 23일 | 공탁해지시까지 | 0.40 | 상속세 연부연납 납세담보
       같은 문서의 raw_te:
         SPC_NM=박학규 · STK_CNT=- · TRD_RVL=한국투자증권 · TRD_KND=담보계약 · TRD_RMK=질권
       → `'24,123,124' in raw` = **False**, `'상속세' in raw` = **False**.
         그리고 raw 에 남은 다섯 값은 **원문 어느 한 행도 아니다.**

     전수 실측 (계약 데이터행이 있는 509문서):
       · 2행 이상인 문서            **461 (90.6%)**
       · raw 평평값이 실재하는 한 행   96 (18.9%)
       · **raw 평평값이 여러 행의 뒤섞임  413 (81.1%)**   ← 여기가 진짜 결함

     **틀린 단언은 «없는 것»보다 나쁘다**(함정 62). 「담보 상대방이 누구냐」에
     엉뚱한 사람과 엉뚱한 은행을 짝지어 답할 수 있는 자리다.

  **함정 26(거래소 서술형 본문이 DB 에 없다) · M-45(major 서술 항목)의 holding 판**이다.
  「한쪽에만 있는 처방」(함정 23) 이 또 한 번 나온 자리다.

★ 왜 질의 시점 원문 읽기가 아니라 «사전 추출»인가 — **배송 트리**다.
  런타임이 여는 원문은 `exchange` 뿐이고(`stage_deploy.CORPUS_RAW_GROUPS`),
  holding 원문(1,083문서)을 질의 시점에 열면 배포본에 그 원문을 통째로 실어야 한다.
  표지 표(`run_build_cover.py`)·major 서술(`run_build_majornotes.py`) 과 같은 길을 쓴다.

★ 어떻게 고르나 — **`<TITLE>` 로 고른다. 표를 전부 담지 않는다.**
  전부 담으면 컨텍스트 예산을 먹는다(세부변동내역만 100행이 넘는 문서가 있다).
  문서 순서상 «그 표 바로 앞의 TITLE» 을 보고 두 종류만 담는다:
     summary_rows  ← 「보유주식등의 수 및 보유비율」   (전수 1,083건이 이 제목을 갖는다)
     contract_rows ← 「계약 내용」/「보유주식등에 관한 계약」 (711건. 나머지 372건은
                      단순투자 약식서식이라 계약 절 자체가 없다 — 실측 TITLE 전수 집계)

★ ROWSPAN 을 지킨다(함정 24). 요약 표의 머리글은
     <TH ROWSPAN="2">보고서작성기준일</TH> … <TH COLSPAN="2">보고자</TH>
  라서, 무시하면 둘째 머리글 행이 왼쪽으로 밀려 「특별관계자수」가 다른 열 이름이 된다.
  `mine_acode_labels._grid()` 와 같은 격자 복원을 쓴다(값은 `<TE>`/`<TU>`, 라벨은 `<TD>/<TH>`
  — 함정 8). 격자 위에서 «그 행에 걸쳐 있는 모든 칸»을 열 순서로 ` | ` 로 잇고,
  COLSPAN 자리는 **빈 칸으로 채운다** — 안 채우면 「증 감」(COLSPAN=4) 행의 숫자들이
  머리글보다 네 칸 왼쪽으로 밀린다.

★ 「TABLE 안의 TABLE 은 건너뛴다」로 짜면 **정정신고본이 통째로 빠진다**(실측 1건).
  정정본은 본문 전체가 바깥 「정정전/정정후」 표 안에 들어 있다.
  → «내가 이미 담은 표»의 속인 것만 건너뛴다. 그 전에는 커버리지가 1,082/1,083 이었다.

★ 실측 결과 (2026-08-30, 전수 1,083건 · 4.0초)
    summary_rows  **1,083건 (100.0%)** · 평균 373자
    contract_rows **711건 (65.7%)** · 평균 865자   (나머지 372건은 약식서식 — 계약 절 없음)
    산출 파일 1,770KB
  열 어긋남 전수 검사 — 「특별관계자수·주식등의 수(주)·비율(%)·본인 성명」 네 칸의 값이
  그 표 안의 `THS_*` ACODE 값과 같은가: **1,082/1,082 일치 · 어긋남 0**
  (1건은 그 표에 ACODE 가 아예 없는 서식이라 대조 불가 — 두산에너빌리티 20250514001078).
  계약 표 1행 대조(6칸 × 467문서): 어긋남 0 (12건은 300자 절단으로 «비고» 꼬리가 잘린 것).

산출: `03_가공/db/holding_tables.jsonl`
      {"rcept_no": …, "summary_rows": [...], "contract_rows": [...]}
      한 문서당 각 종류 최대 12행 · 행당 300자.

실행:  export GONGSI_DATA_ROOT=".../02_데이터" && python3 scripts/run_build_holdingtables.py
"""
from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gongsi.io import loader  # noqa: E402

MAX_ROWS = 12       # 문서당 각 종류 최대 행수
MAX_ROW_CHARS = 300  # 행당 최대 글자수

# 표를 고르는 «앞 제목» 규칙. 제목은 「3. 보유주식등의 수 및 보유비율」·「나. 계약 내용」처럼
# 번호가 서식마다 다르므로(일반서식 2., 약식 제4부 1.) **번호를 보지 않는다.**
RE_SUMMARY = re.compile(r"보유주식등의\s*수\s*및\s*보유비율")
RE_CONTRACT = re.compile(r"계약\s*내용|보유주식등에\s*관한\s*계약")
# ★ 2026-08-31 (R8-057 · R8-060) — 두 표가 스토어에 **통째로** 없었다.
#   원문에는 있고 답이 «한 행»인데, 그 행이 컨텍스트에 한 글자도 안 실렸다.
#     다. 주요계약이 담보계약인 경우 추가 기재사항
#       연번 | 주식등의 수 | **대출금액** | **채무자** | 이자율 | 담보유지비율 | 기타
#       2 | 1,175,647 | 250,000,000,000 | Polish Company Limited | - | - | 주1)
#     제2부 1. 보고자 및 특별관계자의 주식등의 종류별 보유내역
#       **특별관계자** | **국민연금기금** | 219-82-01593 | 50,356,245 | … | 7.86
#   앞의 것은 「계약 내용」 표의 **오른쪽 이어지는 표**라 계약 표만 담으면 반쪽이고,
#   뒤의 것은 「보고자냐 특별관계자냐」를 가르는 **유일한** 자리다(요약 표에는 없다).
#   전수 실측: coll **711건(65.7%)** · bytype **1,083건(100%)** · 기존 세 버킷 손실 **0**.
RE_COLL = re.compile(r"담보계약인\s*경우\s*추가\s*기재|담보계약.{0,6}추가\s*기재사항")
RE_BYTYPE = re.compile(r"주식등의\s*종류별\s*보유내역")
# ★ 2026-08-30 (R7-057·058) — 「취득에 필요한 자금등의 조성내역」·「주식등의 세부변동내역」
#   표도 `raw_te` 에 안 담긴다. 「자기자금 5,004,607,600 · 차입금 - · 계 5,004,607,600」과
#   세부변동 「41,103주 × 121,757원」이 그 자리다. 같은 스토어에 함께 담는다.
#   ※ 표 직전 TITLE 이 하위 제목(「(1) 취득자금등의 개요」)으로 갱신되므로 그것도 받는다.
# ★ 심문 13차 — 한 버킷에 섞어 담고 `[:10]` 으로 자르는 바람에 「자기자금·차입금·취득자금」이
#   있는 490문서 중 **49건(10%)이 겨냥한 값을 통째로 잃었다**. 「발행회사에 관한 사항」은
#   블록 머리글(「취득 자금 조성·세부 변동」)과도 맞지 않아 **따로 담는다**.
#   ※ 「특별관계자에 관한 사항」은 한 건도 안 걸리는 죽은 항이라 뺐다.
RE_FUND = re.compile(r"자금등의\s*조성내역|취득에\s*필요한\s*자금|세부\s*변동\s*내역|"
                     r"주식등의\s*세부변동|취득자금등의\s*개요|차입금의\s*상세내역|"
                     # ★ 2026-08-30 (R7-056) — 「보고자에 대한 구체적 사항」 표도 필요하다.
                     #   보고자가 **여럿일 때 한 명만** raw 에 담긴다(「장세준」은 있고
                     #   「김기호」는 없다 — 함정 30 의 «첫 행만» 계열).
                     r"보고자에\s*대한\s*구체적")
# ★ 「가. 보고자」 표(R7-056 의 김기호)는 **추가하지 않았다** — 넣어 보니 `taken` 로직과
#   얽혀 이미 잡히던 자금조성·세부변동 표(R7-057·058)가 함께 사라졌다. 1문항을 얻으려다
#   2문항을 잃는다(함정 49·70). 필요해지면 표 선택 로직부터 다시 봐야 한다.

# 값이 없는 행(구분선·전부 '-'·빈칸)은 버린다. 다만 「해당사항 없음」류는 **값이다**(평가지표 7).
RE_EMPTY_ROW = re.compile(r"^[\s|\-–—ㆍ·:]*$")

# ★ 2026-08-31 (R9-021·022·023·025) — **표 밖 `<P>` 각주가 통째로 빠져 있었다.**
#   대량보유 보고의 답이 표가 아니라 「주1) …」 문단에 사는 경우가 많다. 실측:
#     레인보우로보틱스 20250106000417 — gold 3,935,814 / 67,956 / 4,581,865 가
#     각주에 8·2·4회 있는데 **스토어 다섯 버킷에는 0·0·1회**.
#   모집단(내가 원문 전수로 다시 셈): 각주 보유 문서 **263 / 1,083 (24.3%)** ·
#   문단 **945개** · 문단 길이 중앙값 113자 · 90%분위 342 · **최대 18,876자**.
#   ★★ 통째로 실으면 안 된다 — 한 문서의 각주 총량이 최대 113,888자로
#     `CTX_CHARS_MAX`(18,000)를 혼자 먹는다(함정 49. 오늘 `&` 수리가 정확히 그래서 기각됐다).
#     → **문단 단위로 담고 문단마다 상한**을 건다. 600자면 문단의 **96.8%가 무손실**이고
#       총량 248,654 → 141,625자(57%)로 꼬리가 사라진다.
#   ★ 정규식을 두 번 고쳤다. 둘 다 실측으로 잡았다:
#     ① 「주N)」로만 잡으면 **번호 없는 「주) 상기의 증감주식 등의 내역은…」을 놓친다.**
#     ② 그렇다고 `주\s*\d*\s*\)` 로 열면 **「(주)영풍」의 `주)` 에 걸린다** — 회사명
#        약칭이다. 그래서 문단이 945 → **2,024** 로 뛰었다(대부분 회사명).
#     → `(?<!\()` 로 괄호를 배제한다. 실측 **1,371문단 · 339문서(31.3%)**.
#   ★★ 심문 17차 — `(?<!\()` 가 「(주)영풍」과 **함께 진짜 각주 211개를 버렸다**
#     (문서 61개 · 그중 **37문서는 각주가 한 줄도 안 남는다**). 실례:
#     「(주2) 2024년 9월 30일 보고자는 500,000주의 추가 신주발행을 진행하였음」.
#     채점 모집단에서 gold 손해 1건(R7-055)이 확인됐다.
#     → **숫자가 있으면 괄호를 허용**하고, 번호 없는 「주)」에만 괄호 배제를 건다.
RE_NOTE = re.compile(r"주\s*\d+\s*\)|(?<!\()주\s*\)")
#   ★★ 상한을 600 → 2,000 으로 올렸다. **600 은 정답을 잘랐다** — R9-022 의 근거 문단이
#      1,750자이고 gold(`67,956`·`62,835`·`68,719`)가 오프셋 **1,112~1,270** 에 있다.
#      2,000 이면 걸리는 문단이 1,371 중 **32(2.3%)** 뿐이다(최대 33,099자는 여전히 자른다).
#      길이가 늘어난 만큼은 **소비자가 «질문 낱말 주변 창»으로 다시 줄인다** — 함정 49.
NOTE_CHARS_MAX = 2000
NOTE_ROWS_MAX = 12          # 문서당 문단 수 상한 (중앙값 3 · 최대 32)
_CELL_TAGS = {"TD", "TE", "TU", "TH"}


def _tag(el) -> str:
    return (el.tag if isinstance(el.tag, str) else "").rsplit("}", 1)[-1].upper()


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace(" ", " ")).strip()


def _span(el, attr: str) -> int:
    try:
        return max(1, int(el.get(attr) or el.get(attr.lower()) or 1))
    except (TypeError, ValueError):
        return 1


def _is_own(el, table) -> bool:
    """el 이 «이 표»의 것인가 — 사이에 다른 TABLE 이 끼면 속 표의 것이다."""
    for a in el.iterancestors():
        if a is table:
            return True
        if _tag(a) == "TABLE":
            return False
    return False


def grid_rows(table) -> list[str]:
    """<TABLE> 을 격자로 복원해 «행 문자열» 목록으로.

    ROWSPAN/COLSPAN 을 지킨다(함정 24) — 안 지키면 열이 왼쪽으로 밀린다.
    세로로 걸친 칸은 «걸친 모든 행»에 되풀이해 넣는다. 되풀이가 지저분해 보여도
    그래야 각 행이 «자기 자리»의 값을 갖는다.
    """
    # ① 격자 좌표 계산.
    #    ★ `table.iter()` 는 **속 표의 TR 까지** 끌고 온다 — 그대로 세면 두 표가 한 격자에
    #      뒤섞인다. 「나와 이 TR 사이에 다른 TABLE 이 있으면 남의 행」으로 걸러 낸다.
    cells: list[dict] = []
    occupied: set[tuple[int, int]] = set()
    r = 0
    for tr in table.iter():
        if _tag(tr) != "TR" or not _is_own(tr, table):
            continue
        c = 0
        for el in tr:
            kind = _tag(el)
            if kind not in ("TD", "TH", "TE", "TU"):
                continue
            while (r, c) in occupied:
                c += 1
            cs, rs = _span(el, "COLSPAN"), _span(el, "ROWSPAN")
            text = _norm("".join(el.itertext()))
            cells.append({"r": r, "c": c, "cs": cs, "rs": rs, "text": text})
            for dr in range(rs):
                for dc in range(cs):
                    occupied.add((r + dr, c + dc))
            c += cs
        r += 1
    if not cells:
        return []

    # ② 행별로 «그 행에 걸쳐 있는» 칸을 모아 열 순서로 잇는다.
    #    가로로 걸친 칸(COLSPAN)은 **빈 칸으로 자리를 채운다** — 안 채우면
    #    「증 감」(COLSPAN=4) 행에서 뒤의 숫자들이 머리글보다 네 칸 왼쪽으로 밀린다.
    by_row: dict[int, list[tuple[int, str, int]]] = collections.defaultdict(list)
    for x in cells:
        for dr in range(x["rs"]):
            by_row[x["r"] + dr].append((x["c"], x["text"], x["cs"]))

    out: list[str] = []
    for ri in sorted(by_row):
        parts: list[str] = []
        for _c, text, cs in sorted(by_row[ri], key=lambda p: p[0]):
            parts.append(text)
            parts.extend([""] * (cs - 1))
        line = re.sub(r" +", " ", " | ".join(parts)).rstrip(" |")
        if RE_EMPTY_ROW.match(line):
            continue
        out.append(line[:MAX_ROW_CHARS])
    return out


def tables_of(file_path: str):
    """원문 → (summary, contract, fund, coll, bytype, note). 자르는 규칙의 «유일한» 자리."""
    try:
        roots = loader.parse_doc(file_path)
    except Exception:                                          # noqa: BLE001
        return [], [], [], [], [], []

    summary: list[str] = []
    contract: list[str] = []
    fund: list[str] = []
    coll: list[str] = []
    bytype: list[str] = []
    note: list[str] = []
    for _name, root in roots:
        # ★ 각주는 «셀 밖»의 `<P>` 다. 「TABLE 밖」으로 재도 결과가 거의 같지만
        #   (문서 262 ↔ 263 · 문단 940 ↔ 945) 정정신고본은 본문 전체가 바깥 표 안에
        #   들어가므로 **셀 기준**이 안전하다(위 `taken` 주석과 같은 이유).
        for _el in root.iter("P"):
            if len(note) >= NOTE_ROWS_MAX:
                break
            if _CELL_TAGS & {a.tag for a in _el.iterancestors()
                             if isinstance(a.tag, str)}:
                continue
            _t = _norm("".join(_el.itertext()))
            if not _t or not RE_NOTE.search(_t):
                continue
            if len(_t) > NOTE_CHARS_MAX:
                _t = _t[:NOTE_CHARS_MAX] + "…"
            if _t not in note:
                note.append(_t)
        title = ""
        taken: list = []
        for el in root.iter():
            kind = _tag(el)
            if kind == "TITLE":
                title = _norm("".join(el.itertext()))
                continue
            if kind != "TABLE":
                continue
            # ★ 「TABLE 안의 TABLE 은 건너뛴다」로 짜면 **정정신고본이 통째로 빠진다** —
            #   정정본은 본문 전체가 바깥 「정정전/정정후」 표 안에 들어 있다
            #   (실측: 에코프로비엠 20231229000588 — 실표 27개가 전부 ancestor=TABLE).
            #   그래서 «내가 이미 담은 표»의 속인 것만 건너뛴다.
            if any(a is t for a in el.iterancestors() for t in taken):
                continue
            # ★ 새 둘을 «먼저» 본다 — 「담보계약인 경우 추가 기재사항」의 제목에는
            #   「계약」이 들어 있어 `RE_CONTRACT` 가 먼저 먹으면 계약 표를 밀어낸다.
            if RE_COLL.search(title):
                bucket = coll
            elif RE_BYTYPE.search(title):
                bucket = bytype
            elif RE_SUMMARY.search(title):
                bucket = summary
            elif RE_CONTRACT.search(title):
                bucket = contract
            elif RE_FUND.search(title):
                bucket = fund
            else:
                continue
            taken.append(el)
            for line in grid_rows(el):
                if line not in bucket and len(bucket) < MAX_ROWS:
                    bucket.append(line)
    return (summary[:MAX_ROWS], contract[:MAX_ROWS], fund[:MAX_ROWS],
            coll[:MAX_ROWS], bytype[:MAX_ROWS], note[:NOTE_ROWS_MAX])


def main() -> int:
    corpus = loader.load_corpus()
    rows = [m for m in corpus.manifest if m.get("doc_group") == "holding"]
    dst = Path(loader.DERIVED_DIR) / "db" / "holding_tables.jsonl"
    dst.parent.mkdir(parents=True, exist_ok=True)

    n_doc = 0
    n = {"summary": 0, "contract": 0, "fund": 0, "coll": 0, "bytype": 0, "note": 0}
    chars_sum = chars_ctr = 0
    with open(dst, "w", encoding="utf-8") as fh:
        for i, m in enumerate(rows, 1):
            s, c, f, cl, bt, nt = tables_of(m["file_path"])
            if s or c or f or cl or bt or nt:
                n_doc += 1
                for k, v in (("summary", s), ("contract", c), ("fund", f),
                             ("coll", cl), ("bytype", bt), ("note", nt)):
                    if v:
                        n[k] += 1
                chars_sum += sum(len(x) for x in s)
                chars_ctr += sum(len(x) for x in c)
                fh.write(json.dumps(
                    {"rcept_no": m["rcept_no"],
                     "summary_rows": s, "contract_rows": c, "fund_rows": f,
                     "coll_rows": cl, "bytype_rows": bt, "note_rows": nt},
                    ensure_ascii=False) + "\n")
            if i % 100 == 0:
                print(f"  … {i}/{len(rows)}  "
                      f"(summary {n['summary']} · contract {n['contract']})", flush=True)

    total = max(len(rows), 1)
    print(f"holding {len(rows)}건 → 표를 담은 문서 {n_doc}건 ({n_doc / total:.1%})")
    print(f"  summary_rows  {n['summary']}건 ({n['summary'] / total:.1%}) · "
          f"평균 {chars_sum // max(n['summary'], 1):,}자")
    print(f"  fund_rows     {n['fund']}건 ({n['fund'] / total:.1%})")
    print(f"  note_rows     {n['note']}건 ({n['note'] / total:.1%})  ← 새 버킷 (표 밖 각주)")
    print(f"  contract_rows {n['contract']}건 ({n['contract'] / total:.1%}) · "
          f"평균 {chars_ctr // max(n['contract'], 1):,}자")
    print(f"  coll_rows     {n['coll']}건 ({n['coll'] / total:.1%})  ← 담보 추가 기재사항")
    print(f"  bytype_rows   {n['bytype']}건 ({n['bytype'] / total:.1%})  ← 종류별 보유내역")
    print(f"  → {dst}  ({dst.stat().st_size / 1024:,.0f}KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
