"""정정공시의 「정정사항」 표를 **항목별 삼중항**으로 사전 추출 — `corr_items.jsonl`.

★ 왜 필요한가 (2026-08-30 실측)
  정정공시 앞머리에는 「4. 정정사항」(major·holding·periodic 은 「3. 정정사항」) 표가 있고,
  거기에 `정정항목 | 정정전 | 정정후` 가 **행으로** 들어 있다. 그런데 **`events.raw` 에는
  그 항목별 값이 없다** — `parse_event` 는 본문 표만 담고, 본문에는 **정정 «후» 값만** 실린다.

      삼성SDI 유상증자 체인 20250314000027 → 20250409001154
        「자금조달의 목적 - 시설자금(원) 454,100,000,000 → 354,100,000,000」
        이 정정사항 표에만 있고 `raw` 에는 시설자금 항목이 **한 글자도** 없다
        (`'FDPP' in raw` → 빈 dict). 그래서 「시설자금이 얼마나 줄었나」(100,000,000,000)를
        답할 수 없었다.
      고려아연 20250120800751  투자금액(원) 69,800,000,000 → 89,200,000,000 ·
                              투자기간 종료일 2025-01-31 → 2026-06-30
      현대로템 20230206800764  계약금액 69,928,200,000 → 77,147,969,000

  `answerer._corr_segment()` 가 정정 헤더 «전문»을 스토어(`db/correction_headers.jsonl`)에
  담고 있지만 그것은 **한 덩어리 텍스트**라 항목별 대조가 안 된다. 여기서 만드는 것은
  **구조화된 (항목, 정정전, 정정후) 삼중항**이다.

★ 이 코퍼스에서 실제로 밟은 함정
  ① **머리글은 채굴해서 확인했다** (라벨을 추측하지 마라 — 함정 40).
     정정본 1,004건 전수 채굴: `정정전` 1,041회 · `정정후` 1,041회로 **정확히 이 두 글자**다
     (공백 제거 후). 라벨 열 머리글은 `정정항목` 631(exchange) · `항목` 402(그 외),
     곁열은 `정정사유` 398 · `정정요구ㆍ명령관련여부` 220.
     → **열 위치를 가정하지 않고 머리글로 찾는다.** 그리고 `정정전(주1)참조` 같은
       «값»이 머리글로 오인되지 않도록 **정확 일치**로만 잡는다(함정 57 — 부분일치 금지).
     실측: 부분일치로 잡으면 정기보고서 XI-1 「공시내용 진행 및 변경사항」의 **본문**이
     머리글로 오인돼 3건이 걸렸다.
  ② **ROWSPAN 으로 칸이 생략된다**(함정 24). 격자를 복원하지 않으면 열이 왼쪽으로 밀린다.
     그래도 라벨 칸이 진짜로 비면 **직전 행의 라벨을 이어받는다.**
  ③ **정정전/정정후 칸 «안»에 표가 또 들어 있다**(major 유상증자 등).
     그 중첩 표의 (소라벨, 값)까지 내려가야 「시설자금」이 나온다.
     exchange 는 대신 한 칸 안에서 `<br>` 로 여러 줄이다 —
     `2. 투자내역 / - 투자금액(원) / - 자기자본대비(%)` 대 `69,800,000,000 / 0.9`.
     → 줄 수가 맞을 때만 1:1 로 짝짓고, 라벨이 한 줄 더 많으면 첫 줄을 **묶음 제목**으로 본다.
       맞지 않으면 **쪼개지 않고** 행 전체를 한 항목으로 낸다 (틀린 짝짓기는 «없는 것»보다
       나쁘다 — 함정 62).
  ④ **before/after 를 뒤바꾸면 코드가 정반대를 단언한다.** 이 프로젝트에서 가장 위험한
     결함 유형이라(함정 60·62), 방향은 오직 머리글로만 정하고 표본 대조로 확인했다.
  ⑤ ★★ **새 함정 — exchange 원문의 `<br>` 은 «닫히지 않은» 태그다.**
     `loader.parse_doc()` 의 XML 파서(`recover=True`)는 `<br java="…">` 를 «여는 태그»로
     보고, 그 뒤의 형제 노드를 전부 그 안으로 삼킨다. 그러면 **한 칸의 텍스트가 문서
     끝까지 번진다** — 고려아연 20250120800751 의 정정항목 칸이 「2. 투자내역 … 69,800,000,000
     … ※ 관련공시」까지 통째로 한 칸이 되어 **정정전/정정후 칸이 아예 사라졌고**,
     투자금액 69,800,000,000 → 89,200,000,000 을 못 뽑았다.
     전수 실측: `<br>` 10,453개 **전부 exchange · 전부 미닫힘**, DART XML(major·periodic·
     holding)에는 **0개**.
     그때 `encoding="utf-8"` 을 **반드시** 주어야 한다 — 원문 meta 가 euc-kr 로 선언돼
     있어(함정 7) HTML 파서가 그 선언을 따르면 표가 **0개**로 파싱된다(실측).
  ⑥ ★★ **같은 병이 DART XML 에도 있다 — 이번엔 `<` 다.**
     메리츠금융지주 정기보고서에 `<P>[주요종속회사에 관한 사항]<손해보험업 부문_메리츠화재></P>`
     처럼 **이스케이프 안 된 `<`** 가 있다. XML 파서는 이걸 «여는 태그»로 읽고 **뒤따르는
     TD 4개를 통째로 삼킨다** → 정정전/정정후 칸이 격자에서 사라져 그 회사 정기보고서
     **14건이 전부 0항목**이었다. 함정 6(미이스케이프 `&`)의 `<` 판이다.
     → 비-exchange 정정본 **373건을 두 파서로 전수 대조**했다:
         동일 338 · HTML 이 행을 더 뽑음 19 · 글자가 다름 16 (**16건 전부 HTML 이 정확**
         — XML 은 `<내용 1-1> 참조` 의 `<내용 1-1>` 을 태그로 먹어 「1-1> 참조」로 만들고,
         `Korea Investment & Securities` 의 `&` 를 지운다). **HTML 이 진 사례는 0건.**
     → 그래서 **모든 정정본을 `etree.HTMLParser` 로 읽는다.**

산출: `03_가공/db/corr_items.jsonl`
      {"rcept_no": …, "items": [{"label": …, "before": …, "after": …}, …]}

실행:  export GONGSI_DATA_ROOT=".../02_데이터" && python3 scripts/run_build_corritems.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gongsi.io import loader  # noqa: E402

# 머리글 — 전수 채굴로 확인한 «정확한» 글자 (공백 제거 후 완전일치만 인정)
H_BEFORE = "정정전"
H_AFTER = "정정후"
H_LABEL = ("정정항목", "항목")          # exchange 631 · 그 외 402
H_SIDE = ("정정사유", "정정요구ㆍ명령관련여부", "정정요구·명령관련여부")

MAX_ITEMS = 20          # 한 문서당 항목 수 상한
MAX_VAL = 200           # 값 길이 상한(자)
EMPTY = {"", "-", "–", "—", "―", ".", "·"}

_WS = re.compile(r"\s+")
_LEAD_NO = re.compile(r"^\d{1,2}\s*[.)]\s*")     # 「4. 자금조달의 목적」의 번호
_LEAD_DASH = re.compile(r"^[\s\-·ㆍ*※]+")


def _tag(el) -> str:
    return (el.tag if isinstance(el.tag, str) else "").rsplit("}", 1)[-1].upper()


def _norm(s: str) -> str:
    """공백을 하나로 접은 표시용 문자열."""
    return _WS.sub(" ", s or "").strip()


def _key(s: str) -> str:
    """머리글 비교용 — 공백을 통째로 지운다 (「정 정 전」 = 「정정전」)."""
    return _WS.sub("", s or "")


def _span(el, attr: str) -> int:
    try:
        return max(1, int(el.get(attr) or el.get(attr.lower()) or 1))
    except (TypeError, ValueError):
        return 1


def _own_rows(table):
    """이 표에 **직접** 속한 TR 만. 중첩 표의 TR 을 섞으면 격자가 통째로 어긋난다."""
    out = []
    for tr in table.iter():
        if _tag(tr) != "TR":
            continue
        p = tr.getparent()
        while p is not None and _tag(p) != "TABLE":
            p = p.getparent()
        if p is table:
            out.append(tr)
    return out


def _grid(table) -> list[dict]:
    """<TABLE> → 격자(ROWSPAN/COLSPAN 복원). 함정 24 의 그 처방.

    반환 셀: {"r","c0","c1","rs","el","text"} — `el` 은 중첩 표를 다시 열기 위해 남긴다.
    """
    cells: list[dict] = []
    occupied: set[tuple[int, int]] = set()
    for r, tr in enumerate(_own_rows(table)):
        c = 0
        for el in tr:
            if _tag(el) not in ("TD", "TH", "TE", "TU"):
                continue
            while (r, c) in occupied:
                c += 1
            cs, rs = _span(el, "COLSPAN"), _span(el, "ROWSPAN")
            cells.append({"r": r, "c0": c, "c1": c + cs - 1, "rs": rs,
                          "el": el, "text": _norm("".join(el.itertext()))})
            for dr in range(rs):
                for dc in range(cs):
                    occupied.add((r + dr, c + dc))
            c += cs
    return cells


def _matrix(cells: list[dict]) -> dict[tuple[int, int], dict]:
    """격자 → {(행, 열): 셀}. ROWSPAN/COLSPAN 이 덮는 칸에 **같은 셀 객체**를 채운다."""
    mat: dict[tuple[int, int], dict] = {}
    for x in cells:
        for dr in range(x["rs"]):
            for c in range(x["c0"], x["c1"] + 1):
                mat.setdefault((x["r"] + dr, c), x)
    return mat


def _direct_tables(cell_el) -> list:
    """셀 «바로 아래» 중첩 표 (그 표 안의 표는 빼고)."""
    out = []
    for t in cell_el.iter():
        if _tag(t) != "TABLE":
            continue
        p, direct = t.getparent(), True
        while p is not None and p is not cell_el:
            if _tag(p) == "TABLE":
                direct = False
                break
            p = p.getparent()
        if direct:
            out.append(t)
    return out


# 줄바꿈 표시. **원문의 개행 문자를 쓰면 안 된다** — DART XML 은 `</SPAN>` 과 `<SPAN>`
# 사이에 서식용 개행이 있어서, 개행으로 자르면 **한 문장이 세 줄로 쪼개진다**
# (실측 삼성SDI 「24. 기타 투자판단에참고할 사항」). 줄은 «태그»가 만든다.
_BRK = "\x00"


def _flat_lines(el) -> list[str]:
    """셀 텍스트를 «줄» 로. `<BR>`·`<P>`·`<DIV>` **만** 줄바꿈으로 본다.

    exchange 는 한 칸 안에서 `<br java="…">` 로 여러 항목을 이어 붙인다 —
    그걸 지우고 이어 붙이면 「투자금액」과 「자기자본대비」가 한 덩어리가 된다.
    """
    buf: list[str] = []

    def walk(node):
        if _tag(node) in ("BR", "P", "DIV", "TR", "LI"):
            buf.append(_BRK)
        if node.text:
            buf.append(node.text)
        for ch in node:
            if isinstance(ch.tag, str):
                walk(ch)
            if ch.tail:
                buf.append(ch.tail)

    walk(el)
    return [_norm(x) for x in "".join(buf).split(_BRK) if _norm(x)]


def _sub_pairs(table) -> list[tuple[str, str]]:
    """중첩 표 → [(소라벨, 값)]. 마지막 칸이 값, 앞쪽 칸들이 라벨이다.

    실측(삼성SDI 20250409001154 「6. 신주 발행가액」):
        ROWSPAN=2 「예정발행가」 + 「보통주식 (원)」 + 「169,200」
        → 소라벨 「예정발행가 보통주식 (원)」. 격자 복원이 없으면 둘째 행의 라벨이 사라진다.
    """
    cells = _grid(table)
    if not cells:
        return []
    by_row: dict[int, list[dict]] = {}
    for x in cells:
        for dr in range(x["rs"]):
            by_row.setdefault(x["r"] + dr, []).append(x)
    out: list[tuple[str, str]] = []
    for r in sorted(by_row):
        row = sorted(by_row[r], key=lambda x: x["c0"])
        if len(row) < 2:
            continue
        label = _norm(" ".join(x["text"] for x in row[:-1]))
        out.append((label, row[-1]["text"]))
    return out


def _cell_view(cell: dict | None) -> tuple[list[tuple[str, str]], list[str]]:
    """셀 → (중첩 표의 (소라벨,값) 목록, 평문 줄 목록). 둘 중 하나만 채워진다."""
    if cell is None:
        return [], []
    tabs = _direct_tables(cell["el"])
    if tabs:
        pairs: list[tuple[str, str]] = []
        for t in tabs:
            pairs += _sub_pairs(t)
        if pairs:
            return pairs, []
    return [], _flat_lines(cell["el"])


def _clean_label(s: str) -> str:
    return _norm(_LEAD_DASH.sub("", _LEAD_NO.sub("", _norm(s))))


def _is_empty(v: str) -> bool:
    return _norm(v) in EMPTY


def _emit(items: list[dict], label: str, before: str, after: str) -> None:
    label, before, after = _clean_label(label), _norm(before), _norm(after)
    if not label and _is_empty(before) and _is_empty(after):
        return
    if _is_empty(before) and _is_empty(after):
        return                                   # 양쪽 다 비었거나 「-」뿐 → 버린다
    items.append({"label": label[:MAX_VAL],
                  "before": before[:MAX_VAL],
                  "after": after[:MAX_VAL]})


def _row_items(lab_lines: list[str], cb: dict | None, ca: dict | None) -> list[dict]:
    """한 데이터 행 → 항목들. `lab_lines` 는 라벨 칸을 줄 단위로 쪼갠 것이다.

    ★ 쪼갤 수 있을 때만 쪼갠다. 짝이 안 맞으면 행 전체를 한 항목으로 낸다 —
      **틀린 짝짓기는 «없는 것»보다 나쁘다**(함정 62).
    """
    out: list[dict] = []
    pb, lb = _cell_view(cb)
    pa, la = _cell_view(ca)
    base = _clean_label(" ".join(lab_lines))

    # ① 양쪽이 중첩 표 → 소라벨이 «완전히 같을 때만» 항목별로 쪼갠다
    if pb and pa and len(pb) == len(pa) and \
            all(_key(x[0]) == _key(y[0]) for x, y in zip(pb, pa)):
        for (sub, bv), (_s, av) in zip(pb, pa):
            lab = f"{base} - {_clean_label(sub)}" if base else _clean_label(sub)
            _emit(out, lab, bv, av)
        return out

    # ② 양쪽이 평문 여러 줄 → 줄 수가 맞을 때만 1:1
    if lb and la and len(lb) == len(la) and len(lb) > 1:
        heads: list[str] = []
        if len(lab_lines) == len(lb):
            base, heads = "", lab_lines            # 줄마다 제 라벨이 있다
        elif len(lab_lines) == len(lb) + 1:
            # 첫 줄은 «값이 없는» 묶음 제목 — 「2. 투자내역 / - 투자금액(원) / - 자기자본대비(%)」
            base, heads = _clean_label(lab_lines[0]), lab_lines[1:]
        if heads:
            grp = base
            for raw, bv, av in zip(heads, lb, la):
                sub = _clean_label(raw)
                if raw.lstrip()[:1] in ("-", "·", "ㆍ", "*"):
                    # 대시로 시작하는 줄은 «바로 위 묶음»에 속한다
                    lab = f"{grp} - {sub}" if grp and _key(grp) != _key(sub) else sub
                else:
                    grp = sub                      # 대시가 없는 줄은 그 자체가 새 묶음이다
                    lab = (f"{base} - {sub}" if base and _key(base) != _key(sub)
                           else sub)
                _emit(out, lab, bv, av)
            return out

    # ③ 못 쪼갠다 → 행 전체 한 항목
    bv = " / ".join(x[1] for x in pb) if pb else " / ".join(lb)
    av = " / ".join(x[1] for x in pa) if pa else " / ".join(la)
    _emit(out, base, bv, av)
    return out


def _roots(m: dict) -> list:
    """원문 → lxml 루트 목록. **HTML 파서**로 읽는다 (위 ⑤·⑥ 참조).

    `encoding="utf-8"` 을 **반드시** 준다 — 원문 meta 가 euc-kr 로 선언돼 있어(함정 7)
    HTML 파서가 그 선언을 따르면 표가 **0개**로 파싱된다(실측 고려아연 20250120800751).
    """
    from lxml import etree

    parser = etree.HTMLParser(recover=True, huge_tree=True, encoding="utf-8")
    out = []
    for name, text in loader.read_doc(m["file_path"]):
        root = etree.fromstring(text.encode("utf-8"), parser)
        if root is not None:
            out.append((name, root))
    return out


def items_of(m: dict) -> list[dict]:
    """정정본 원문 → [{label, before, after}] (추출 규칙의 «유일한» 자리)."""
    try:
        roots = _roots(m)
    except Exception:                                          # noqa: BLE001
        return []
    items: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for _name, root in roots:
        for table in root.iter():
            if _tag(table) != "TABLE":
                continue
            cells = _grid(table)
            if not cells:
                continue
            mat = _matrix(cells)
            # 머리글 행 찾기 — 위치를 가정하지 않는다
            hdr_r = c_before = c_after = c_label = None
            for x in sorted(cells, key=lambda z: (z["r"], z["c0"])):
                if _key(x["text"]) != H_BEFORE:
                    continue
                mates = [y for y in cells if y["r"] == x["r"] and _key(y["text"]) == H_AFTER]
                if not mates:
                    continue
                hdr_r, c_before, c_after = x["r"], x["c0"], mates[0]["c0"]
                lab = [y for y in cells
                       if y["r"] == x["r"] and _key(y["text"]) in H_LABEL]
                if lab:
                    c_label = lab[0]["c0"]
                else:
                    side = {c_before, c_after} | {
                        y["c0"] for y in cells
                        if y["r"] == x["r"] and _key(y["text"]) in H_SIDE}
                    rest = [y["c0"] for y in cells if y["r"] == x["r"]
                            and y["c0"] not in side]
                    c_label = min(rest) if rest else 0
                break
            if hdr_r is None:
                continue

            nrow = max(x["r"] for x in cells) + 1
            carry: list[str] = []
            for r in range(hdr_r + 1, nrow):
                cb, ca = mat.get((r, c_before)), mat.get((r, c_after))
                cl = mat.get((r, c_label))
                if cb is None or ca is None or cb is ca:
                    continue                     # COLSPAN 주석 행 (「※ 금번 정정은 …」)
                if cl is cb or cl is ca:
                    continue
                if _key(cb["text"]) == H_BEFORE and _key(ca["text"]) == H_AFTER:
                    continue                     # 같은 표 안의 두 번째 머리글
                lab_lines = _flat_lines(cl["el"]) if cl else []
                if not lab_lines:
                    # ROWSPAN 으로도 안 채워진 «진짜 빈» 라벨 → 직전 행의 라벨을 이어받는다
                    lab_lines = carry
                elif lab_lines[0].lstrip().startswith("/") and carry:
                    # exchange 는 묶음 라벨을 «슬래시 들여쓰기»로 생략한다 —
                    #   「2. 계약내역 / 계약금액」 다음 행이 「        / 매출액대비」
                    #   (실측 현대로템 20230206800764). 앞 라벨의 묶음 부분을 이어 붙인다.
                    grp = _clean_label(carry[0]).split("/")[0].strip()
                    lab_lines = [f"{grp} {lab_lines[0].lstrip()}"] + lab_lines[1:]
                else:
                    carry = lab_lines
                for it in _row_items(lab_lines, cb, ca):
                    k = (it["label"], it["before"], it["after"])
                    if k in seen:
                        continue
                    seen.add(k)
                    items.append(it)
    if len(items) > MAX_ITEMS:
        # 넘치면 «바뀌지 않은» 항목부터 버린다 (정정의 요점은 «무엇이 바뀌었나»다)
        changed = [x for x in items if _key(x["before"]) != _key(x["after"])]
        items = (changed if len(changed) >= MAX_ITEMS else
                 changed + [x for x in items if x not in changed])[:MAX_ITEMS]
    return items


def main() -> int:
    corpus = loader.load_corpus()
    rows = [m for m in corpus.manifest if m.get("is_correction")]
    dst = Path(loader.DERIVED_DIR) / "db" / "corr_items.jsonl"
    dst.parent.mkdir(parents=True, exist_ok=True)

    n_doc = n_item = 0
    by_group: dict[str, list[int]] = {}
    with open(dst, "w", encoding="utf-8") as fh:
        for i, m in enumerate(rows, 1):
            its = items_of(m)
            g = by_group.setdefault(m.get("doc_group") or "?", [0, 0])
            g[1] += 1
            if its:
                n_doc += 1
                n_item += len(its)
                g[0] += 1
                fh.write(json.dumps({"rcept_no": m["rcept_no"], "items": its},
                                    ensure_ascii=False) + "\n")
            if i % 100 == 0:
                print(f"  … {i}/{len(rows)}  (항목 있는 문서 {n_doc})", flush=True)

    print(f"정정본 {len(rows)}건 → 항목 추출 {n_doc}건 "
          f"({n_doc / max(len(rows), 1):.1%}) · 항목 {n_item:,}개 · "
          f"문서당 평균 {n_item / max(n_doc, 1):.1f}개")
    for g in sorted(by_group):
        ok, tot = by_group[g]
        print(f"  {g:9s} {ok:4d}/{tot:4d}  ({ok / max(tot, 1):.1%})")
    print(f"  → {dst}  ({dst.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
