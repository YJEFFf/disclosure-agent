# -*- coding: utf-8 -*-
r"""정기공시 1,054건 → 섹션 단위 분해 (C단계).

실측한 구조 (삼성전자 사업보고서 20260310002820):
  DOCUMENT > BODY > SECTION-1(장) > SECTION-2(절) > (드물게 SECTION-3)
  각 섹션의 첫 TITLE 이 제목. TABLE-GROUP 안의 TITLE 은 표 제목이라 경계가 아니다.
  재무제표 파일은 SECTION-1(재무제표) "본문 안에" SECTION-2(주석)가 중첩된다
  → 섹션 텍스트는 반드시 "자기 텍스트"(하위 섹션 제외)로 뽑아야 이중계상이 없다.

출력: 02_데이터\03_가공\db\sections.jsonl
  한 줄 = 한 섹션. doc 메타 + sec_path(장>절 경로) + level + text(자기 텍스트)

실행: scripts\run_parse_periodic.py 가 진입점.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "io"))
import loader  # noqa: E402

SECTION_TAGS = {"SECTION-1", "SECTION-2", "SECTION-3"}
# 텍스트 직렬화: 블록 태그 뒤에 줄바꿈, 셀 태그 앞뒤에 구분 공백
_BLOCK = {"P", "TABLE", "TR", "TITLE", "TABLE-GROUP", "COVER"}
_CELL = {"TD", "TE", "TU", "TH"}

# 섹션으로 남길 가치가 없는 제목 (본문이 형식 문구뿐)
_SKIP_TITLES = {"목 차", "목차"}

MIN_CHARS = 30          # 자기 텍스트가 이보다 짧으면 제목만 있는 껍데기로 본다
# ★ 단 «없다»를 명시한 짧은 섹션은 살린다 (M-103). 그 자체가 답이기 때문이다.
# ★ 2026-09-06 (R55) — 「작성지침에 따라 분기보고서에는 **기재하지 아니함**」(현대제철
#   분기 배당 섹션 · 28자)이 목록에 없어 M-103 과 같은 모양으로 사라졌다. «기재 생략»과
#   같은 뜻의 다른 표기다 — 배당 섹션 결손 30건 중 10건(현대제철 전 분기판)의 원인.
_NONE_STATEMENT = re.compile(r"해당\s?사항|해당\s?없|없습니다|없음|미해당|기재\s?생략|작성\s?생략"
                             r"|기재하지\s?(?:아니|않)|작성하지\s?(?:아니|않)")


def _span(el, attr: str) -> int:
    try:
        n = int(el.get(attr) or el.get(attr.lower()) or 1)
    except (TypeError, ValueError):
        return 1
    return n if 1 <= n <= 200 else 1


def _owner_table(tr):
    """이 TR 이 직접 속한 TABLE (중첩 표를 헷갈리지 않기 위해)."""
    p = tr.getparent()
    while p is not None and getattr(p, "tag", None) != "TABLE":
        p = p.getparent()
    return p


def _carry_map(tb) -> dict:
    """표 하나 → {TR: (before, tail)} — ROWSPAN 으로 **생략된 칸**을 되살릴 자리표.

    ★ 2026-08-17 실측. 원문은 ROWSPAN 으로 칸을 생략하는데 여기선 그걸 무시하고
      TR 을 그대로 이어 붙여 왔다. 그래서 다음 행들은 셀이 모자라 **열이 왼쪽으로 밀린다**:

          | 대출약정한도 | (주)KEB하나은행 | 천원 | 30,000,000 | - |
          | 천원 | 1,000,000 | - |                    ← 앞 2칸 누락
          | (주)국민은행 | 천원 | 72,000,000 | - |      ← 앞 1칸 누락

      머리글이 「약정내용|금융기관|단위|약정금액|실행금액|비고」이므로 마지막 줄은
      «약정내용=(주)국민은행, 금융기관=천원, 단위=72,000,000» 으로 읽힌다.
      한전기술 20260326001003 「2. 우발부채」가 이 때문에 R10·R12·R13·R14 **네 라운드
      연속** 0.30 이었다 (gold 72,000,000).

    모집단: 정기공시 TR 의 15.6% 가 span 셀을 가진다(표본 25건). 이 함수가 실제로
      칸을 되살리는 TR 은 전체의 5.8%, 복원 칸 17,898개(표본 10건). 글자 +5.2%.

    행 안의 COLSPAN 은 **펼치지 않는다** — 펼치면 글자가 +30.6% 인데(실측),
    라벨↔값이 어긋나는 결함은 ROWSPAN 이 원인이고 COLSPAN 은 대개 머리글 묶음이다.

    before[i] : i번째 실제 셀 **앞**에 끼울 값 · tail : 행 끝에 남은 칸
    """
    trs = [tr for tr in tb.iter("TR") if _owner_table(tr) is tb]
    if not trs:
        return {}
    width = 0
    for tr in trs:
        w = sum(_span(el, "COLSPAN") for el in tr
                if isinstance(el.tag, str) and el.tag in _CELL)
        width = max(width, w)

    out: dict = {}
    filled: dict[tuple[int, int], str] = {}       # (행,열) → 위에서 내려온 값
    for r, tr in enumerate(trs):
        cells = [el for el in tr if isinstance(el.tag, str) and el.tag in _CELL]
        before: list[list[str]] = [[] for _ in cells]
        col = 0
        for i, el in enumerate(cells):
            while (r, col) in filled:
                before[i].append(filled[(r, col)])
                col += 1
            cs, rs = _span(el, "COLSPAN"), _span(el, "ROWSPAN")
            val = " ".join("".join(el.itertext()).split())
            # 표 전체 너비를 덮는 칸은 배너(「(단위: 천원)」 등) — 반복하지 않는다
            banner = cs > 1 and cs >= width
            if rs > 1:
                for k in range(cs):
                    v = val if (k == 0 or not banner) else ""
                    for dr in range(1, rs):
                        filled[(r + dr, col + k)] = v
            col += cs
        tail: list[str] = []
        while (r, col) in filled:
            tail.append(filled[(r, col)])
            col += 1
        if any(before) or tail:
            out[tr] = (before, tail)
    return out


def own_text(el) -> str:
    """섹션 요소의 텍스트. 하위 SECTION-* 은 건너뛴다 (그쪽은 자기 행에서 나온다).

    표 직렬화: **행(TR) = 한 줄, 셀 = ' | ' 구분.**
    처음엔 셀마다 줄바꿈을 했더니(셀 안의 P 가 블록이라) '매출액' 라벨과 값들이
    세로로 흩어져 HCX-005 가 "공시에서 확인되지 않습니다"를 냈다 (실측).
    셀 내부에선 블록 줄바꿈을 눌러서 행 구조를 지킨다.

    ROWSPAN 으로 생략된 칸은 `_carry_map` 이 제자리에 되살린다 — 그게 없으면
    행이 자기 라벨을 잃고 열이 통째로 밀린다.
    """
    parts: list[str] = []
    carry: dict = {}
    for tb in el.iter("TABLE"):
        carry.update(_carry_map(tb))

    # 원문 XML 은 프리티프린팅이라 텍스트 노드 안에 줄바꿈+들여쓰기가 들어 있다.
    # 이걸 그대로 두면 셀 하나가 여러 줄로 흩어진다 → 원문 줄바꿈은 공백으로,
    # 줄 구조는 우리가 넣는 '\n'(블록 경계)만으로 만든다.
    def _t(s: str) -> str:
        return s.replace("\n", " ").replace("\r", " ")

    def walk(e, in_cell: bool = False):
        if e.text:
            parts.append(_t(e.text))
        info = carry.get(e) if getattr(e, "tag", None) == "TR" else None
        cell_i = 0
        for ch in e:
            if not isinstance(ch.tag, str):        # 주석 노드 등
                if ch.tail:
                    parts.append(_t(ch.tail))
                continue
            if ch.tag in SECTION_TAGS:             # 하위 섹션은 제외, tail 은 살린다
                if ch.tail:
                    parts.append(_t(ch.tail))
                continue
            child_in_cell = in_cell or ch.tag in _CELL
            if ch.tag in _CELL:
                if info is not None and cell_i < len(info[0]):
                    for v in info[0][cell_i]:      # 위 행에서 내려온 칸을 먼저
                        parts.append(" | " + v)
                cell_i += 1
                parts.append(" | ")
            walk(ch, child_in_cell)
            if ch.tag in _BLOCK and not child_in_cell:
                parts.append("\n")
            if ch.tail:
                parts.append(_t(ch.tail))
        if info is not None:
            for v in info[1]:                      # 행 끝에 남은 칸
                parts.append(" | " + v)

    walk(el)
    # 줄 단위 정리: 줄 안 공백 압축, 빈 줄 제거
    lines = ("".join(parts)).splitlines()
    lines = [" ".join(ln.split()) for ln in lines]
    return "\n".join(ln for ln in lines if ln)


def sec_title(el) -> str:
    """섹션의 제목 = 자기 직속 TITLE (하위 섹션·표 제목 제외)."""
    for ch in el:
        if isinstance(ch.tag, str) and ch.tag == "TITLE":
            return " ".join("".join(ch.itertext()).split())
    return ""


def walk_sections(root, path: tuple[str, ...] = ()):
    """(sec_path, level, element) 를 문서 순서로 낸다."""
    for el in root:
        if not isinstance(el.tag, str):
            continue
        if el.tag in SECTION_TAGS:
            title = sec_title(el)
            level = int(el.tag[-1])
            new_path = path + (title,) if title else path
            yield new_path, level, el
            yield from walk_sections(el, new_path)
        else:
            yield from walk_sections(el, path)


def parse_doc_sections(doc: dict) -> list[dict]:
    """정기공시 1건(폴더) → 섹션 행 목록."""
    rows: list[dict] = []
    for fname, root in loader.parse_doc(doc["file_path"]):
        for sec_path, level, el in walk_sections(root):
            title = sec_path[-1] if sec_path else ""
            if not title or title in _SKIP_TITLES:
                continue
            text = own_text(el)
            # 제목 줄이 본문 맨 앞에 반복되면 제거
            if text.startswith(title):
                text = text[len(title):].lstrip("\n ")
            # ★ 2026-08-19 (M-103) — **「해당사항 없음」은 버릴 값이 아니다.**
            #   실측 H5-040: 한미반도체 반기보고서의 「3. 제재 등과 관련된 사항」 본문이
            #   「- 해당사항 없음」(9자)이라 `MIN_CHARS` 에 걸려 사라졌고, 제품은
            #   「반기보고서 자체가 포함되어 있지 않다」고 답했다 — **원문엔 있다.**
            #   평가지표 7(정보한계 대응)이 재는 것이 「없다」와 「못 찾았다」의 차이다(M-96).
            #   표본 60건: 버려진 것 중 «해당사항 없음»류 **150** · 완전 빈껍데기 599 ·
            #   짧은 기타 55. 전체 환산 **약 2,600 섹션**(현재 37,837 대비 +7%).
            #   빈껍데기와 이미지 캡션(「대표이사 서명확인.jpg …」)은 그대로 버린다.
            if len(text) < MIN_CHARS and not _NONE_STATEMENT.search(text):
                continue
            rows.append({
                "doc_id": doc["doc_id"],
                "corp_code": doc["corp_code"],
                "corp_name": doc["corp_name"],
                "doc_subtype": doc["doc_subtype"],
                "report_nm": doc["report_nm"],
                "rcept_no": doc["rcept_no"],
                "rcept_dt": doc["rcept_dt"],
                "base_year": doc.get("base_year"),
                "base_month": doc.get("base_month"),
                "is_correction": doc.get("is_correction"),
                "file": fname,
                "sec_path": " > ".join(sec_path),
                "sec_title": title,
                "level": level,
                "n_chars": len(text),
                "text": text,
            })
    return rows


def main() -> int:
    c = loader.load_corpus()
    docs = [r for r in c.manifest if r["doc_group"] == "periodic"]
    print(f"정기공시 {len(docs):,}건 섹션 분해 시작")

    out_path = Path(loader.DERIVED_DIR) / "db" / "sections.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    n_rows = n_fail = 0
    fails: list[str] = []
    per_subtype = Counter()
    empty_docs: list[str] = []

    with out_path.open("w", encoding="utf-8") as fh:
        for i, doc in enumerate(docs, 1):
            try:
                rows = parse_doc_sections(doc)
            except Exception as exc:                       # noqa: BLE001
                n_fail += 1
                if len(fails) < 5:
                    fails.append(f"{doc['rcept_no']} {type(exc).__name__}: {exc}")
                continue
            if not rows:
                empty_docs.append(doc["rcept_no"])
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            n_rows += len(rows)
            per_subtype[doc["doc_subtype"]] += len(rows)
            if i % 200 == 0:
                print(f"  {i:,}/{len(docs):,} … 섹션 {n_rows:,}행")

    print("\n===== parse_periodic 리포트 =====")
    print(f"  문서: {len(docs):,}건 · 실패 {n_fail}건 · 섹션 0건 문서 {len(empty_docs)}건")
    print(f"  섹션 행: {n_rows:,}")
    for st, n in per_subtype.most_common():
        print(f"    {st:<10} {n:>8,}행")
    if fails:
        print("  실패 예시:", *fails, sep="\n    ")
    if empty_docs[:5]:
        print("  섹션 0건 예시:", ", ".join(empty_docs[:5]))
    print(f"  출력: {out_path}")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
