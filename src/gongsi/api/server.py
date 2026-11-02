"""평가용 API 서버.

주최측 규격 (디스코드 공지 2026-08-12):
    GET {endpoint}/answer?question_id={id}&question={질의}
    → application/json, 필드 5개, **전부 string 타입**

여기서 반드시 지키는 것:
  - 경로는 /answer 고정, 인증 헤더 없음
  - 5개 필드 전부 문자열. 리스트·객체를 넣으면 규격 위반
  - **어떤 예외가 나도 200 + JSON 을 돌려준다.** 5xx 를 내면 주최측 재시도를 소모한다
  - 내부 타임아웃 280초 ― 주최측 타임아웃 300초 안에 부분 답변이라도 반환
  - question_id 응답 캐시 ― 재시도(최대 2회) 때 즉시 응답

★ 2026-08-20 (D-2) 보강 세 가지. 전부 «폐기한 프로젝트 B 가 실물에서 데어 본 것»이다.

  ㉠ 응답 캐시를 **디스크에 남긴다** (`ANSWER_STATE_DIR`).
     주최측 규격상 **장애로 인한 서버 재기동은 실격이 아니다.** 그런데 인메모리 캐시는
     재기동과 함께 사라지고, 그러면 같은 `question_id` 에 **다른 답**이 나갈 수 있다
     (LLM 은 temperature 를 낮춰도 완전 결정적이지 않다). 규격이 요구하는 것은
     「안 죽는다」가 아니라 **「죽어도 같은 주소에서 같은 답이 나온다」**이다.

  ㉡ **`/selfcheck`** — 캐시를 우회해 조회 파이프라인을 실제로 돌린다.
     워치독이 `/answer` 카나리아만 보면 **두 번째부터 캐시 적중**이라, 스토어 볼륨이
     통째로 빠져도 초록이 유지된다. 우회로가 따로 있어야 그걸 잡는다.
     ★ HCX 는 부르지 않는다 — 감시가 크레딧을 태우면 안 되고, 검사하려는 것은
       «우리 스토어가 살아 있는가»이지 «HCX 가 살아 있는가»가 아니다.

  ㉢ `/health` 가 **스토어 실재**까지 본다. 상수 200 은 감시가 아니다.
     (SIGSTOP 걸린 서버는 TCP connect 가 성립하고 포트도 열려 있다 — B 라운드 14 실측.)
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import sqlite3
import sys
import threading
import time
import traceback
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

_SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SRC / "agent"))
sys.path.insert(0, str(_SRC / "io"))
import answerer  # noqa: E402
import loader    # noqa: E402

INTERNAL_TIMEOUT = 280          # 주최측 300초보다 짧게
app = FastAPI(title="공시 Agent")
_pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
_mem: dict[str, dict] = {}      # question_id → 응답 (디스크 앞단의 메모리 캐시)
_lock = threading.Lock()
_BOOT = time.time()

# ── 디스크 영속 캐시 ────────────────────────────────────────────────────
#   컨테이너에서는 **반드시 영속 볼륨**으로 마운트한다. 컨테이너와 함께 사라지면
#   ㉠ 의 목적이 통째로 없어진다.
STATE_DIR = Path(os.environ.get("ANSWER_STATE_DIR")
                 or (Path(loader.DERIVED_DIR) / "answer_state"))


def _state_path(question_id: str) -> Path:
    """question_id → 파일. 파일명에 못 쓰는 문자가 와도 안전하도록 해시로 짓는다."""
    h = hashlib.sha256(question_id.encode("utf-8")).hexdigest()[:32]
    return STATE_DIR / f"{h}.json"


def _cache_get(question_id: str) -> dict | None:
    if not question_id:
        return None
    with _lock:
        hit = _mem.get(question_id)
    if hit is not None:
        return hit
    path = _state_path(question_id)
    try:
        if path.exists():
            hit = json.loads(path.read_text(encoding="utf-8"))
            with _lock:
                _mem[question_id] = hit
            return hit
    except Exception:                                          # noqa: BLE001
        # 캐시가 깨졌으면 «없는 것»으로 친다. 여기서 죽으면 답 자체를 못 낸다.
        pass
    return None


def _cache_put(question_id: str, payload: dict) -> None:
    if not question_id:
        return
    with _lock:
        _mem[question_id] = payload
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        path = _state_path(question_id)
        # ★ 원자적 쓰기 — 쓰는 도중에 죽어도 «반쪽짜리 JSON» 이 남지 않는다.
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:                                          # noqa: BLE001
        # 디스크에 못 써도 **답은 나가야 한다.** 메모리 캐시는 이미 채웠다.
        print(f"[server] ★ 경고: 응답 캐시를 디스크에 못 썼습니다 → {STATE_DIR}",
              file=sys.stderr)


def _as_strings(payload: dict) -> dict:
    return {k: ("" if v is None else str(v)) for k, v in payload.items()}


# ══════════════════════════════════════════════════════════════════════
#  /answer  — 규격 경로
# ══════════════════════════════════════════════════════════════════════
@app.get("/answer")
def get_answer(question_id: str = Query(""), question: str = Query("")):
    cached = _cache_get(question_id)
    if cached is not None:
        return JSONResponse(cached)                            # 재시도 → 즉시 «같은» 응답

    base = {"question_id": question_id, "question": question,
            "retrieved_context": "", "think_trace": "", "answer": ""}

    if not question.strip():
        base["answer"] = "질의가 비어 있습니다."
        return JSONResponse(_as_strings(base))

    failed = False
    try:
        future = _pool.submit(answerer.answer, question, question_id)
        result = future.result(timeout=INTERNAL_TIMEOUT)
        payload = _as_strings({**base, **result})
    except concurrent.futures.TimeoutError:
        failed = True
        base["think_trace"] = f"내부 타임아웃 {INTERNAL_TIMEOUT}초 초과"
        base["answer"] = "처리 시간이 초과되어 답변을 생성하지 못했습니다."
        payload = _as_strings(base)
    except Exception:                                          # noqa: BLE001
        failed = True
        base["think_trace"] = traceback.format_exc()[-1500:]
        base["answer"] = "내부 오류로 답변을 생성하지 못했습니다."
        payload = _as_strings(base)

    # ★ **실패 응답은 캐시하지 않는다.**
    #   주최측은 타임아웃·5xx 시 최대 2회 재시도한다. 그런데 오류를 캐시해 두면
    #   재시도가 **같은 오류를 즉시** 받아 간다 — 재시도 기회 2회가 통째로 소모된다.
    #   2026-08-15 실측: HCX 429 소진으로 한 문항이 「내부 오류」를 냈고, 같은
    #   question_id 로 다시 부르니 0.00초에 같은 오류가 돌아왔다.
    #   캐시의 목적은 «성공한 답을 재현»하는 것이지 «실패를 고정»하는 것이 아니다.
    #   ★ D-2 에서 디스크로 옮기면서도 이 판단은 그대로 지킨다 — 실패가 디스크에
    #     남으면 재기동해도 그 문항은 영영 오류를 돌려준다.
    if question_id and not failed:
        _cache_put(question_id, payload)
    return JSONResponse(payload)


# ══════════════════════════════════════════════════════════════════════
#  /health  — 「떠 있는가」가 아니라 「스토어가 살아 있는가」
# ══════════════════════════════════════════════════════════════════════
def _store_probe() -> tuple[bool, dict]:
    """런타임이 여는 스토어를 실제로 열어 본다. (ok, 상세)"""
    detail: dict[str, str] = {}
    ok = True
    for name, rel, table in (("event", "db/event.sqlite", "events"),
                             ("section", "db/section.sqlite", "sections")):
        path = Path(loader.DERIVED_DIR) / rel
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
            n = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            conn.close()
            detail[name] = str(n)
            ok = ok and n > 0
        except Exception as e:                                 # noqa: BLE001
            detail[name] = f"ERR:{type(e).__name__}"
            ok = False
    try:
        n_man = len(answerer.corpus().manifest)
        detail["manifest"] = str(n_man)
        ok = ok and n_man > 0
    except Exception as e:                                     # noqa: BLE001
        detail["manifest"] = f"ERR:{type(e).__name__}"
        ok = False
    # ★ 2026-09-04 (R50) — 나머지 스토어도 «실재 + 비어 있지 않음»을 본다.
    #   종전에는 10벌 중 4종만 봤다. 나머지 6벌이 빠져도 초록이 유지됐다.
    #   ★★ **파일 크기로는 부족하다** — `section.sqlite` 는 47,853행본과 47,163행본이
    #     똑같이 1,240,666,112바이트다(실측). jsonl 은 **줄 수**를 센다.
    for rel in loader.STORE_FILES:
        name = Path(rel).stem
        if name in detail:                       # 위에서 이미 본 것(event·section)
            continue
        path = Path(loader.DERIVED_DIR) / rel
        try:
            with open(path, encoding="utf-8") as stream:
                n = sum(1 for line in stream if line.strip())
            detail[name] = str(n)
            ok = ok and n > 0
        except Exception as e:                                 # noqa: BLE001
            detail[name] = f"ERR:{type(e).__name__}"
            ok = False
    return ok, detail


@app.get("/health")
def health():
    ok, detail = _store_probe()
    return JSONResponse({
        "status": "ok" if ok else "degraded",
        "stores_ok": str(ok).lower(),
        "detail": detail,
        "cached": str(len(_mem)),
        "uptime_sec": f"{time.time() - _BOOT:.1f}",
    }, status_code=200)          # ★ 200 을 유지한다 — 5xx 는 주최측 재시도를 소모한다


# ══════════════════════════════════════════════════════════════════════
#  /selfcheck  — 캐시를 «우회»해 조회 파이프라인을 실제로 돌린다
# ══════════════════════════════════════════════════════════════════════
#   ★ 워치독이 이걸 주기적으로 친다. `/answer` 카나리아만 보면 두 번째부터
#     캐시 적중이라, 스토어 볼륨이 빠져도 초록이 유지된다 (B 라운드 실측).
#   ★ HCX 는 부르지 않는다. 검사 대상은 «우리 스토어»이지 «HCX» 가 아니다.
_SELFCHECK_CORP = "삼성전자"
_SELFCHECK_Q = "2025년 연결기준 매출액은 얼마인가?"


@app.get("/selfcheck")
def selfcheck():
    t0 = time.time()
    out: dict[str, str] = {}
    ok = True
    try:
        rows = answerer.keyword_rows([_SELFCHECK_CORP], _SELFCHECK_Q, limit=3)
        out["event_rows"] = str(len(rows))
        ok = ok and len(rows) > 0
    except Exception as e:                                     # noqa: BLE001
        out["event_rows"] = f"ERR:{type(e).__name__}"
        ok = False
    try:
        sec, how = answerer.search_sections(
            [_SELFCHECK_CORP], [2025], ("%요약재무%",), _SELFCHECK_Q, None)
        out["section_rows"] = str(len(sec))
        out["section_how"] = how[:60]
        ok = ok and len(sec) > 0
    except Exception as e:                                     # noqa: BLE001
        out["section_rows"] = f"ERR:{type(e).__name__}"
        ok = False
    try:
        # 정정 헤더 스토어(배송 트리에서 원문을 대신하는 것)가 살아 있는가
        out["corr_store"] = str(len(answerer._corr_store()))
        ok = ok and len(answerer._corr_store()) > 0
    except Exception as e:                                     # noqa: BLE001
        out["corr_store"] = f"ERR:{type(e).__name__}"
        ok = False
    store_ok, detail = _store_probe()
    ok = ok and store_ok
    return JSONResponse({
        "status": "ok" if ok else "degraded",
        "elapsed_sec": f"{time.time() - t0:.3f}",
        "pipeline": out,
        "stores": detail,
        "note": "캐시 우회 · HCX 미호출",
    }, status_code=200)
