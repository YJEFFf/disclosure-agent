# -*- coding: utf-8 -*-
r"""감사보고서 SUMMARY(EXTRACTION) 블록 → fin_summary 테이블.

실측(작업일지 [32]): 금융지주·보험사 등의 공식 재무 요약값(TOT_SALES·TOT_ASSETS 등)은
본문 요약재무 표가 아니라 첨부 감사보고서의 기계판독용 <EXTRACTION> 블록에 있다.
섹션 파서(SECTION 태그 기준)는 이 블록을 버리므로 별도 수집한다. 단위: 백만원.

scope(연결/별도)는 같은 파일의 섹션 제목('연결재무제표' 포함 여부)으로 판정.
출력: section.sqlite 의 fin_summary 테이블 (rcept_no·file·scope·acode·value)

실행: py -3 -X utf8 scripts\run_build_finsummary.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "gongsi" / "io"))
sys.path.insert(0, str(ROOT / "src" / "gongsi" / "index"))
import loader     # noqa: E402
import sectiondb  # noqa: E402

_RE_EXT = re.compile(r'<EXTRACTION\s+ACODE="([A-Z0-9_]+)"[^>]*>([^<]*)</EXTRACTION>')

SCHEMA = """
DROP TABLE IF EXISTS fin_summary;
CREATE TABLE fin_summary (
    rcept_no    TEXT,
    corp_name   TEXT,
    doc_subtype TEXT,
    base_year   INTEGER,
    file        TEXT,
    scope       TEXT,      -- 연결 | 별도 | 불명
    acode       TEXT,
    value       TEXT
);
CREATE INDEX idx_fs ON fin_summary(corp_name, base_year, acode);
"""


def main() -> int:
    c = loader.load_corpus()
    conn = sectiondb.connect()

    # 파일별 scope 판정 재료: sections 테이블의 (rcept_no, file) → 제목들
    file_titles: dict[tuple, str] = {}
    for r in conn.execute("SELECT rcept_no, file, GROUP_CONCAT(sec_title) t FROM sections GROUP BY 1,2"):
        file_titles[(r["rcept_no"], r["file"])] = r["t"] or ""

    conn.executescript(SCHEMA)
    rows = []
    docs = [r for r in c.manifest if r["doc_group"] == "periodic"]
    for i, doc in enumerate(docs, 1):
        try:
            files = loader.read_doc(doc["file_path"])
        except Exception:                                    # noqa: BLE001
            continue
        for name, text in files:
            head = text[:20000]
            if "<SUMMARY" not in head and "EXTRACTION" not in head:
                continue
            hits = _RE_EXT.findall(head)
            if not hits:
                continue
            # ★ 2026-08-15 실측 버그: 제목만으로 판정하면 감사보고서 첨부는 **섹션이 없어**
            #   titles 가 비고, `_00760`(별도)·`_00761`(연결)이 **둘 다 '별도'** 로 들어갔다.
            #   JYP Ent 2024 가 TOT_SALES 488,264(별도)·601,788(연결)을 모두 '별도'로 달아
            #   「별도 매출액」 질의에 값이 둘이 되어 답을 못 냈다 (실측 F-002·F-007·F-009·F-015).
            #
            #   파일 접미사가 확실한 신호다 — 원문 헤더로 확인함:
            #     …_00760.xml = 「감사보고서」(별도)   …_00761.xml = 「연결감사보고서」
            #   그래서 접미사를 1순위로, 본문 표제를 2순위로, 섹션 제목을 3순위로 본다.
            titles = file_titles.get((doc["rcept_no"], name), "")
            if re.search(r"_00761\.xml$", name):
                scope = "연결"
            elif re.search(r"_0076\d\.xml$", name):
                scope = "별도"
            elif "연결감사보고서" in head or "연결재무제표" in head:
                scope = "연결"
            else:
                scope = "연결" if "연결" in titles else ("별도" if titles else "불명")
            for acode, val in hits:
                rows.append([doc["rcept_no"], doc["corp_name"], doc["doc_subtype"],
                             doc.get("base_year"), name, scope, acode, val.strip()])
        if i % 300 == 0:
            print(f"  {i:,}/{len(docs):,} … {len(rows):,}행")

    conn.executemany("INSERT INTO fin_summary VALUES (?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    n_sales = conn.execute("SELECT COUNT(*) FROM fin_summary WHERE acode='TOT_SALES'").fetchone()[0]
    print(f"fin_summary {len(rows):,}행 (TOT_SALES {n_sales}건)")
    # 검증: 메리츠 별도 2025 = 1,264,249 (실측 gold)
    r = conn.execute("""SELECT value FROM fin_summary WHERE corp_name='메리츠금융지주'
                        AND base_year=2025 AND scope='별도' AND acode='TOT_SALES'
                        ORDER BY rcept_no DESC LIMIT 1""").fetchone()
    print("검증 메리츠 별도 2025 TOT_SALES:", r[0] if r else "없음", "(기대 1264249)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
