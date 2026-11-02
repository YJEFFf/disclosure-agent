# -*- coding: utf-8 -*-
"""증권 발행 공시의 «회차»를 원문에서 사전 추출한다 → db/ordinals.jsonl (R48-3).

★ 왜 스토어인가 — 회차(「제17회」·표의 「회차 | 17」)는 events raw 어디에도 없고
  (우리금융 상각형 21건 전수 실측: raw 표기 0건), 원문 표에만 산다. 질의 시점에
  원문을 열면 개발기에선 돌지만 **배송 트리에는 major 원문이 없다**(D-2 사전 추출
  원칙) — 로컬에서만 점수가 나는 죽은 기능을 만들지 않으려면 스토어로 잘라 둔다.

대상: major 전체(598 폴더). 캐치: ① 본문 «제 N 회» ② 태그 제거 후 «회차 … N»
  (표 셀 — 우리금융 20240607000124 실측 서식). 회차가 없으면 행을 쓰지 않는다.
검증: 스크립트 끝에 건수·표본을 찍는다. 실행: GONGSI_DATA_ROOT 걸고
  `python3 scripts/run_build_ordinals.py`.
"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gongsi.io import loader  # noqa: E402

_RE_TAGS = re.compile(r"<[^>]+>")
_RE_JE = re.compile(r"제\s*(\d{1,3})\s*회(?!계)")
_RE_CELL = re.compile(r"회\s*차\W{0,12}?(\d{1,3})(?!\d)")
# ★★ 2026-09-04 (R49 · 심문 반박 ②) — 「기발행미상환사채권」 표는 **남의 회차**를 싣는다.
#   그 표는 이 공시가 발행하는 사채가 아니라 **이전에 발행해 아직 안 갚은** 사채들의
#   목록이다. 그걸 함께 채굴해서 저장 회차 129개 중 **21개(16.3%)가 그 문서 것이 아니었고**,
#   R48-5 핀이 「원문 «회차 N» 실측」이라는 **권위를 달고 엉뚱한 공시를 지목**했다
#   (R8-041·R8-042 에서 6개 공시를 「회차 14」라 단언 — 실제로 회차 셀이 14인 것은 1개).
#   함정 62 의 「코드가 틀린 것을 단언한다」가 스토어 층에서 재현된 것이다.
#
#   처방 실측(`scratchpad_R48Y/ord_audit.py` · major 전수 108문서):
#       「기발행미상환사채권」 보유 문서 22건 · 회차 값 129 → 115 (잉여 14 걷힘)
#       값이 걷힌 문서 6건 · **회차가 통째로 사라지는 문서 0건**
#   그리고 걷히는 모양이 스스로를 증명한다 — 문서마다 «다음 회차» 하나만 남고
#   이전 발행분이 버려진다(20250717000413: 14·15·16·17 걷고 **18** 남김).
_RE_PRIOR = re.compile(r"기\s*발\s*행\s*미\s*상\s*환\s*사\s*채\s*권")


def mine(text: str) -> list[int]:
    flat = _RE_TAGS.sub(" ", text)
    # ★ 「기발행미상환사채권」이 나오면 **그 앞까지만** 본다. 그 표는 문서 끝의 부표이고
    #   이 공시 자신의 회차는 서식 앞쪽(표제·발행조건)에 있다 — 실측으로 소실 0 확인.
    _m = _RE_PRIOR.search(flat)
    if _m:
        flat = flat[:_m.start()]
    out = {int(m.group(1)) for m in _RE_JE.finditer(flat)}
    out |= {int(m.group(1)) for m in _RE_CELL.finditer(flat)}
    # 연도·큰 수 오염 방지 — 발행 회차는 실무상 세 자리를 넘지 않는다(패턴이 이미 1~3자리).
    return sorted(x for x in out if 1 <= x <= 999)


def main() -> int:
    manifest = [json.loads(line) for line in
                (Path(loader.META_DIR) / "manifest.jsonl").open(encoding="utf-8")]
    out_path = Path(loader.DERIVED_DIR) / "db" / "ordinals.jsonl"
    n_doc = n_hit = 0
    with out_path.open("w", encoding="utf-8") as f:
        for m in manifest:
            if m.get("doc_group") != "major":
                continue
            n_doc += 1
            # ★ R49 — 파일마다 따로 채굴하지 «않는다». 「기발행미상환사채권」 절단은
            #   문서 «전체» 위에서 판정해야 하고(그 표가 다른 파일에 있을 수 있다),
            #   실측(`ord_audit.py`)도 합친 텍스트에서 쟀다. **구현과 측정을 일치시킨다.**
            try:
                _texts = [t for _name, t in loader.read_doc(m["file_path"])]
            except Exception as e:                 # noqa: BLE001
                print(f"  ⚠ {m['rcept_no']} 읽기 실패: {e}", file=sys.stderr)
                continue
            ords = set(mine("\n".join(_texts)))
            if ords:
                f.write(json.dumps({"rcept_no": m["rcept_no"],
                                    "ordinals": sorted(ords)},
                                   ensure_ascii=False) + "\n")
                n_hit += 1
    print(f"major {n_doc}건 중 회차 검출 {n_hit}건 → {out_path}")
    if n_hit < 50:
        print("  ⚠ 검출이 50건 미만 — 패턴이 서식을 놓쳤을 수 있다. 표본을 열어 볼 것.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
