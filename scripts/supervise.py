#!/usr/bin/env python3
"""워치독 — **「떠 있음」이 아니라 「규격대로 답함」을 본다.**

    python3 scripts/supervise.py                 # 서버를 띄우고 감시한다 (PID 1 로 쓴다)
    python3 scripts/supervise.py --once          # 한 바퀴만 돌고 판정만 찍는다 (진단용)

왜 한 겹이 더 필요한가 — 세 겹으로 막는다.

    docker --restart=always     호스트 재부팅 · 컨테이너 자체가 죽었을 때
      └─ supervise.py           서버가 죽거나 **살아 있는데 답을 못 할** 때
           └─ run_server.py

가운데 층이 없으면 「살아 있지만 답을 못 하는」 상태를 **아무도 못 잡는다.**
`SIGSTOP` 걸린 서버는 **TCP connect 가 성립하고 포트도 열려 있으며**,
`--restart=always` 도 systemd `Restart=always` 도 아무 일을 하지 않는다.

두 겹으로 본다.

  얕은 검사 (매 주기)   `/answer` 를 실제로 쳐서 **5필드가 전부 str·비어 있지 않고
                        question_id 가 반향되고 answer 가 20자 이상인가**
  깊은 검사 (N주기마다) `/selfcheck` — **캐시를 우회**해 조회 파이프라인을 돌린다

★ 깊은 검사가 없으면 안 되는 이유 (2026-08-20 우리 코드로 실측):
  `section.sqlite` 를 뺀 트리에서 `/answer` 는 **200 에 5필드가 전부 채워진 채** 나온다.
  카나리아 질의가 캐시에 있기 때문이다. 얕은 검사만 보면 **스토어가 반쯤 죽어도 초록이다.**

★ 워밍업 유예(`WARMUP_GRACE`)와 컨테이너 `HEALTHCHECK --start-period` 는 **한 묶음**이다.
  하나만 건드리지 마라. B 는 이 조합에서 세 번 데었다(라운드 15·20·27) —
  유예가 그 기계의 워밍업보다 짧으면 **감시자가 «정상적으로 워밍업 중인 서버»를 죽이고**
  컨테이너가 영구 재시작 루프에 빠진다.
  ※ 우리 워밍업은 호스트 실측 **0.06초**다(지연 적재 설계라 기동에 아무것도 안 짓는다).
    그래도 유예를 넉넉히 둔다 — 컨테이너·네트워크 볼륨에서는 «미측정»이고,
    「배포에서는 빠를 것」은 예상이지 측정이 아니다.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PORT = int(os.environ.get("PORT", "8000"))
BASE = f"http://127.0.0.1:{PORT}"

# ── 손잡이 (전부 환경변수로 조절 가능) ───────────────────────────────────
INTERVAL = float(os.environ.get("WATCHDOG_INTERVAL_SEC", "30"))
RETRY_INTERVAL = float(os.environ.get("WATCHDOG_RETRY_INTERVAL_SEC", "5"))
PROBE_TIMEOUT = float(os.environ.get("WATCHDOG_PROBE_TIMEOUT_SEC", "20"))
WARMUP_PROBE_TIMEOUT = float(os.environ.get("WATCHDOG_WARMUP_PROBE_TIMEOUT_SEC", "60"))
WARMUP_GRACE = float(os.environ.get("WATCHDOG_WARMUP_GRACE_SEC", "240"))
FAIL_LIMIT = int(os.environ.get("WATCHDOG_FAIL_LIMIT", "3"))
DEEP_EVERY = int(os.environ.get("WATCHDOG_DEEP_EVERY", "20"))
TERM_WAIT = float(os.environ.get("WATCHDOG_TERM_WAIT_SEC", "15"))

# ★★ 2026-08-20 — **검출 지연은 «주기»가 아니라 «탐침 타임아웃 × 실패한도»가 정한다.**
#   첫 판에서 `PROBE_TIMEOUT=60` 이었더니 SIGSTOP 을 **25초 안에 못 잡았다** —
#   멈춘 서버에 건 요청이 60초를 통째로 매달려 주기(3초)가 아무 의미가 없었다.
#
#   짧게 줄여도 되는 근거 (실측 2026-08-20):
#     · 카나리아는 «캐시된» 질의라 정상 서버는 **0.001~0.005초**에 답한다
#     · 11.38초짜리 «비캐시» 질의를 처리하는 «동안»에도 카나리아는 **최대 0.005초**였다
#       (답변기가 스레드풀에 있어 이벤트 루프가 안 막힌다)
#   → 20초는 정상 지연의 **4,000배** 여유다. 바쁜 서버를 죽일 위험이 없다.
#
#   ★ 워밍업 «중»의 첫 카나리아는 캐시가 없어 HCX 왕복(~11초)이 든다.
#     그래서 그때만 `WARMUP_PROBE_TIMEOUT`(60초)을 쓴다.
#
#   최악 검출 지연 = FAIL_LIMIT × (RETRY_INTERVAL + PROBE_TIMEOUT) + INTERVAL
#                  = 3 × (5 + 20) + 30 = 105초  (첫 실패 뒤에는 주기를 «짧게» 쓴다)

# 카나리아 — 답이 «있는» 질의여야 한다. 없는 것을 물으면 거절이 정답이라
# answer 길이 검사가 의미를 잃는다.
CANARY_Q = os.environ.get("WATCHDOG_CANARY_Q", "삼성전자의 2025년 연결기준 매출액은 얼마인가?")
CANARY_ID = "WATCHDOG-CANARY"

FIELDS = ("question_id", "question", "retrieved_context", "think_trace", "answer")


def log(msg: str) -> None:
    print(f"[supervise {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _get(path: str, timeout: float, **params) -> tuple[int, bytes]:
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, r.read()


# ══════════════════════════════════════════════════════════════════════
#  검사 두 겹
# ══════════════════════════════════════════════════════════════════════
def shallow_probe(timeout: float = PROBE_TIMEOUT) -> tuple[bool, str]:
    """`/answer` 를 실제로 쳐서 **규격대로 답하는지** 본다."""
    try:
        status, body = _get("/answer", timeout,
                            question_id=CANARY_ID, question=CANARY_Q)
    except urllib.error.URLError as e:
        return False, f"연결 실패 {type(e).__name__}: {e}"
    except Exception as e:                                     # noqa: BLE001
        return False, f"요청 실패 {type(e).__name__}: {e}"
    if status != 200:
        return False, f"HTTP {status}"
    try:
        o = json.loads(body)
    except Exception:                                          # noqa: BLE001
        return False, "JSON 이 아니다"
    missing = [k for k in FIELDS if k not in o]
    if missing:
        return False, f"필드 누락 {missing}"
    if len(o) != len(FIELDS):
        return False, f"필드 수 {len(o)} (5여야 한다)"
    bad = [k for k in FIELDS if not isinstance(o[k], str)]
    if bad:
        return False, f"string 이 아닌 필드 {bad}"
    empty = [k for k in FIELDS if not o[k].strip()]
    if empty:
        return False, f"빈 필드 {empty}"
    if o["question_id"] != CANARY_ID:
        return False, f"question_id 반향 실패 {o['question_id']!r}"
    if len(o["answer"]) < 20:
        return False, f"answer 가 너무 짧다 ({len(o['answer'])}자)"
    return True, f"5필드 정상 (answer {len(o['answer'])}자)"


def deep_probe() -> tuple[bool, str]:
    """`/selfcheck` — **캐시를 우회**해 조회 파이프라인을 돌린다.

    얕은 검사의 카나리아는 두 번째부터 캐시 적중이라, 스토어가 통째로 사라져도
    초록이 유지된다. 그것을 잡는 것이 이 검사의 «유일한» 목적이다.
    """
    try:
        status, body = _get("/selfcheck", PROBE_TIMEOUT)
    except Exception as e:                                     # noqa: BLE001
        return False, f"요청 실패 {type(e).__name__}: {e}"
    if status != 200:
        return False, f"HTTP {status}"
    try:
        o = json.loads(body)
    except Exception:                                          # noqa: BLE001
        return False, "JSON 이 아니다"
    if o.get("status") != "ok":
        return False, f"status={o.get('status')!r} pipeline={o.get('pipeline')}"
    return True, f"파이프라인 정상 ({o.get('elapsed_sec')}초) {o.get('pipeline')}"


# ══════════════════════════════════════════════════════════════════════
#  프로세스 관리
# ══════════════════════════════════════════════════════════════════════
def spawn() -> subprocess.Popen:
    cmd = [sys.executable, str(ROOT / "scripts" / "run_server.py"),
           "--host", "0.0.0.0", "--port", str(PORT)]
    log(f"서버 기동: {' '.join(cmd)}")
    # ★ 프로세스 «그룹»을 새로 만든다 — SIGSTOP 걸린 자식과 그 손자까지 함께 거둔다.
    return subprocess.Popen(cmd, start_new_session=True)


def reap(proc: subprocess.Popen) -> None:
    """SIGTERM → 안 죽으면 SIGKILL. **그룹째** 보낸다.

    ★ SIGSTOP 걸린 프로세스는 SIGTERM 을 «받아 두기만 하고 처리하지 않는다».
      그래서 SIGCONT 로 깨운 뒤 TERM 을 보내야 한다. 안 그러면 TERM_WAIT 를 통째로
      기다린 끝에 KILL 로 가게 되고, 그동안 서비스가 죽어 있다.
    """
    if proc.poll() is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
    except OSError:
        pgid = None

    def send(sig: int) -> None:
        try:
            if pgid is not None:
                os.killpg(pgid, sig)
            else:
                proc.send_signal(sig)
        except (OSError, ProcessLookupError):
            pass

    send(signal.SIGCONT)          # ★ 멈춰 있으면 먼저 깨운다
    send(signal.SIGTERM)
    t0 = time.time()
    while time.time() - t0 < TERM_WAIT:
        if proc.poll() is not None:
            log(f"서버 종료 (SIGTERM · {time.time() - t0:.1f}초)")
            return
        time.sleep(0.3)
    log(f"★ SIGTERM 을 {TERM_WAIT:.0f}초 무시했다 → SIGKILL")
    send(signal.SIGKILL)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        log("★★ SIGKILL 후에도 안 거둬졌다")


def wait_warm(proc: subprocess.Popen) -> bool:
    """워밍업 유예 안에 «규격대로 답하는» 상태가 되는가."""
    t0 = time.time()
    while time.time() - t0 < WARMUP_GRACE:
        if proc.poll() is not None:
            log(f"★ 서버가 워밍업 중에 죽었다 (rc={proc.returncode})")
            return False
        ok, why = shallow_probe(WARMUP_PROBE_TIMEOUT)
        if ok:
            log(f"워밍업 완료 {time.time() - t0:.2f}초 — {why}")
            return True
        time.sleep(2)
    log(f"★★ 워밍업 유예 {WARMUP_GRACE:.0f}초 초과 — {shallow_probe(WARMUP_PROBE_TIMEOUT)[1]}")
    return False


# ══════════════════════════════════════════════════════════════════════
def once() -> int:
    """진단용 — 이미 떠 있는 서버에 두 검사를 한 번씩만."""
    ok_s, why_s = shallow_probe()
    print(f"  얕은 검사 (/answer 5필드)   {'✅' if ok_s else '★★'}  {why_s}")
    ok_d, why_d = deep_probe()
    print(f"  깊은 검사 (/selfcheck)      {'✅' if ok_d else '★★'}  {why_d}")
    return 0 if (ok_s and ok_d) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="워치독")
    ap.add_argument("--once", action="store_true", help="한 바퀴만 (서버를 안 띄운다)")
    a = ap.parse_args()
    if a.once:
        return once()

    worst = FAIL_LIMIT * (RETRY_INTERVAL + PROBE_TIMEOUT) + INTERVAL
    log(f"감시 시작 · 포트 {PORT} · 주기 {INTERVAL:.0f}초(실패중 {RETRY_INTERVAL:.0f}초) "
        f"· 탐침 {PROBE_TIMEOUT:.0f}초 · 유예 {WARMUP_GRACE:.0f}초 · 실패한도 {FAIL_LIMIT} "
        f"· 깊은검사 {DEEP_EVERY}주기마다 · **최악 검출지연 {worst:.0f}초**")

    stop = {"v": False}

    def on_term(_sig, _frm):
        log("종료 신호 수신 → 서버를 거두고 나간다")
        stop["v"] = True

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)

    proc: subprocess.Popen | None = None
    spawns = fails = ticks = 0
    try:
        while not stop["v"]:
            if proc is None or proc.poll() is not None:
                if proc is not None:
                    log(f"★ 서버가 죽어 있다 (rc={proc.returncode}) → 되살린다")
                proc = spawn()
                spawns += 1
                if not wait_warm(proc):
                    reap(proc)
                    proc = None
                    continue
                fails = 0

            # ★ 실패가 쌓이는 «중»에는 주기를 짧게 쓴다 — 고장 났는데 30초를 기다릴 이유가 없다
            time.sleep(RETRY_INTERVAL if fails else INTERVAL)
            if stop["v"]:
                break
            ticks += 1

            ok, why = shallow_probe()
            if ok and DEEP_EVERY > 0 and ticks % DEEP_EVERY == 0:
                ok, why = deep_probe()
                why = "[깊은] " + why

            if ok:
                if fails:
                    log(f"회복 — {why}")
                fails = 0
                continue

            fails += 1
            log(f"★ 검사 실패 {fails}/{FAIL_LIMIT} — {why}")
            if fails >= FAIL_LIMIT:
                log("★★ 한도 초과 → 서버를 거두고 다시 띄운다 "
                    "(컨테이너가 아니라 «안의 프로세스»만 — 볼륨 재마운트가 없다)")
                reap(proc)
                proc = None
                fails = 0
    finally:
        if proc is not None:
            reap(proc)
    log(f"감시 종료 (기동 {spawns}회 · 주기 {ticks}회)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
