"""HyperCLOVA X 호출 래퍼.

대회 규칙상 LLM 은 HCX 만 쓸 수 있다. 모든 호출이 여기를 지난다.

여기서 반드시 하는 것 3가지:
  1) **디스크 캐시** ― 같은 입력 재호출 금지. 크레딧이 서버·네트워크까지 함께 차감되므로
     개발 중 반복 실행이 그대로 비용이다.
  2) **429/5xx 자체 재시도 + 지수 백오프** ― 주최측의 재시도 2회는 '주최측 → 우리 서버' 구간이다.
     '우리 서버 → CLOVA' 구간은 우리 책임이고, 다른 팀도 429 를 겪는 중이다.
  3) **thinking 은 기본 low** ― high 는 최종 검증 때만.

환경변수: CLOVASTUDIO_API_KEY (.env 또는 시스템 환경변수)
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "io"))
import loader  # noqa: E402

BASE_URL = "https://clovastudio.stream.ntruss.com/v1/openai"
DEFAULT_MODEL = "HCX-005"          # v0 는 경량 모델. 최종 답변만 HCX-007 로 올린다.
# ★ 문항당 예산이 **300초**(주최측 규격)다. 5회(누적 31초)에서 포기할 이유가 없다.
#   실측 2026-08-16: 연속 호출 중 429 가 5회 연속 나 답변 하나가 통째로 폐기됐다
#   (「언어모델 호출이 실패해 생성 답변을 폐기했습니다」). 429 는 **우리 책임 구간**이다.
#   8회 = 누적 1+2+4+8+16+30+30 = 91초. 내부 타임아웃 280초 안에 넉넉히 들어간다.
# ★ 2026-09-04 — 환경변수로만 올릴 수 있게 열어 둔다. **기본값은 8 그대로**이므로
#   서버·평가·시험의 동작은 한 글자도 안 바뀐다. 여는 이유는 하나다 —
#   벌크 채점이 분당 한도를 포화시키면 8회가 ~91초 만에 소진되고 그 문항이
#   폴백으로 떨어진다(R51 실측). 운영(순차 1건씩)에서는 일어나지 않는 «계측 인공물»이다.
#   ★ 올려서 쓰면 «잰 자가 다르다» — 그 수치를 인용할 때 반드시 병기하라.
MAX_RETRY = int(os.environ.get("GONGSI_HCX_MAX_RETRY", "8"))
# 이 시간보다 적게 남았으면 새 시도를 시작하지 않는다 (시작해 봐야 못 끝낸다)
_MIN_ATTEMPT_SEC = 8.0

# ── 문항 단위 벽시계 예산 ─────────────────────────────────────────────────
# `answerer` 는 한 문항에 `chat` 을 최대 4~5회 부른다. 호출마다 인자를 넘기는 대신
# **스레드별 예산**을 한 번 심어 두고 모든 호출이 같은 시한을 공유하게 한다.
# (서버는 워커 스레드에서 `answerer.answer` 를 부르므로 스레드 지역이 맞다.)
_BUDGET = threading.local()


def set_deadline(monotonic_at: float | None) -> None:
    """이 스레드의 HCX 예산 시한을 정한다. None 이면 해제."""
    _BUDGET.value = monotonic_at


def remaining() -> float | None:
    """남은 예산(초). 예산이 없으면 None."""
    at = getattr(_BUDGET, "value", None)
    return None if at is None else at - time.monotonic()


def _api_key() -> str:
    key = os.environ.get("CLOVASTUDIO_API_KEY", "").strip()
    if not key:
        env = Path(loader.CFG.get("data_root", ".")).parents[0] / "10_프로젝트" / "gongsi-agent" / ".env"
        for candidate in (Path.cwd() / ".env", env):
            if candidate.exists():
                for line in candidate.read_text(encoding="utf-8").splitlines():
                    if line.strip().startswith("CLOVASTUDIO_API_KEY"):
                        key = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
            if key:
                break
    if not key:
        raise RuntimeError(
            "CLOVASTUDIO_API_KEY 가 없습니다.\n"
            "  10_프로젝트\\gongsi-agent\\.env 파일에 아래 한 줄을 넣으세요:\n"
            "  CLOVASTUDIO_API_KEY=nv-xxxxxxxx"
        )
    return key


def _cache_dir() -> Path:
    path = Path(loader.CFG.get("llm_cache", str(Path(loader.DERIVED_DIR) / "cache")))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_key(payload: dict) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def chat(messages: list[dict], *, model: str = DEFAULT_MODEL, temperature: float = 0.1,
         max_tokens: int = 4096, thinking: str | None = None,
         use_cache: bool = True, deadline: float | None = None) -> dict:
    """HCX 채팅 호출. {'text': ..., 'thinking': ..., 'cached': bool, 'elapsed': float} 반환.

    `deadline` — `time.monotonic()` 기준 «이 시각까지만 시도한다». 넘기면 즉시 raise 한다.

    ★ 2026-08-22 신설. 이게 없어서 **실격급 구멍**이었다.
      HTTP 타임아웃 240초 × MAX_RETRY 8회 + 백오프 121초 = **최악 2,041초(34분)**.
      주최측 문항당 한도는 **300초**이므로 `chat` 단 한 번이 한도를 7배 넘길 수 있었다.
      게다가 `server.py` 의 `future.result(timeout=280)` 은 **기다리기를 포기할 뿐
      스레드를 죽이지 않는다.** 워커가 2개뿐이라 HCX 가 두 번 매달리면 그 뒤 문항이
      전부 큐에서 대기하다 줄줄이 타임아웃 응답이 된다 — 주최측 재시도 2회까지 같이 태운다.

      폴백(`answerer.code_only_answer`)은 이미 갖춰져 있었다. 여기서 제때 raise 해 주면
      그쪽으로 넘어가 **5필드 200 응답**이 나간다. 늦게 맞는 답보다 제때 나가는 답이 낫다.
    """
    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        # B단계 실측(2026-08-14): max_tokens 는 HCX-007 에서 400 (Invalid parameter).
        # max_completion_tokens 는 005·007 양쪽에서 동작 → 이걸로 통일.
        # 단 tools 를 쓸 땐 토큰 한도 파라미터 자체를 생략해야 한다 (넣으면 400).
        "max_completion_tokens": max_tokens,
    }
    if thinking:                       # HCX-007 추론 모델에서만 유효. 'none' 이면 추론 강제 해제
        payload["thinking"] = {"effort": thinking}

    cache_file = _cache_dir() / f"{_cache_key(payload)}.json"
    if use_cache and cache_file.exists():
        hit = json.loads(cache_file.read_text(encoding="utf-8"))
        hit["cached"] = True
        return hit

    headers = {"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"}
    started = time.time()
    last_error = ""
    if deadline is None:                       # 인자가 없으면 스레드 예산을 쓴다
        deadline = getattr(_BUDGET, "value", None)

    for attempt in range(MAX_RETRY):
        # ── 벽시계 예산 ──────────────────────────────────────────────
        # 남은 시간 안에 «끝날 수 있는» 시도만 시작한다. HTTP 타임아웃도 남은 시간으로 줄인다.
        if deadline is not None:
            left = deadline - time.monotonic()
            if left <= _MIN_ATTEMPT_SEC:
                raise TimeoutError(
                    f"HCX 예산 소진 ({attempt}회 시도, 남은 {left:.1f}초) — 폴백으로 넘긴다. "
                    f"마지막 오류: {last_error or '없음'}")
            http_timeout = max(_MIN_ATTEMPT_SEC, min(240.0, left))
        else:
            http_timeout = 240.0

        try:
            res = requests.post(f"{BASE_URL}/chat/completions", headers=headers,
                                json=payload, timeout=http_timeout)
        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            res = None

        if res is not None and res.status_code == 200:
            data = res.json()
            msg = data["choices"][0]["message"]
            out = {
                "text": (msg.get("content") or "").strip(),
                # B단계 실측: OpenAI 호환층은 추론 내용을 reasoning_content 로 준다.
                # (네이티브 v3 는 thinkingContent. thinking_content 는 존재하지 않았다)
                "thinking": (msg.get("reasoning_content") or msg.get("thinking_content") or "").strip(),
                "model": model,
                "usage": data.get("usage", {}),
                "cached": False,
                "elapsed": round(time.time() - started, 2),
            }
            # ★ 2026-08-17 (R21 B215-002). **빈 응답은 캐시하지 않는다.**
            #   007 은 thinking 만 쓰고 content 가 비는 일이 있다. 그걸 캐시하면
            #   같은 질문이 **영원히 빈 답**을 받는다 — 실제로 답변 길이 0 이 나갔고
            #   그건 서버에서 0점이다. 캐시를 비워 두면 다음 호출이 다시 시도한다.
            if use_cache and out["text"]:
                cache_file.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
            return out

        # 429(호출 초과) · 5xx 는 재시도. 그 외는 즉시 실패.
        if res is not None:
            last_error = f"HTTP {res.status_code}: {res.text[:200]}"
            if res.status_code not in (429, 500, 502, 503, 504):
                break

        # 마지막 시도 뒤에는 자지 않는다 (자 봐야 곧장 raise 로 간다)
        if attempt == MAX_RETRY - 1:
            break
        wait = min(2 ** attempt, 30)
        if deadline is not None:
            left = deadline - time.monotonic()
            if left - wait <= _MIN_ATTEMPT_SEC:      # 자고 나면 시도할 시간이 없다
                raise TimeoutError(
                    f"HCX 예산 소진 (백오프 {wait}초 후 남는 시간 {left - wait:.1f}초) — "
                    f"폴백으로 넘긴다. 마지막 오류: {last_error}")
        print(f"  [재시도 {attempt + 1}/{MAX_RETRY}] {last_error} ― {wait}초 대기", file=sys.stderr)
        time.sleep(wait)

    raise RuntimeError(f"HCX 호출 실패: {last_error}")


NATIVE_URL = "https://clovastudio.stream.ntruss.com/v3/chat-completions"


def chat_structured(messages: list[dict], schema: dict, *, model: str = "HCX-007",
                    use_cache: bool = True) -> dict | None:
    """구조화 출력 (JSON 스키마 강제). B단계 실측: 네이티브 v3 + thinking none 만 가능.

    질의 계획(검색 키워드 추출 등) 용도. 실패 시 None ― 호출측이 규칙 경로로 폴백.
    """
    payload = {
        "messages": messages,
        "temperature": 0.1,
        "topP": 0.8,
        "thinking": {"effort": "none"},
        "responseFormat": {"type": "json", "schema": schema},
    }
    cache_file = _cache_dir() / f"st_{_cache_key({**payload, 'model': model})}.json"
    if use_cache and cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    headers = {"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"}
    for attempt in range(3):
        try:
            res = requests.post(f"{NATIVE_URL}/{model}", headers=headers,
                                json=payload, timeout=60)
        except requests.RequestException:
            res = None
        if res is not None and res.status_code == 200:
            try:
                out = json.loads(res.json()["result"]["message"]["content"])
            except (KeyError, json.JSONDecodeError):
                return None
            if use_cache:
                cache_file.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
            return out
        if res is not None and res.status_code not in (429, 500, 502, 503, 504):
            return None
        time.sleep(2 ** attempt)
    return None


EMB_MODEL = "clir-emb-dolphin"      # 1024차원. 실측: 호환층 /embeddings, 단건만 (배치 400)


def embed(text: str, *, use_cache: bool = True) -> list[float]:
    """임베딩 1건. 429/5xx 재시도는 chat 과 동일 정책."""
    text = text[:4000]
    cache_file = _cache_dir() / f"emb_{hashlib.sha256(text.encode('utf-8')).hexdigest()[:32]}.json"
    if use_cache and cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    headers = {"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"}
    last_error = ""
    for attempt in range(MAX_RETRY):
        try:
            res = requests.post(f"{BASE_URL}/embeddings", headers=headers,
                                json={"model": EMB_MODEL, "input": text}, timeout=60)
        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            res = None
        if res is not None and res.status_code == 200:
            vec = res.json()["data"][0]["embedding"]
            if use_cache:
                cache_file.write_text(json.dumps(vec), encoding="utf-8")
            return vec
        if res is not None:
            last_error = f"HTTP {res.status_code}: {res.text[:200]}"
            if res.status_code not in (429, 500, 502, 503, 504):
                break
        time.sleep(min(2 ** attempt, 30))
    raise RuntimeError(f"임베딩 실패: {last_error}")


def ping() -> bool:
    """키가 살아있는지 1회 확인. 크레딧을 거의 안 쓰는 최소 호출."""
    try:
        res = chat([{"role": "user", "content": "안녕하세요라고만 답하세요."}],
                   max_tokens=20, use_cache=False)
        print(f"HCX 응답 OK ({res['elapsed']}초) → {res['text'][:40]}")
        return True
    except Exception as exc:                                  # noqa: BLE001
        print(f"HCX 호출 실패: {exc}")
        return False


if __name__ == "__main__":
    raise SystemExit(0 if ping() else 1)
