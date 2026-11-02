# -*- coding: utf-8 -*-
r"""코퍼스 내 list_*.json(DART list API 원본) → 전체 공시 카탈로그.

수집 대상 4종 '밖' 공시(IR 안내·공정공시·기타 경영사항 등)도 목록에는 있다.
"OO가 X일에 공시한 △△ 내용은?" 질의에서 해당 공시가 코퍼스 밖임을
접수번호와 함께 밝히기 위한 재료 (평가 absent_with_pointer 유형).

출력: 02_데이터\03_가공\db\catalog.jsonl (rcept_no 단위, in_corpus 표시)
실행: py -3 scripts\run_build_catalog.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "gongsi" / "io"))
import loader  # noqa: E402


def main() -> int:
    c = loader.load_corpus()
    in_corpus = {r["rcept_no"] for r in c.manifest}

    seen: dict[str, dict] = {}
    n_files = 0
    for f in Path(loader.RAW_DIR).rglob("list_*.json"):
        n_files += 1
        try:
            items = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        for it in items if isinstance(items, list) else items.get("list", []):
            no = it.get("rcept_no")
            if not no or no in seen:
                continue
            seen[no] = {
                "rcept_no": no,
                "corp_name": loader.nfc(it.get("corp_name", "")),
                "report_nm": " ".join((it.get("report_nm") or "").split()),
                "rcept_dt": it.get("rcept_dt"),
                "in_corpus": no in in_corpus,
            }

    out = Path(loader.DERIVED_DIR) / "db" / "catalog.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for row in sorted(seen.values(), key=lambda r: r["rcept_no"]):
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    n_out = sum(1 for r in seen.values() if not r["in_corpus"])
    print(f"list 파일 {n_files}개 → 카탈로그 {len(seen):,}건 "
          f"(코퍼스 내 {len(seen) - n_out:,} · 밖 {n_out:,})")
    print(f"출력: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
