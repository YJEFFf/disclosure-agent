#!/usr/bin/env python3
"""배송 트리 분리기 — **「무엇을 여는가」와 「무엇을 싣는가」는 다른 질문이다.**

    python3 scripts/stage_deploy.py            # var/deploy/ 를 짓는다
    python3 scripts/stage_deploy.py --verify   # 짓고 나서 전수 검증까지

왜 필요한가 (2026-08-20 전수 측정):

    원문 전체                                  5,301 MB
    파생물 전체                                2,108 MB
    ─────────────────────────────────────────────────
    통째로 실으면                              7,409 MB

    런타임이 실제로 여는 것                    ≈1,200 MB   ← 이 스크립트가 만드는 것

★★ 2026-09-04 (R49) — **위 문장은 낡았고, 그 낡음이 배포본에서 조용히 답을 갉아먹었다.**

    2026-08-20 자 `grep parse_doc` 전수는 「원문을 여는 자리는 정확히 두 곳」이라 했고
    이 스크립트 전체가 그 문장 하나에 얹혀 있었다. 그런데 그 뒤 **R72(2026-09-02)에
    격자 핀 `grid_header_pin` 이 들어왔고, 그것이 `_grid_doc()` 에서 periodic 원문을
    질의 시점에 연다.** periodic 을 안 실으니 배송본에서는 **첫 질의부터**
        [경고] grid_header_pin 원문 파싱 실패 …: 원문 없음: raw/periodic/…
    가 찍히고 그 핀이 통째로 침묵한다. 표본 60 짝지은 차분 실측 —
    격자 핀 발화 **개발기 4 → 배송 0**, 잃은 문항에 **R12-041**(이 핀이 만들어진 바로 그
    부검 문항)이 들어 있었다.

    **채점은 우리 배포 서버를 때린다.** 배포본에서 침묵하는 핀은 값이 0이고, 더 나쁘게는
    **개발기에서 잰 모든 수치가 배포본을 과대평가**하게 된다.
    → **원문을 싣는다.** `answerer.py` 를 안 건드리므로 A/B 재실행이 필요 없고,
      그 즉시 **개발기 = 배포본**이 되어 우리가 인용하는 수치가 정직해진다.
    → 기각한 대안: ①격자 표 사전추출 스토어(옳은 공학이지만 answerer 변경 → 전수 A/B 재실행 ·
      71% 잡음 이력이 있는 핀을 프리즈 전날 건드리는 위험 · 함정 70) ②손실 수용(점수를
      알면서 버리고 측정값과 배포본이 계속 어긋난다).

    ★ 교훈은 「목록을 늘려라」가 아니라 **「이 docstring 이 근거다」라는 구조가 위험하다**는
      것이다. 원문 개방 자리는 **핀이 하나 늘 때마다** 늘 수 있다. 그래서 이제
      **전 그룹을 싣고**, 「무엇을 여는가」에 배송 정합성을 걸지 않는다.

런타임이 원문을 여는 자리 (2026-09-04 재감사 · 셋):
    agent/answerer.py  _corr_segment()  → periodic 정정본 (스토어가 가로챈다 · 1,004/1,004 전문 동일)
    agent/answerer.py  exchange_body()  → exchange 서술형 (함정 26)
    agent/answerer.py  _grid_doc()      → periodic 본문 (grid_header_pin · R72)

정정 헤더는 여기서 미리 잘라 스토어로도 만든다(922MB → ~2MB · 원문 개방을 줄여 빠르다).

★ 하드링크로 짓는다 — 같은 파일시스템이면 **디스크를 0바이트도 더 쓰지 않는다.**
★ 끝에 「했다」가 아니라 **「됐는지 확인한 결과」**를 찍고 끝난다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "gongsi" / "io"))
sys.path.insert(0, str(ROOT / "src" / "gongsi" / "index"))
sys.path.insert(0, str(ROOT / "src" / "gongsi" / "llm"))
sys.path.insert(0, str(ROOT / "src" / "gongsi" / "agent"))

import loader  # noqa: E402

MB = 1024 * 1024

# ── 배송 트리에 싣는 것 ──────────────────────────────────────────────────
#   corpus/  주최측 코퍼스 중 런타임이 여는 부분  (읽기전용으로 마운트)
#   stores/  우리가 만든 파생물 중 런타임이 여는 것 (읽기전용으로 마운트)
#
# ★ 이 목록이 «단일 진실원»이다. 늘리기 전에 「런타임이 정말 여는가」를 grep 으로 증명하라.
CORPUS_FILES = [
    ("manifest.jsonl", "meta"),      # 4,204문서 메타 — corpus() 가 매번 읽는다
    ("universe.csv", "meta"),        # 70개사 — 사명 별칭의 모집단
]
# ★★ R49 (2026-09-04) — **전 그룹을 싣는다.** 종전 `["exchange"]` 는 `_grid_doc()` 이
#   periodic 을 여는 것을 몰라서(docstring 참조) 배포본에서 격자 핀을 통째로 죽이고 있었다.
#   원문 개방 자리는 «핀이 하나 늘 때마다» 늘 수 있으므로, 목록을 좇는 대신 전부 싣는다.
#   대가는 배송 트리 ~1.23GB → ~6.4GB (하드링크라 **로컬 디스크 추가 소모는 0**).
#   얻는 것은 «개발기 = 배포본» — 우리가 인용하는 모든 수치가 배포본을 그대로 기술한다.
CORPUS_RAW_GROUPS = ["exchange", "periodic", "major", "holding"]

STORE_FILES = [
    "db/event.sqlite",               # 이벤트 검색
    "db/section.sqlite",             # 정기공시 섹션 (+FTS5) — 트리에서 가장 크다
    "db/catalog.jsonl",              # 비수집 공시 카탈로그
    "db/chains.jsonl",               # 정정 체인
    "db/correction_headers.jsonl",   # ★ 이 스크립트가 만든다
    "db/cover_fields.jsonl",         # 정기공시 표지 표 (cover_field_pin — NT-115)
    "db/major_notes.jsonl",          # major 서술 항목 (major_notes — R6N 발견 ⑥)
    "db/holding_tables.jsonl",       # 대량보유 요약·계약 표 (R6N-059/068)
    "db/corr_items.jsonl",           # 정정사항 표 (항목·정정전·정정후 — R6N-050)
    "db/ordinals.jsonl",             # major 증권 발행 «회차» (R48-3 · R10-052)
]

# 실으면 «안 되는» 것 — 빌드 전용이라 런타임이 한 번도 열지 않는다
NEVER_SHIP = ["db/sections.jsonl", "db/events.jsonl", "db/related.jsonl"]


def human(n: int) -> str:
    return f"{n / MB:,.1f} MB"


def link_or_copy(src: Path, dst: Path) -> int:
    """하드링크 시도 → 안 되면 복사. 실린 바이트 수를 돌려준다."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:                                            # 다른 파일시스템 등
        shutil.copy2(src, dst)
    return src.stat().st_size


