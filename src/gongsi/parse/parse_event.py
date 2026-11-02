"""이벤트공시(exchange · major · holding) → 구조화 레코드.

원문 4,204건 중 이벤트공시 3,150건을 표에서 필드로 뽑아 JSONL 로 떨군다.
정기공시(periodic)는 parse_periodic.py 담당.

    python scripts/run_parse_event.py                 # 전체 실행 + 추출률 리포트
    python scripts/run_parse_event.py --group exchange
    python scripts/run_parse_event.py --acode-report  # 미확인 ACODE 목록 뽑기
    python scripts/run_parse_event.py --limit 50      # 빠른 점검

출력: 02_데이터/03_가공/db/events.jsonl , related.jsonl
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "io"))
import loader  # noqa: E402

from . import dart_xml, exchange_html  # noqa: E402


def parse_one(row: dict) -> dict | None:
    """manifest 한 행 → 이벤트 레코드. 파싱 불가면 None."""
    group = row["doc_group"]
    docs = loader.parse_doc(row["file_path"])
    if not docs:
        return None                              # pdf+html 대체 수집 3건
    _name, root = docs[0]

    if group == "exchange":
        etype = exchange_html.event_type_of(row["report_nm"])
        if etype is None:
            return None
        text = exchange_html.body_text("", root)
        fields = exchange_html.parse(text, etype)
    else:
        etype = dart_xml.event_type_of(row["report_nm"], group)
        fields = dart_xml.parse(root, etype)

    related = fields.pop("related", [])
    record = {
        "rcept_no":      row["rcept_no"],
        "corp_code":     row["corp_code"],
        "corp_name":     row["corp_name"],
        "doc_group":     group,
        "report_nm":     row["report_nm"],
        "rcept_dt":      row["rcept_dt"],
        "is_correction": row["is_correction"],
        "sector":        row.get("sector"),
        **fields,
    }
    record["_related"] = related
    return record


def link_related(records: list[dict], manifest: list[dict]) -> list[dict]:
    """※ 관련공시 날짜 → 코퍼스 내 접수번호.

    실측(해지 20건 전수): 코퍼스가 수집한 유형이고 기간 안이면 매칭률 100%.
    못 찾는 경우는 두 가지뿐이고 둘 다 정상이다.
      out_of_corpus      ― 수집 시작(2023-01-01) 이전에 나온 공시
      type_not_collected ― 코퍼스가 수집하지 않는 공시 유형
                           (기타 경영사항·공정공시·채무인수결정·풍문해명 등)
    그래서 unresolved 만 실패로 센다.
    """
    index = collections.defaultdict(list)
    for row in manifest:
        index[(row["corp_name"], row["rcept_dt"])].append(row)

    # 후보가 둘 이상일 때 판별에 쓸 «파싱된 필드» 색인 (manifest 에는 금액·상대방이 없다)
    parsed = {r["rcept_no"]: r for r in records}

    links = []
    for rec in records:
        for rel in rec.get("_related", []):
            ymd = rel["related_dt"].replace("-", "")
            hits = index.get((rec["corp_name"], ymd), [])
            named = [h for h in hits
                     if _title_match(rel["related_nm"], h["report_nm"])
                     and h["rcept_no"] != rec["rcept_no"]]
            best, status = _pick_related(rec, named, parsed)
            if best:
                pass
            elif not _is_collected_type(rel["related_nm"]):
                status = "type_not_collected"
            elif status:
                pass                       # _pick_related 가 ambiguous 로 판정한 경우
            elif rel["related_dt"] < CORPUS_START:
                status = "out_of_corpus"
            else:
                status = "unresolved"
            links.append({
                "rcept_no":      rec["rcept_no"],
                "related_dt":    rel["related_dt"],
                "related_nm":    rel["related_nm"],
                "related_rcept": best["rcept_no"] if best else None,
                "chain_status":  status,
            })
    return links


# 코퍼스가 수집한 exchange 공시 유형. 이 밖의 관련공시는 애초에 존재하지 않는다.
CORPUS_START = "2023-01-01"
COLLECTED_KEYWORDS = ("단일판매", "공급계약", "시설투자", "투자판단")


def _is_collected_type(related_nm: str) -> bool:
    flat = related_nm.replace(" ", "").replace("ㆍ", "")
    return any(k in flat for k in COLLECTED_KEYWORDS)


def _norm(text: str) -> str:
    return text.replace(" ", "").replace("ㆍ", "").replace("[기재정정]", "")


def _title_match(related_nm: str, report_nm: str) -> bool:
    key = _norm(related_nm)[:8]
    return bool(key) and key in _norm(report_nm)


def _sig(rec: dict) -> tuple:
    """판별 서명 — 금액·상대방·계약시작일. 해지 공시는 `계약명` 이 None 이라 쓸 수 없다."""
    return (rec.get("amount"),
            (rec.get("counterparty") or "").strip(),
            rec.get("period_start"))


def _pick_related(rec: dict, named: list[dict], parsed: dict) -> tuple[dict | None, str]:
    """관련공시 후보 중 하나를 고른다. (선택된 manifest 행, chain_status) 반환.

    ★ 2026-08-22 실측으로 고침. 옛 코드는 `next(...)` 로 **첫 매치를 그냥 집었다.**
      같은 회사·같은 날·같은 서식의 공시가 2건 이상이면 manifest 순서가 답을 정했다.

      실측: linked 764건 중 후보가 2건 이상인 «모호» 링크 **243건**,
            그중 3필드로 유일하게 갈리는 것이 72건이고 **49건이 틀린 문서를 가리키고 있었다.**
            대표 사례 ― 삼성중공업 해지 20250618800388 의 원계약을 20240612800459 로 줬으나
            금액·상대방·시작일 셋이 전부 어긋나고, 정답은 20240612800468 이다.
            삼성바이오로직스 정정본 3건은 전부 20231128800002 하나를 가리키고 있었다.

      이 버그가 「N:M 관계가 실재한다」(구 함정 18)로 **잘못 일반화**돼 있었다.

    판정:
      후보 0            → 링크 없음 (호출측이 out_of_corpus / unresolved 를 판정)
      후보 1            → linked
      후보 2+ · 3필드 유일 → linked          (확신)
      후보 2+ · 그 외      → ambiguous       (**틀린 근거를 확신 있게 내놓느니 모른다고 한다**)
    """
    if not named:
        return None, ""
    if len(named) == 1:
        return named[0], "linked"

    want = _sig(rec)
    exact = [h for h in named
             if h["rcept_no"] in parsed and _sig(parsed[h["rcept_no"]]) == want]
    if len(exact) == 1:
        return exact[0], "linked"
    return None, "ambiguous"


def run(groups: list[str], limit: int | None, acode_report: bool) -> int:
    corpus = loader.load_corpus()
    targets = [r for r in corpus.manifest if r["doc_group"] in groups]
    if limit:
        targets = targets[:limit]

    records, failures = [], []
    for row in targets:
        try:
            rec = parse_one(row)
            if rec is None:
                failures.append((row["rcept_no"], "파싱 대상 아님/파일 없음"))
            else:
                records.append(rec)
        except Exception as exc:                      # noqa: BLE001
            failures.append((row["rcept_no"], f"{type(exc).__name__}: {exc}"))

    links = link_related(records, corpus.manifest)

    out_dir = Path(loader.DERIVED_DIR) / "db"
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "events.jsonl", "w", encoding="utf-8") as fh:
        for rec in records:
            rec.pop("_related", None)
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    with open(out_dir / "related.jsonl", "w", encoding="utf-8") as fh:
        for link in links:
            fh.write(json.dumps(link, ensure_ascii=False) + "\n")

    _report(targets, records, failures, links)
    if acode_report:
        _acode_report(records)
    return 0 if not failures else 1


def _rate(hit: int, total: int) -> str:
    return f"{hit:,}/{total:,} ({hit / total * 100:.0f}%)" if total else "-"


def _report(targets, records, failures, links) -> None:
    print("=" * 62)
    print(f" 이벤트공시 파싱 ― 대상 {len(targets):,}건 · 성공 {len(records):,}건 · 실패 {len(failures):,}건")
    print("=" * 62)

    by_type = collections.defaultdict(list)
    for rec in records:
        by_type[rec["event_type"]].append(rec)

    print(f"\n{'event_type':<20}{'건수':>7}  {'금액':>16}{'비율':>16}{'상대방':>16}")
    # 유형에 아예 없는 칸은 추출률을 매기지 않는다.
    #   투자판단  : 금액·상대방 칸이 서식에 없음 (서술형)
    #   신규시설투자: 계약상대 개념 없음
    #   대량보유  : 금액 개념 없음 (주식수·비율만)
    #   자기주식  : 상대방 칸 없음 (신탁계약만 계약 증권사가 있음)
    NO_AMOUNT = {"투자판단", "대량보유", "회사합병결정", "회사분할결정", "회사분할합병결정"}
    NO_RATIO = {"공급계약해지", "투자판단", "자기주식취득", "자기주식처분",
                "자기주식신탁계약체결", "자기주식신탁계약해지"}
    NO_PARTY = {"투자판단", "신규시설투자", "자기주식취득", "자기주식처분",
                "유상증자결정", "무상증자결정", "감자결정"}
    NA = "해당없음"
    for etype, recs in sorted(by_type.items(), key=lambda kv: -len(kv[1])):
        n = len(recs)
        amt = NA if etype in NO_AMOUNT else _rate(sum(1 for r in recs if r.get("amount") is not None), n)
        rat = NA if etype in NO_RATIO else _rate(sum(1 for r in recs if r.get("amount_ratio") is not None), n)
        par = NA if etype in NO_PARTY else _rate(sum(1 for r in recs if r.get("counterparty")), n)
        print(f"{etype:<20}{n:>7,}  {amt:>16}{rat:>16}{par:>16}")

    status = collections.Counter(l["chain_status"] for l in links)
    print(f"\n※ 관련공시 링크 {len(links):,}개")
    for key in ("linked", "out_of_corpus", "type_not_collected", "unresolved"):
        print(f"   {key:<14}{status.get(key, 0):>6,}")
    if status.get("unresolved"):
        print("   ! unresolved 만 실패다. 아래 목록 확인:")
        for l in [x for x in links if x["chain_status"] == "unresolved"][:10]:
            print(f"     {l['rcept_no']} → {l['related_dt']} {l['related_nm']}")

    if failures:
        print(f"\n실패 {len(failures)}건 (상위 10)")
        for rcept, msg in failures[:10]:
            print(f"   {rcept}  {msg}")


def _acode_report(records) -> None:
    known = set(dart_xml.ACODE_MAP) | set(dart_xml.AUNIT_MAP)
    counter = collections.Counter()
    for rec in records:
        for code in list(rec.get("raw_te", {})) + list(rec.get("raw_tu", {})):
            if code not in known:
                counter[code] += 1
    print(f"\n미확인 ACODE/AUNIT {len(counter)}종 (상위 40) ― dart_xml.py 사전에 추가할 후보")
    for code, n in counter.most_common(40):
        print(f"   {code:<24}{n:>7,}")


def main() -> int:
    ap = argparse.ArgumentParser(description="이벤트공시 파싱")
    ap.add_argument("--group", action="append",
                    choices=["exchange", "major", "holding"], default=None)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--acode-report", action="store_true")
    args = ap.parse_args()
    groups = args.group or ["exchange", "major", "holding"]
    return run(groups, args.limit, args.acode_report)


if __name__ == "__main__":
    raise SystemExit(main())
