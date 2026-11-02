"""events.jsonl → SQLite. 숫자·비교 질의를 SQL 한 방으로 답하기 위한 계층.

    py -3 scripts\\run_build_db.py          DB 생성
    py -3 scripts\\run_build_db.py --check  간단 질의로 검증
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "io"))
import loader  # noqa: E402

SCHEMA = """
DROP TABLE IF EXISTS events;
CREATE TABLE events (
    rcept_no      TEXT PRIMARY KEY,
    corp_code     TEXT,
    corp_name     TEXT,
    sector        TEXT,
    doc_group     TEXT,
    event_type    TEXT,
    report_nm     TEXT,
    rcept_dt      TEXT,          -- YYYYMMDD
    rcept_year    INTEGER,
    is_correction INTEGER,
    amount        INTEGER,
    amount_src    TEXT,
    amount_ratio  REAL,
    counterparty  TEXT,
    period_start  TEXT,
    period_end    TEXT,
    event_date    TEXT,
    reason        TEXT,
    계약명         TEXT,
    raw           TEXT,          -- 스키마 밖 질의 대비 원본 필드 전량
    chain_id      TEXT,          -- 정정 체인 (correction.py). NULL = 단독 문서
    chain_version INTEGER,
    is_latest     INTEGER DEFAULT 1
);
CREATE INDEX idx_corp   ON events(corp_name);
CREATE INDEX idx_type   ON events(event_type);
CREATE INDEX idx_year   ON events(rcept_year);
CREATE INDEX idx_sector ON events(sector);