# ══════════════════════════════════════════════════════════════════════
#  ① 정정 헤더 사전 추출
# ══════════════════════════════════════════════════════════════════════
def build_correction_store(out_path: Path) -> tuple[int, int]:
    """정정본 전수의 「정정신고」 블록을 잘라 jsonl 로.

    ★ 자르는 코드를 여기 «복제하지 않는다» — `answerer._corr_segment()` 를 그대로 부른다.
      복제하면 언젠가 한쪽만 고쳐지고, 그러면 개발기와 배포본의 컨텍스트가 갈린다
      (함정 23 — 「한쪽에만 있는 처방」이 이 프로젝트에서 다섯 번 났다).
    """
    import answerer

    man = [m for m in loader.load_corpus().manifest if m.get("is_correction")]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n_empty = 0
    t0 = time.time()
    with open(out_path, "w", encoding="utf-8") as fh:
        for i, m in enumerate(man, 1):
            seg = answerer._corr_segment(m)
            if not seg:
                n_empty += 1
            fh.write(json.dumps({"rcept_no": m["rcept_no"], "seg": seg},
                                ensure_ascii=False) + "\n")
            if i % max(1, len(man) // 20) == 0:
                print(f"    … {i}/{len(man)} ({i * 100 // len(man)}%)", flush=True)
    print(f"    정정본 {len(man)}건 · 빈 블록 {n_empty}건 · {time.time() - t0:.1f}초")
    return len(man), n_empty


# ══════════════════════════════════════════════════════════════════════
#  ② 배송 트리
# ══════════════════════════════════════════════════════════════════════
def stage(deploy_dir: Path) -> dict:
    corpus_dir = deploy_dir / "corpus"
    stores_dir = deploy_dir / "stores"
    if deploy_dir.exists():
        shutil.rmtree(deploy_dir)

    report: dict[str, int] = {}

    print("\n── 1/3  정정 헤더 · 표지 필드 사전 추출")
    corr_path = Path(loader.DERIVED_DIR) / "db" / "correction_headers.jsonl"
    build_correction_store(corr_path)
    # 표지 표는 첫 TITLE 이전이라 섹션 DB 에 없다 — 런타임(`cover_field_pin`)이 이 파일을
    # 연다. 자르는 코드는 `run_build_cover.py` 한 곳에만 둔다 (함정 23).
    # ★ 심문 8차 지적 — `if not exists` 로만 빌드하면 **낡은 스토어가 그대로 실린다**
    #   (원문·추출 규칙이 바뀌어도 갱신되지 않는다). 정정 헤더처럼 **매번 다시 만든다** —
    #   1,054문서 추출은 배포 준비 시간에 견줘 싸다.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import run_build_cover
    run_build_cover.main()
    # major 서술 항목도 같은 이유(런타임이 major 원문을 열지 않는다)로 사전 추출한다.
    import run_build_majornotes
    run_build_majornotes.main()
    import run_build_holdingtables
    run_build_holdingtables.main()
    import run_build_corritems
    run_build_corritems.main()

    print("\n── 2/3  코퍼스 (런타임이 여는 것만)")
    n_bytes = n_files = 0
    for name, _kind in CORPUS_FILES:
        src = Path(loader.META_DIR) / name
        if not src.exists():
            raise FileNotFoundError(f"메타 파일이 없습니다: {src}")
        n_bytes += link_or_copy(src, corpus_dir / name)
        n_files += 1
    for group in CORPUS_RAW_GROUPS:
        gdir = Path(loader.RAW_DIR) / group
        if not gdir.exists():
            raise FileNotFoundError(f"원문 그룹이 없습니다: {gdir}")
        for f in gdir.rglob("*"):
            if f.is_file():
                n_bytes += link_or_copy(f, corpus_dir / group / f.relative_to(gdir))
                n_files += 1
    report["corpus_bytes"], report["corpus_files"] = n_bytes, n_files
    print(f"    {n_files:,}파일 · {human(n_bytes)}")

    print("\n── 3/3  스토어")
    n_bytes = n_files = 0
    for rel in STORE_FILES:
        src = Path(loader.DERIVED_DIR) / rel
        if not src.exists():
            raise FileNotFoundError(
                f"스토어가 없습니다: {src}\n  전처리를 먼저 돌리십시오 (README §재현 방법).")
        n_bytes += link_or_copy(src, stores_dir / rel)
        n_files += 1
    report["stores_bytes"], report["stores_files"] = n_bytes, n_files
    print(f"    {n_files:,}파일 · {human(n_bytes)}")
    return report


# ══════════════════════════════════════════════════════════════════════
#  ③-보조  «글자 수» 하한 — 건수·파일 크기는 내용을 보증하지 않는다
# ══════════════════════════════════════════════════════════════════════
def _payload_chars(obj) -> int:
    """json 오브젝트를 재귀로 내려가 «문자열 값»의 길이만 합산한다.

    직렬화 길이(행 길이)로 재면 키 이름·이스케이프가 절반을 차지해
    (cover_fields 실측: 직렬화 157,811자 중 값 텍스트는 77,446자 = 49%)
    내용 손실이 희석된다 — 값 텍스트만 세야 −22% 손실이 −22% 로 보인다.
    """
    if isinstance(obj, str):
        return len(obj)
    if isinstance(obj, dict):
        return sum(_payload_chars(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return sum(_payload_chars(v) for v in obj)
    return 0


def char_floor_checks(stores_dir: Path) -> list[tuple[bool, str]]:
    """버킷·스토어별 «글자 수» 하한 검사 — (통과여부, 메시지) 목록을 돌려준다.

    ★ 왜 생겼나 (2026-08-31 실사고): `<한글` 파서 병합이 holding_tables 의
      `note_rows` 버킷을 **건수 376건 그대로 두고** 내용만 250,875 → 194,581자
      (−22.4%)로 줄였는데, 당시 verify 30항목(건수 하한 포함)이 **전부 통과**했다.
      같은 날 section.sqlite 는 47,853행본과 47,163행본이 **똑같이
      1,240,666,112바이트**였다 — 건수도 파일 크기도 내용을 보증하지 않는다.
      → **글자 수를 함께 본다.**
    ★ 하한은 실측 기준선(2026-09-01 측정)의 **약 90%** 리터럴이다 —
      −22.4% 급 손실은 반드시 걸리고, 정상 재빌드의 미세 변동은 안 걸린다.
      기준선이 «정당하게» 움직이면(스토어 확장 등) 재측정해서 숫자를 갱신하라.
    ★ verify() 밖의 독립 함수인 이유: 「검사기는 자기 검출력을 먼저 증명한다」 —
      검출력 시험(손상본 주입)이 이 함수를 «단독으로» 불러야 한다.
    """
    import json as _json
    import sqlite3
    from urllib.parse import quote
    out: list[tuple[bool, str]] = []

    # (a) holding_tables — 버킷별 글자 수(리스트 원소 문자열 길이 합).
    #     실측 기준선 2026-09-01: summary 405,005 · contract 615,637 · fund 726,882 ·
    #     coll 181,215 · bytype 1,021,442 · note 250,875
    #     (note 250,875 는 8/31 사고 당시 «손실 전» 값과 정확히 일치 — 축이 맞다)
    _floors = {"summary_rows": 364_000, "contract_rows": 554_000,
               "fund_rows": 654_000, "coll_rows": 163_000,
               "bytype_rows": 919_000, "note_rows": 225_000}
    _hp = stores_dir / "db/holding_tables.jsonl"
    if not _hp.exists():
        out.append((False, "글자수 검사 대상 없음: db/holding_tables.jsonl"))
    else:
        _ch = {k: 0 for k in _floors}
        for _l in open(_hp, encoding="utf-8"):
            if not _l.strip():
                continue
            _d = _json.loads(_l)
            for _k in _ch:
                _ch[_k] += sum(len(str(s)) for s in (_d.get(_k) or []))
        for _k, _floor in _floors.items():
            out.append((_ch[_k] >= _floor,
                        f"대량보유 {_k} 글자 {_ch[_k]:,} (>= {_floor:,})"))

    # (b) 사전 추출 스토어 — 파일별 «값 텍스트» 총 글자 수.
    #     실측 기준선 2026-09-01: cover 77,446 · major_notes 641,302 · corr_items 315,616
    #     (correction_headers 는 (3) 의 전수 바이트 대조가 더 센 검사라 여기서 뺀다)
    for _rel, _floor, _name in (("db/cover_fields.jsonl", 69_000, "표지 표"),
                                ("db/major_notes.jsonl", 577_000, "major 서술항목"),
                                ("db/corr_items.jsonl", 284_000, "정정사항 표")):
        _p = stores_dir / _rel
        if not _p.exists():
            out.append((False, f"글자수 검사 대상 없음: {_rel}"))
            continue
        _n = sum(_payload_chars(_json.loads(_l))
                 for _l in open(_p, encoding="utf-8") if _l.strip())
        out.append((_n >= _floor, f"{_name} 값텍스트 {_n:,}자 (>= {_floor:,})"))

    # (c) ★★ R49 (2026-09-04) — `ordinals` 는 **글자 하한에 둘 수 없다.**
    #   R48-3 이 「기준선 5,352**바이트**」를 근거로 하한 4,500 을 (b) 에 꽂았는데,
    #   (b) 가 재는 것은 `_payload_chars()` = **문자열 값 길이**다. 그런데
    #       {"rcept_no": "20230630000403", "ordinals": [5]}
    #   는 값이 **정수 리스트**라 세어지는 것이 `rcept_no` 14자 × 108 = 1,512자뿐 —
    #   **어떤 정상 스토어도 4,500 을 못 넘는다.** 검출력 시험이 그것을 증명했다:
    #   무손상도 실패, ordinals 를 0건으로 비워도 실패 → **판별력 0인 「늘 거짓」 검사**.
    #   R48 은 이 스토어를 등록만 하고 `--verify` 를 한 번도 안 돌린 것이다.
    #   → `_payload_chars` 를 고치지 «않는다» (고치면 위 실측 하한들이 통째로 무효가 된다).
    #     대신 이 스토어에 **자기 축**을 준다 — 건수와 «회차 값 개수».
    #   ★ 기준선을 갱신했다: 2026-09-03 은 108건·**129개**였는데, 같은 날 심문이
    #     「기발행미상환사채권」 오염 21개를 잡아 추출기를 고쳤다(→ 108건·**115개**).
    #     **기준선이 «정당하게» 움직이면 하한을 재측정해 갱신한다** — 안 그러면
    #     이 검사가 「늘 거짓」으로 되돌아간다(이 스토어가 이미 한 번 그랬다).
    _op = stores_dir / "db/ordinals.jsonl"
    if not _op.exists():
        out.append((False, "검사 대상 없음: db/ordinals.jsonl"))
    else:
        _rows = [_json.loads(_l) for _l in open(_op, encoding="utf-8") if _l.strip()]
        _vals = sum(len(_d.get("ordinals") or []) for _d in _rows)
        out.append((len(_rows) >= 100, f"회차 스토어 {len(_rows):,}건 (>= 100)"))
        out.append((_vals >= 103, f"회차 값 {_vals:,}개 (>= 103)"))

    # (c) section.sqlite — 행 수 + 본문 총 글자 수. (SUM(LENGTH) 실측 5초 — 쌀 때 넣는다)
    #     실측 기준선 2026-09-01: 47,853행 · SUM(LENGTH(text)) 510,156,649자
    _sp = stores_dir / "db/section.sqlite"
    if not _sp.exists():
        out.append((False, "글자수 검사 대상 없음: db/section.sqlite"))
    else:
        _con = sqlite3.connect("file:" + quote(str(_sp)) + "?mode=ro", uri=True)
        _rows, _chars = _con.execute(
            "SELECT COUNT(*), SUM(LENGTH(text)) FROM sections").fetchone()
        _con.close()
        out.append((_rows >= 43_000, f"섹션 행 {_rows:,} (>= 43,000)"))
        out.append(((_chars or 0) >= 459_000_000,
                    f"섹션 본문 글자 {(_chars or 0):,} (>= 459,000,000)"))
    return out


# ══════════════════════════════════════════════════════════════════════
#  ③ 검증 — 「했다」가 아니라 「됐는지 확인하는 명령」
# ══════════════════════════════════════════════════════════════════════
def verify(deploy_dir: Path, report: dict) -> int:
    print("\n══ 검증 ══════════════════════════════════════════════════")
    fail = 0

    def check(ok: bool, msg: str) -> None:
        nonlocal fail
        print(f"  {'✅' if ok else '★★'} {msg}")
        fail += (not ok)

    corpus_dir, stores_dir = deploy_dir / "corpus", deploy_dir / "stores"

    # (1) 실으면 안 되는 것이 실리지 않았는가
    # ★★ R49 (2026-09-04) — 이 검사는 **뒤집혔다.** 종전에는 「periodic 원문 xml 이 0개
    #   이어야 한다」였다. 그 불변식이 곧 결함이었다 — `_grid_doc()`(grid_header_pin · R72)이
    #   periodic 원문을 질의 시점에 여는데 안 실어서, 배포본에서만 그 핀이 죽고 있었다.
    #   이제는 **있어야 한다**(docstring 참조). 「0이어야 한다」를 그대로 두면 앞으로
    #   누가 다시 뺀다.
    #   하한은 **실측 기준선의 약 90%** (2026-09-04 실측: periodic xml 1,466개).
    n_periodic = len(list((corpus_dir).rglob("periodic/**/*.xml")))
    check(n_periodic >= 1_300,
          f"periodic 원문 xml: {n_periodic:,}개 (>= 1,300 — grid_header_pin 이 연다)")
    for rel in NEVER_SHIP:
        p = stores_dir / rel
        check(not p.exists(), f"빌드 전용이 안 실렸다: {rel}")
    big = [f for f in deploy_dir.rglob("*") if f.is_file() and f.stat().st_size > 2_000 * MB]
    check(not big, f"2GB 넘는 파일: {len(big)}개")

    # (2) 실어야 하는 것이 다 실렸는가
    for rel in STORE_FILES:
        check((stores_dir / rel).exists(), f"스토어 실재: {rel}")
    for name, _k in CORPUS_FILES:
        check((corpus_dir / name).exists(), f"메타 실재: {name}")

    # ★★ R49 (2026-09-04) — **«실재»만 보면 «비어 있음»을 못 잡는다.**
    #   `stage()` 는 원문 그룹 디렉터리가 **없을 때만** 죽는다. 있는데 **비어 있으면**
    #   0파일을 실은 채 검증 전 항목이 초록이었다. 그러면 `exchange_body()`
    #   (함정 26 · 서술형 1,469건의 **유일한** 본문 경로)가 전량 빈손이 되는데 아무도 모른다.
    #   메타도 마찬가지였다 — manifest 4,204행·universe 70행이 한 줄도 검사되지 않았다.
    #   하한은 **실측 기준선(2026-09-04)의 약 90%**:
    #     exchange 1,539파일 18.3MB · periodic 1,545파일 5,058.2MB ·
    #     major 668파일 21.8MB · holding 1,153파일 209.4MB
    for _grp, _nfloor, _bfloor in (("exchange", 1_400, 16 * MB),
                                   ("periodic", 1_400, 4_500 * MB),
                                   ("major", 550, 18 * MB),
                                   ("holding", 1_000, 180 * MB)):
        _fs = [f for f in (corpus_dir / _grp).rglob("*") if f.is_file()]
        _by = sum(f.stat().st_size for f in _fs)
        check(len(_fs) >= _nfloor and _by >= _bfloor,
              f"원문 {_grp}: {len(_fs):,}파일 · {human(_by)} "
              f"(>= {_nfloor:,}파일 · {human(_bfloor)})")
    _mp = corpus_dir / "manifest.jsonl"
    _n_man = sum(1 for _l in open(_mp, encoding="utf-8") if _l.strip()) if _mp.exists() else 0
    check(_n_man >= 4_200, f"manifest 행 {_n_man:,} (>= 4,200)")
    _up = corpus_dir / "universe.csv"
    _n_uni = sum(1 for _l in open(_up, encoding="utf-8-sig") if _l.strip()) if _up.exists() else 0
    check(_n_uni >= 70, f"universe 행 {_n_uni:,} (>= 70 · 머리글 포함)")

    # (3) ★★ 정정 헤더 스토어가 «원문 읽기와 바이트 동일»한가 — 전수
    #     이게 어긋나면 컨텍스트가 바뀌고 **HCX 캐시가 갈린다** (M-49 기전).
    print("\n  ── 정정 헤더 전수 대조 (스토어 ↔ 원문 읽기)")
    import answerer
    man = [m for m in loader.load_corpus().manifest if m.get("is_correction")]
    store = answerer._corr_store()
    check(len(store) == len(man), f"스토어 항목 수 {len(store)} == 정정본 {len(man)}")
    #   ★ 2026-08-20 — **처음에 `_corr_fmt(..., 3000)` 끼리 비교했다가 «장님»이었다.**
    #     검출력 시험(스토어 한 건의 «꼬리» 5자를 일부러 훼손)에서 **0건**이 잡혔다 —
    #     그 블록이 22,950자라 3,000자로 자른 뒤에는 훼손 지점이 보이지 않는다.
    #     즉 「바이트 동일」이라는 주장이 실은 **「앞 3,000자만 동일」**이었다.
    #     → **자르기 «전» 전문(`seg_full`)을 비교한다.** 그게 진짜 불변식이다.
    #     (계기가 «무엇을 성공으로 셌는가»를 의심하라 — 함정 55.)
    diff, worst = [], 0
    for i, m in enumerate(man, 1):
        rc = m["rcept_no"]
        a = store.get(rc, "")
        b = answerer._corr_segment(m)
        worst = max(worst, len(b))
        if a != b:
            diff.append(rc)
        if i % max(1, len(man) // 10) == 0:
            print(f"    … {i}/{len(man)} ({i * 100 // len(man)}%)", flush=True)
    check(not diff, f"블록 «전문» 바이트 동일: {len(man)}건 중 다른 것 {len(diff)}건"
                    + (f" 예:{diff[:3]}" if diff else f" (최장 {worst:,}자)"))

    # (3-b) ★ 표지 스토어도 «원문 읽기와 같은가» — 표본 전수 대조
    #   심문 8차가 정정 헤더에만 이 검사가 있고 표지에는 없다고 지적했다
    #   (함정 23 — 한쪽에만 있는 처방). 같은 기전으로 컨텍스트가 갈린다.
    print("\n  ── 표지 필드 대조 (스토어 ↔ 원문 읽기 · 표본 60)")
    import run_build_cover
    per = [m for m in loader.load_corpus().manifest if m.get("doc_group") == "periodic"]
    cov = answerer._cover_store()
    check(len(cov) >= len(per) - 5,
          f"표지 스토어 {len(cov)}건 / 정기공시 {len(per)}건 (실패 5건 이하여야 한다)")
    _bad = [m["rcept_no"] for m in per[::max(1, len(per) // 60)]
            if cov.get(m["rcept_no"], {}) != run_build_cover.cover_fields(m["file_path"])]
    check(not _bad, f"표지 필드 일치: 표본 대조 불일치 {len(_bad)}건"
                    + (f" 예:{_bad[:3]}" if _bad else ""))

    # (3-c) 나머지 사전 추출 스토어의 «건수 하한» — 빌드가 조용히 빈손이 되는 것을 막는다.
    #   (실측 기준선: major_notes 559 · holding_tables 1,083 · corr_items 987)
    import json as _json
    for rel, floor, name in (("db/major_notes.jsonl", 500, "major 서술항목"),
                             ("db/holding_tables.jsonl", 1000, "대량보유 표"),
                             ("db/corr_items.jsonl", 900, "정정사항 표")):
        _p = stores_dir / rel
        _n = sum(1 for _l in open(_p, encoding="utf-8") if _l.strip()) if _p.exists() else 0
        check(_n >= floor, f"{name} 스토어 {_n}건 (>= {floor})")

    # ★ 2026-08-31 — 「행 수」가 아니라 «버킷별» 하한도 본다. 대량보유 스토어는 파일 행이
    #   1,083 이어도 **특정 버킷만 통째로 빈손**이 될 수 있다 (표 선택 정규식 하나가
    #   어긋나면 그렇게 된다). 실측 기준선: coll 711 · bytype 1,083 · **note 339**.
    _hp = stores_dir / "db/holding_tables.jsonl"
    if _hp.exists():
        # ★ 2026-08-31 (R41 배치3) — `note_rows`(표 밖 각주) 하한을 함께 본다.
        #   이 버킷은 정규식 하나에 걸려 있어 **조용히 빈손이 되기 가장 쉬운** 자리다.
        #   실측 기준선 **339건**.
        _b = {"coll_rows": 0, "bytype_rows": 0, "contract_rows": 0, "summary_rows": 0,
              "note_rows": 0}
        for _l in open(_hp, encoding="utf-8"):
            if not _l.strip():
                continue
            _d = _json.loads(_l)
            for _k in _b:
                if _d.get(_k):
                    _b[_k] += 1
        for _k, _floor in (("summary_rows", 1000), ("contract_rows", 650),
                           ("coll_rows", 650), ("bytype_rows", 1000),
                           ("note_rows", 300)):
            check(_b[_k] >= _floor, f"대량보유 {_k} {_b[_k]}건 (>= {_floor})")

    # (3-d) ★ 2026-09-01 — 버킷·스토어별 «글자 수» 하한.
    #   건수·파일 크기는 내용을 보증하지 않는다 (8/31 note_rows −22.4% 사고 —
    #   경위·기준선·검출력은 `char_floor_checks()` docstring 에 있다).
    print("\n  ── 글자 수 하한 (건수·크기가 못 보는 내용 손실)")
    for _ok, _msg in char_floor_checks(stores_dir):
        check(_ok, _msg)

    # (4) 크기
    total = report["corpus_bytes"] + report["stores_bytes"]
    print(f"\n  배송 트리 합계  {human(total)}"
          f"  (코퍼스 {human(report['corpus_bytes'])} + 스토어 {human(report['stores_bytes'])})")
    print(f"  원본 대비        {human(5_301 * MB + 2_108 * MB)} → {human(total)}"
          f"  ({(5_301 + 2_108) * MB / max(total, 1):.1f}배 감축)")
    return fail


def main() -> int:
    ap = argparse.ArgumentParser(description="배송 트리 분리기")
    ap.add_argument("--out", default=str(ROOT / "var" / "deploy"))
    ap.add_argument("--verify", action="store_true", help="짓고 나서 전수 검증")
    a = ap.parse_args()

    deploy_dir = Path(a.out)
    print(f"배송 트리 → {deploy_dir}")
    report = stage(deploy_dir)

    if not a.verify:
        print("\n  (--verify 를 붙이면 전수 검증까지 합니다)")
        return 0
    # ★ R50 — 목록이 둘로 갈라지는 것을 코드가 막는다(함정 23).
    if tuple(STORE_FILES) != tuple(loader.STORE_FILES):
        print("★★ STORE_FILES 가 loader.STORE_FILES 와 다릅니다 — 목록이 갈라졌습니다.\n"
              f"   stage_deploy: {STORE_FILES}\n   loader      : {list(loader.STORE_FILES)}")
        return 1
    fail = verify(deploy_dir, report)
    print("\n" + ("═" * 60))
    print(" 전부 통과" if fail == 0 else f" ★★ 실패 {fail}건 — 이 트리를 배포하지 마십시오")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
