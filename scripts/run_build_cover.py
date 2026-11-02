"""정기공시 «표지 표» 사전 추출 — `cover_fields.jsonl`.

★ 2026-08-30 (NT-115). 표지 표는 첫 `<TITLE>` **이전**에 있어 `parse_periodic` 의 섹션
  분해에서 통째로 빠진다 — 「본  점  소  재  지 : 서울특별시 중구 세종대로7길 37(순화동)」이
  원문에 그대로 있는데 섹션 DB 검색으로는 **한 건도** 닿지 않는다(실측: `본 점 소 재 지`
  LIKE 0건). `correction_header` 가 같은 이유로 이미 이 길을 냈다 — 같은 처방을 쓴다.

  라벨은 **자간 공백**(「본  점  소  재  지」 — 공백 두 칸)이라 질문의 한 칸 표기와도,
  `[가-힣]{2,}` 토큰화와도 맞지 않는다. 그래서 공백을 지운 키로 저장한다.

모집단(실측): 정기공시 **1,054건** 중 표지 쌍이 잡히는 문서 **947건(89.8%)** ·
  회사명 942 · 대표이사 938 · 본점소재지 938 · 작성책임자 912.
  (앞 숫자를 한 번 틀리게 적었다 — 원문 파일 수와 문서 수를 섞어 셌다. 심문 8차 지적.)

산출: `03_가공/db/cover_fields.jsonl` — {"rcept_no": …, "fields": {"본점소재지": …}}
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gongsi.io import loader  # noqa: E402

# 표지 표는 `<TD>라벨 :</TD><TD>값</TD>` 두 칸 구조다. 라벨 칸 안에 콜론이 붙어 있다.
# ★ 2026-09-05 (R52 · NT-143) — **값 칸이 `<TU>` 인 필드가 있다** (함정 27 의 표지판).
#   「제출대상법인 유형 :」의 값 「주권상장법인」이 `<TU AUNIT="PB_TYPE">` 에 있어
#   TD-TD 쌍만 읽던 이 정규식이 통째로 놓쳤다(원문 실측 · 현대건설 20240314001734).
#   값 칸을 TD|TU 로 넓힌다 — 라벨 칸(콜론 포함)은 TD 그대로다.
PAIR = re.compile(
    r"<TD[^>]*>\s*([^<>]{2,40}?)\s*:\s*</TD>\s*<T[DU][^>]*>\s*([^<>]{1,120}?)\s*</T[DU]>", re.S)
# 표지에서만 읽는다 — 본문까지 훑으면 다른 표의 라벨이 섞인다.
# ★ 2026-08-30 심문 8차 — **앞 14,000자 고정은 정정본을 통째로 놓친다.** 추출 실패
#   107건이 **100% 정정본**이었다: 정정 사유·내용 표가 앞머리에 붙어 표지가 뒤로 밀린다
#   (레인보우로보틱스 16,219 · 기아 20,416 · 고려아연 **183,900**).
#   원문은 표지 표 직전에 `<COVER>` 태그를 두므로 **그 자리를 기준점**으로 잡는다.
HEAD_CHARS = 14000
# 「1. 정정대상 공시서류」처럼 번호가 붙은 정정 헤더 항목은 표지 필드가 아니다.
_SKIP = re.compile(r"^\s*\d+\s*\.|^\(")


def cover_fields(file_path: str) -> dict[str, str]:
    """원문 표지에서 (공백 제거 라벨 → 값). 자르는 규칙의 «유일한» 자리."""
    try:
        docs = loader.read_doc(file_path)
    except Exception:                                          # noqa: BLE001
        return {}
    out: dict[str, str] = {}
    for _name, text in docs:
        # 자간을 `&nbsp;` 로 적은 문서가 있다 — 공백과 같이 본다(실측 1건).
        text = text.replace("&nbsp;", " ")
        _c = text.find("<COVER")
        _seg = text[_c:_c + HEAD_CHARS] if _c >= 0 else text[:HEAD_CHARS]
        for lab, val in PAIR.findall(_seg):
            lab = lab.strip()
            if _SKIP.match(lab) or not val.strip():
                continue
            key = re.sub(r"\s+", "", lab)
            if key and key not in out:
                out[key] = re.sub(r"\s+", " ", val.strip())
        if out:
            break
    return out


def main() -> int:
    corpus = loader.load_corpus()
    rows = [m for m in corpus.manifest if m.get("doc_group") == "periodic"]
    dst = Path(loader.DERIVED_DIR) / "db" / "cover_fields.jsonl"
    dst.parent.mkdir(parents=True, exist_ok=True)
    n_doc = 0
    with open(dst, "w", encoding="utf-8") as fh:
        for m in rows:
            f = cover_fields(m["file_path"])
            if not f:
                continue
            n_doc += 1
            fh.write(json.dumps({"rcept_no": m["rcept_no"], "fields": f},
                                ensure_ascii=False) + "\n")
    print(f"정기공시 {len(rows)}건 → 표지 필드 {n_doc}건 ({n_doc / max(len(rows), 1):.1%})")
    print(f"  → {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
