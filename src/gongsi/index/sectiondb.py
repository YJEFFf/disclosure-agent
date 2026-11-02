r"""sections.jsonl → SQLite (+FTS5 전문검색). 정기공시 섹션 검색 계층 (C+ 단계).

    py -3 scripts\run_build_sectiondb.py          DB 생성
    py -3 scripts\run_build_sectiondb.py --check  검증 질의

is_latest: 정기공시 정정은 manifest 만으로 매칭된다 (CLAUDE.md 함정 19).
  (corp_code, doc_subtype, base_year, base_month) 그룹에서 rcept_no 최댓값 = 최신.
  검색 기본 대상은 is_latest=1. "정정 전 값" 질의를 위해 이전 버전도 보존한다.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "io"))
import loader  # noqa: E402

SCHEMA = """
DROP TABLE IF EXISTS sections;
CREATE TABLE sections (
    id            INTEGER PRIMARY KEY,
    rcept_no      TEXT,
    corp_code     TEXT,
    corp_name     TEXT,
    doc_subtype   TEXT,          -- annual | half | quarter
    report_nm     TEXT,
    rcept_dt      TEXT,
    rcept_year    INTEGER,
    base_year     INTEGER,
    base_month    INTEGER,
    is_correction INTEGER,
    is_latest     INTEGER DEFAULT 1,
    file          TEXT,
    sec_path      TEXT,
    sec_title     TEXT,
    level         INTEGER,
    n_chars       INTEGER,
    text          TEXT
);
CREATE INDEX idx_sec_corp ON sections(corp_name, base_year);
CREATE INDEX idx_sec_doc  ON sections(rcept_no);
CREATE INDEX idx_sec_title ON sections(sec_title);
"""

FTS = """
DROP TABLE IF EXISTS sections_fts;
CREATE VIRTUAL TABLE sections_fts USING fts5(
    sec_path, text,
    content='sections', content_rowid='id',
    tokenize='unicode61'
);
INSERT INTO sections_fts(rowid, sec_path, text)
    SELECT id, sec_path, text FROM sections;
