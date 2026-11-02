"""major «서술 항목» 사전 추출 — `major_notes.jsonl`.

★ 2026-08-30 (R6N 출제자 발견 ⑥ → 전수 실측으로 확증). 주요사항보고서의
  「기타 투자판단에 참고할 사항」·「향후 회사구조개편에 관한 계획」 같은 항목은
  `<TABLE>` 에 **제목만** 들어 있고 본문은 표 **바깥 문단**이다. 그래서
  `dart_xml` 의 ACODE 파서가 한 글자도 못 담는다.

      major 598건 · 서술 항목이 원문에 있는 문서 **590**
        DB raw 에도 있음 51 · **내용이 있는데 DB 에 없음 533 (89%)**

  값은 실질적이다 — 「상기 1항의 상장폐지주식 수…」·「1일 매수 주문 수량한도 :
  보통주 6,525,123주 ※ 산출근거…」·「취득예정금액은 이사회 결의일 당일 종가 기준」.

  **함정 26 의 major 판**이다. 거래소 서술형을 `exchange_body()` 로 푼 처방이
  major 에는 없었다 (함정 23 — 「한쪽에만 있는 처방」 열 번째).

★ 왜 질의 시점 원문 읽기가 아니라 사전 추출인가 — **배송 트리**다.
  런타임이 여는 원문은 `exchange` 뿐이고(`stage_deploy.CORPUS_RAW_GROUPS`),
  major 원문을 질의 시점에 열면 배포본에 그 원문을 통째로 실어야 한다.
  표지 표(`run_build_cover.py`)와 같은 길을 쓴다.

산출: `03_가공/db/major_notes.jsonl` — {"rcept_no": …, "notes": "…"}
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gongsi.io import loader  # noqa: E402

# 서술 항목의 머리말. 번호는 서식마다 다르므로(13·14·16·17…) 번호를 보지 않는다.
HEAD = re.compile(
    r"(기타\s*투자판단[에과][^\n]{0,20}|향후\s*회사구조개편에\s*관한\s*계획|"
    r"기타\s*투자판단과\s*관련한\s*중요사항)")
# 다음 항목이 시작되면 멈춘다. 서식 항목은 「N. 」 꼴이고, 확인 블록·서명이 뒤에 온다.
STOP = re.compile(r"\d{1,2}\s*\.\s*[가-힣]{2,}|【\s*대표이사|사외이사의\s*참석여부|"
                  r"감사\s*\(감사위원|주요사항보고서\s*/|거래소\s*신고의무")
MAX_CHARS = 2000


def notes_of(file_path: str) -> str:
    """원문에서 서술 항목 본문을 잘라 낸다 (자르는 규칙의 «유일한» 자리)."""
    try:
        docs = loader.read_doc(file_path)
    except Exception:                                          # noqa: BLE001
        return ""
    out: list[str] = []
    for _name, text in docs:
        t = re.sub(r"<[^>]+>", " ", text.replace("&nbsp;", " "))
        t = re.sub(r"[ \t]+", " ", t)
        for m in HEAD.finditer(t):
            seg = t[m.end(): m.end() + MAX_CHARS * 2]
            # ★ 2026-08-30 (R7-048) — **본문이 인용한 항목 번호가 STOP 으로 잡혔다.**
            #   「상기 '**2.**사채의 권면총액'은 …」에서 끊겨 그 뒤 「이사회에서 결의한
            #   발행금액인 **4,000억원** 이내에서 결정할 예정」이 통째로 빠졌다.
            #   따옴표 안은 «인용»이지 «다음 항목»이 아니다 — 마스킹한 뒤 STOP 을 찾는다.
            _masked = re.sub(r"['\u2018\u2019\"\u201c\u201d][^'\u2018\u2019\"\u201c\u201d\n]{0,40}"
                             r"['\u2018\u2019\"\u201c\u201d]",
                             # ★ 심문 13차 — 채움을 «공백»으로 하면 `\s*` 가 이어져
                             #   **없던 STOP 을 만든다**(「상기 4.'보유예상기간' 관련하여」에서
                             #   따옴표를 지우자 「4. 관련하여」가 STOP 으로 잡혔다).
                             #   한 글자만 바꾸면 해결된다 — 재빌드로 줄어든 문서 5 → 0.
                             lambda mm: "_" * len(mm.group(0)), seg)
            stop = STOP.search(_masked)
            body = seg[: stop.start()] if stop else seg
            body = re.sub(r"\s+", " ", body).strip(" -·:")
            # 「해당사항 없습니다」도 값이다(평가지표 7) — 버리지 않는다.
            if len(body) >= 4:
                line = f"{m.group(0).strip()}: {body[:MAX_CHARS]}"
                if line not in out:
                    out.append(line)
        if out:
            break
    return "\n".join(out)[: MAX_CHARS * 2]


def main() -> int:
    corpus = loader.load_corpus()
    rows = [m for m in corpus.manifest if m.get("doc_group") == "major"]
    dst = Path(loader.DERIVED_DIR) / "db" / "major_notes.jsonl"
    dst.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    total = 0
    with open(dst, "w", encoding="utf-8") as fh:
        for i, m in enumerate(rows, 1):
            s = notes_of(m["file_path"])
            if not s:
                continue
            n += 1
            total += len(s)
            fh.write(json.dumps({"rcept_no": m["rcept_no"], "notes": s},
                                ensure_ascii=False) + "\n")
            if i % max(1, len(rows) // 10) == 0:
                print(f"  … {i}/{len(rows)}", flush=True)
    print(f"major {len(rows)}건 → 서술 항목 {n}건 ({n / max(len(rows), 1):.1%}) · "
          f"평균 {total // max(n, 1):,}자")
    print(f"  → {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