DROP TABLE IF EXISTS related;
CREATE TABLE related (
    rcept_no      TEXT,
    related_dt    TEXT,
    related_nm    TEXT,
    related_rcept TEXT,
    chain_status  TEXT
);
CREATE INDEX idx_rel ON related(rcept_no);
CREATE INDEX idx_rel_target ON related(related_rcept);
"""

COLS = ["rcept_no", "corp_code", "corp_name", "sector", "doc_group", "event_type",
        "report_nm", "rcept_dt", "rcept_year", "is_correction", "amount", "amount_src",
        "amount_ratio", "counterparty", "period_start", "period_end", "event_date",
        "reason", "계약명", "raw", "chain_id", "chain_version", "is_latest"]


def db_path(create: bool = False) -> Path:
    path = Path(loader.CFG.get("event_db", str(Path(loader.DERIVED_DIR) / "db" / "event.sqlite")))
    # ★ 2026-09-04 (R53) — **읽기 경로가 폴더를 만들면 안 된다.**
    #   전에는 여기서 무조건 `mkdir` 했다. 그래서 `data_root` 가 틀린 셸에서
    #   **없는 폴더가 새로 파이고** 그 아래 빈 DB 가 생겼다(실물 확인: 0바이트
    #   `section.sqlite`, 테이블 0개). 폴더가 «생겨 버린» 뒤로는 존재 검사마저
    #   통과해 경고도 안 뜬다 — 이 저장소가 가장 두려워하는 「조용히 틀린다」다.
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    return path


def connect(create: bool = False) -> sqlite3.Connection:
    # ★ `sqlite3.connect` 는 **파일이 없으면 만든다.** 그대로 두면 경로가 틀렸을 때
    #   «빈 DB» 가 조용히 열리고, 모든 조회가 0건을 돌려주며 제품은 「없습니다」라고
    #   답한다. 데이터 부재와 경로 오류가 구별되지 않는다. 읽기는 **없으면 죽는다.**
    path = db_path(create=create)
    if not create and (not path.exists() or path.stat().st_size == 0):
        raise FileNotFoundError(
            f"[eventdb] 스토어가 없거나 비어 있다 → {path}\n"
            f"         data_root = {loader.CFG.get('data_root')}\n"
            f"         `GONGSI_DATA_ROOT` 를 걸었는지, `configs/paths.yaml` 의 "
            f"`data_root` 가 맞는지 확인하십시오. (빈 DB 로 «조용히» 답하지 않는다)")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    if not create:
        _assert_rows(conn, path)
    return conn


# 스토어별 «검사 통과» 기록 — 같은 파일을 요청마다 46번 열기 때문에 한 번만 본다.
_ROWS_OK: dict = {}


def _assert_rows(conn: sqlite3.Connection, path: Path) -> None:
    """**스키마만 있고 행이 0** 인 스토어를 거른다 (R52 심문 성립).

    0바이트 검사만으로는 부족하다 — 실측: `CREATE TABLE` 만 돈 40,960바이트 스토어가
    가드를 **통과**하고 「찾지 못했습니다」를 **HTTP 200** 으로 내보낸다. 그리고 그 답은
    `failed=False` 라 **응답 디스크 캐시에 영구 고착**된다(캐시 키에 스토어 지문이 없다).
    즉 스토어가 통째로 비어도 초록불이 유지되고, 재기동해도 같은 빈 답이 나간다.
    «데이터가 없다» 와 «스토어가 안 실렸다» 는 다른 사실이다 — 후자는 죽어야 한다.
    """
    key = str(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None
    if _ROWS_OK.get(key) == mtime:
        return
    try:
        row = conn.execute("SELECT 1 FROM events LIMIT 1").fetchone()
    except sqlite3.Error as exc:
        raise FileNotFoundError(
            f"[eventdb] 스토어에 `events` 테이블이 없다 → {path} ({exc})\n"
            f"         data_root = {loader.CFG.get('data_root')}\n"
            f"         빌드가 끝난 스토어인지 확인하십시오.") from exc
    if row is None:
        raise FileNotFoundError(
            f"[eventdb] 스토어의 `events` 이 **비어 있다**(행 0) → {path}\n"
            f"         data_root = {loader.CFG.get('data_root')}\n"
            f"         스키마만 있는 스토어로 «찾지 못했습니다» 를 내보내지 않는다.")
    _ROWS_OK[key] = mtime


def build() -> int:
    src = Path(loader.DERIVED_DIR) / "db" / "events.jsonl"
    if not src.exists():
        print("events.jsonl 이 없습니다. 먼저 run_parse_event.py 를 실행하세요.")
        return 1

    conn = connect(create=True)   # 빌드만 «만들 수» 있다 (읽기는 없으면 죽는다)
    conn.executescript(SCHEMA)

    # 정정 체인 (correction.py 출력). 없으면 전부 단독 문서로 취급.
    chains: dict[str, dict] = {}
    chain_src = Path(loader.DERIVED_DIR) / "db" / "chains.jsonl"
    if chain_src.exists():
        for line in open(chain_src, encoding="utf-8"):
            ch = json.loads(line)
            chains[ch["rcept_no"]] = ch

    rows = []
    for line in open(src, encoding="utf-8"):
        ev = json.loads(line)
        raw = {k: v for k, v in ev.items() if k in ("raw_te", "raw_tu")}
        extra = {k: v for k, v in ev.items() if k not in COLS and k not in ("raw_te", "raw_tu")}
        ch = chains.get(ev.get("rcept_no"), {})
        rows.append([
            ev.get("rcept_no"), ev.get("corp_code"), ev.get("corp_name"), ev.get("sector"),
            ev.get("doc_group"), ev.get("event_type"), ev.get("report_nm"), ev.get("rcept_dt"),
            int(str(ev.get("rcept_dt", "0"))[:4] or 0), 1 if ev.get("is_correction") else 0,
            ev.get("amount"), ev.get("amount_src"), ev.get("amount_ratio"),
            ev.get("counterparty"), ev.get("period_start"), ev.get("period_end"),
            ev.get("event_date"), ev.get("reason"), ev.get("계약명"),
            json.dumps({**extra, **raw}, ensure_ascii=False),
            ch.get("chain_id"), ch.get("version"), ch.get("is_latest", 1),
        ])
    conn.executemany(f"INSERT OR REPLACE INTO events VALUES ({','.join('?' * len(COLS))})", rows)

    rel_src = Path(loader.DERIVED_DIR) / "db" / "related.jsonl"
    rels = []
    if rel_src.exists():
        for line in open(rel_src, encoding="utf-8"):
            r = json.loads(line)
            rels.append([r.get("rcept_no"), r.get("related_dt"), r.get("related_nm"),
                         r.get("related_rcept"), r.get("chain_status")])
        conn.executemany("INSERT INTO related VALUES (?,?,?,?,?)", rels)

    conn.commit()
    print(f"DB 생성 완료: {db_path()}")
    print(f"  events {len(rows):,}행 · related {len(rels):,}행")
    return 0


def check() -> None:
    conn = connect()
    q = lambda sql: [dict(r) for r in conn.execute(sql).fetchall()]  # noqa: E731

    print("\n[유형별 건수]")
    for r in q("SELECT event_type, COUNT(*) n FROM events GROUP BY 1 ORDER BY n DESC LIMIT 6"):
        print(f"   {r['event_type']:<24}{r['n']:>6,}")

    # ★ 2026-08-15: 기준을 «정정 제외»에서 «발생(체결)일»로 바꿨다.
    #   전에는 `rcept_year=2025 AND is_correction=0` 이었다. 그 조건이 내는 값(현대건설
    #   8건 13.19조)은 **우연히 맞았다** — 과거 계약의 2025년 정정본을 정정 필터가
    #   덤으로 걸러 줬을 뿐이다. 그래서 에이전트(정정본 포함, 접수일 기준 21건 29.38조)와
    #   답이 갈렸다. 이제 둘 다 `answerer.OCCUR_YEAR` 과 같은 식을 쓴다.
    #   상세: docs/작업일지.md [M-3]
    occur = "substr(COALESCE(NULLIF(event_date,''), NULLIF(period_start,''), rcept_dt),1,4)"
    print("\n[2025년 공급계약체결 상위 5개사 ― 발생(체결)일 기준 · 최신 유효본]")
    for r in q(f"""SELECT corp_name, COUNT(*) n, SUM(amount) total FROM events
                  WHERE event_type='공급계약체결' AND {occur}='2025' AND is_latest=1
                  GROUP BY 1 ORDER BY total DESC LIMIT 5"""):
        print(f"   {r['corp_name']:<16}{r['n']:>3}건  {(r['total'] or 0)/1e12:>8.2f}조원")

    print("\n[연도 기준 차이 ― 접수일로 세면 얼마나 늘어나는가]")
    for r in q(f"""SELECT COUNT(*) n, SUM({occur}<>substr(rcept_dt,1,4)) moved
                  FROM events WHERE is_latest=1"""):
        print(f"   최신 유효본 {r['n']:,}건 중 발생연도≠접수연도 {r['moved']:,}건 "
              f"({r['moved']/max(r['n'],1):.1%})")
    for r in q(f"""SELECT event_type, COUNT(*) n, SUM({occur}<>substr(rcept_dt,1,4)) moved
                  FROM events WHERE is_latest=1 GROUP BY 1 HAVING moved>0
                  ORDER BY moved DESC LIMIT 3"""):
        print(f"     {r['event_type']:<20}{r['moved']:>4}/{r['n']:<5}")

    print("\n[체결→해지 체인 (코퍼스 내 원계약이 있는 것)]")
    for r in q("""SELECT e.corp_name, e.rcept_no 해지, r.related_rcept 원계약, e.amount
                  FROM events e JOIN related r ON e.rcept_no = r.rcept_no
                  WHERE e.event_type='공급계약해지' AND r.chain_status='linked' LIMIT 5"""):
        print(f"   {r['corp_name']:<14}{r['해지']} → {r['원계약']}  {(r['amount'] or 0)/1e8:,.0f}억")


def main() -> int:
    rc = build()
    if rc == 0 and "--check" in sys.argv:
        check()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