"""


def db_path(create: bool = False) -> Path:
    path = Path(loader.CFG.get("section_db", str(Path(loader.DERIVED_DIR) / "db" / "section.sqlite")))
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
            f"[sectiondb] 스토어가 없거나 비어 있다 → {path}\n"
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
        row = conn.execute("SELECT 1 FROM sections LIMIT 1").fetchone()
    except sqlite3.Error as exc:
        raise FileNotFoundError(
            f"[sectiondb] 스토어에 `sections` 테이블이 없다 → {path} ({exc})\n"
            f"         data_root = {loader.CFG.get('data_root')}\n"
            f"         빌드가 끝난 스토어인지 확인하십시오.") from exc
    if row is None:
        raise FileNotFoundError(
            f"[sectiondb] 스토어의 `sections` 이 **비어 있다**(행 0) → {path}\n"
            f"         data_root = {loader.CFG.get('data_root')}\n"
            f"         스키마만 있는 스토어로 «찾지 못했습니다» 를 내보내지 않는다.")
    _ROWS_OK[key] = mtime


def has_fts5(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("CREATE VIRTUAL TABLE _fts_probe USING fts5(x)")
        conn.execute("DROP TABLE _fts_probe")
        return True
    except sqlite3.OperationalError:
        return False


def build() -> int:
    src = Path(loader.DERIVED_DIR) / "db" / "sections.jsonl"
    if not src.exists():
        print("sections.jsonl 이 없습니다. 먼저 run_parse_periodic.py 를 실행하세요.")
        return 1

    conn = connect(create=True)   # 빌드만 «만들 수» 있다 (읽기는 없으면 죽는다)
    conn.executescript(SCHEMA)

    cols = ["rcept_no", "corp_code", "corp_name", "doc_subtype", "report_nm", "rcept_dt",
            "rcept_year", "base_year", "base_month", "is_correction", "file",
            "sec_path", "sec_title", "level", "n_chars", "text"]
    rows = []
    for line in open(src, encoding="utf-8"):
        s = json.loads(line)
        rows.append([
            s.get("rcept_no"), s.get("corp_code"), s.get("corp_name"), s.get("doc_subtype"),
            s.get("report_nm"), s.get("rcept_dt"), int(str(s.get("rcept_dt", "0"))[:4] or 0),
            s.get("base_year"), s.get("base_month"), 1 if s.get("is_correction") else 0,
            s.get("file"), s.get("sec_path"), s.get("sec_title"), s.get("level"),
            s.get("n_chars"), s.get("text"),
        ])
    conn.executemany(
        f"INSERT INTO sections ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", rows)

    # is_latest: 그룹 내 rcept_no 최댓값 문서만 1 (함정 19 ― manifest 매칭 98.5%)
    conn.execute("""
        UPDATE sections SET is_latest = 0
        WHERE rcept_no NOT IN (
            SELECT MAX(rcept_no) FROM sections
            GROUP BY corp_code, doc_subtype, base_year, base_month
        )""")

    fts_ok = has_fts5(conn)
    if fts_ok:
        conn.executescript(FTS)
    conn.commit()

    n_docs = conn.execute("SELECT COUNT(DISTINCT rcept_no) FROM sections").fetchone()[0]
    n_latest = conn.execute(
        "SELECT COUNT(DISTINCT rcept_no) FROM sections WHERE is_latest=1").fetchone()[0]
    print(f"DB 생성 완료: {db_path()}")
    print(f"  섹션 {len(rows):,}행 · 문서 {n_docs:,}건 (최신본 {n_latest:,}건)")
    print(f"  FTS5: {'생성됨' if fts_ok else '미지원 → LIKE 검색으로 폴백해야 함'}")
    return 0


def search(query: str, corp: str | None = None, base_year: int | None = None,
           subtype: str | None = None, latest_only: bool = True, limit: int = 8) -> list[dict]:
    """FTS 검색. answerer 가 부르는 진입점."""
    conn = connect()
    where, params = ["1=1"], []
    if corp:
        where.append("s.corp_name = ?"); params.append(corp)
    if base_year:
        where.append("s.base_year = ?"); params.append(base_year)
    if subtype:
        where.append("s.doc_subtype = ?"); params.append(subtype)
    if latest_only:
        where.append("s.is_latest = 1")
    sql = f"""
        SELECT s.*, bm25(sections_fts) AS score
        FROM sections_fts f JOIN sections s ON s.id = f.rowid
        WHERE sections_fts MATCH ? AND {' AND '.join(where)}
        ORDER BY score LIMIT {int(limit)}"""
    return [dict(r) for r in conn.execute(sql, [query] + params).fetchall()]


# ---------------------------------------------------------------- 임베딩 검색 (F단계)

_EMB = None          # (ids: list[int], mat: np.ndarray[n,1024] 정규화, meta: dict[id] → row)


def _load_emb():
    """sections_emb.jsonl 을 메모리에 1회 적재. 빌드가 부분 진행 중이어도 있는 만큼 동작."""
    global _EMB
    if _EMB is not None:
        return _EMB
    import numpy as np
    path = Path(loader.DERIVED_DIR) / "index" / "sections_emb.jsonl"
    # ★ 2026-08-18 심문 반대 2 — 빈 결과를 `_EMB` 에 **굳히면 안 된다.** 굳히면
    #   「스토어를 나중에 만들면 자동으로 다시 켜진다」가 거짓이 된다 (프로세스 재기동 전까지
    #   절대 안 읽는다). 9/7~9/20 서버 운영 중 임베딩을 빌드하는 경우가 정확히 그것이다.
    #   파일 존재 확인은 싸다 — 없을 때는 캐시하지 않고 매번 확인한다.
    if not path.exists():
        return ([], None, {})
    ids, vecs = [], []
    for line in open(path, encoding="utf-8"):
        try:
            r = json.loads(line)
            ids.append(r["id"])
            vecs.append(r["vec"])
        except (json.JSONDecodeError, KeyError):
            continue
    # ★ 심문 반대 1 — **파일은 있는데 유효 레코드가 0** 인 상태가 실제로 만들어진다:
    #   `run_build_embed.py` 가 첫 임베딩이 성공하기 전에 `open("a")` 로 빈 파일을 만든다.
    #   그때 `np.linalg.norm(mat, axis=1)` 이 AxisError 를 던졌고, `has_embed()` 를
    #   try 밖에서 부르게 되면서 그 예외가 `answer()` 밖으로 나가 **전 문항 500** 이 됐다.
    #   (오늘 ⑤로 세운 「HCX 가 죽어도 답은 나간다」를 ①이 깬 셈이다 — 심문관 지적이 옳다.)
    if not vecs:
        return ([], None, {})
    mat = np.asarray(vecs, dtype=np.float32)
    mat /= (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
    conn = connect()
    meta = {r["id"]: dict(r) for r in conn.execute(
        "SELECT id, corp_name, base_year, doc_subtype FROM sections WHERE is_latest=1")}
    _EMB = (ids, mat, meta)
    return _EMB


def search_embed(query_vec: list[float], corp: str | None = None,
                 base_year: int | None = None, subtype: str | None = None,
                 limit: int = 6) -> list[dict]:
    """코사인 top-k. corp/base_year/subtype 로 사전 필터."""
    import numpy as np
    ids, mat, meta = _load_emb()
    if mat is None or not len(ids):
        return []
    mask = []
    for sid in ids:
        m = meta.get(sid)
        ok = m is not None
        if ok and corp:
            ok = m["corp_name"] == corp
        if ok and base_year:
            ok = m["base_year"] == base_year
        if ok and subtype:
            ok = m["doc_subtype"] == subtype
        mask.append(ok)
    idx = np.where(np.asarray(mask))[0]
    if idx.size == 0:
        return []
    qv = np.asarray(query_vec, dtype=np.float32)
    qv /= (np.linalg.norm(qv) + 1e-9)
    sims = mat[idx] @ qv
    order = np.argsort(-sims)[:limit]
    conn = connect()
    out = []
    for j in order:
        row = conn.execute("SELECT * FROM sections WHERE id=?", (ids[int(idx[j])],)).fetchone()
        if row:
            d = dict(row)
            d["score"] = float(sims[j])
            out.append(d)
    return out


def check() -> None:
    conn = connect()
    q = lambda sql, *p: [dict(r) for r in conn.execute(sql, p).fetchall()]  # noqa: E731

    print("\n[doc_subtype 별 섹션 수]")
    for r in q("SELECT doc_subtype, COUNT(*) n, COUNT(DISTINCT rcept_no) docs FROM sections GROUP BY 1"):
        print(f"   {r['doc_subtype']:<8} {r['n']:>8,}행 · {r['docs']:>4}문서")

    print("\n[is_latest 검증]")
    for r in q("""SELECT is_latest, COUNT(DISTINCT rcept_no) docs FROM sections GROUP BY 1"""):
        print(f"   is_latest={r['is_latest']}  {r['docs']:>4}문서")

    print("\n[FTS: 삼성전자 2025 사업보고서에서 '자금조달' 검색]")
    for r in search("자금조달", corp="삼성전자", base_year=2025, subtype="annual", limit=3):
        print(f"   [{r['level']}] {r['sec_path'][:60]}  ({r['n_chars']:,}자, score {r['score']:.1f})")

    print("\n[FTS: '수주' 상위 3건 (전사)]")
    for r in search("수주", limit=3):
        print(f"   {r['corp_name']:<12} {r['sec_path'][:50]}  ({r['n_chars']:,}자)")


def main() -> int:
    rc = build()
    if rc == 0 and "--check" in sys.argv:
        check()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())


def has_embed() -> bool:
    """임베딩 스토어가 실제로 쓸 수 있는 상태인가.

    ★ 2026-08-18 (M-49). `_embed_hits()` 가 `clovax.embed(question)` 을 **먼저** 부르고
      그 결과로 `search_embed` 를 불렀는데, 이 작업본에는 `index/sections_emb.jsonl` 이
      없어 **항상 빈 리스트**가 돌아온다. 즉 «질의마다 임베딩 API 를 한 번 때리고
      결과를 버리는» 상태였다 (캐시에 emb_*.json 1,358개 = 전부 버려진 호출).
      평가 서버에서도 문항마다 발생하므로 크레딧·지연·429 노출이 그만큼 늘어난다.
      **스토어를 나중에 만들면 자동으로 다시 켜져야 하므로 상수가 아니라 실측으로 판정한다.**
    """
    ids, mat, _ = _load_emb()
    return mat is not None and bool(len(ids))
