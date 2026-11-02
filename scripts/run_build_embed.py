# -*- coding: utf-8 -*-
r"""정기공시 섹션 임베딩 빌드 (F단계). 최신본(is_latest=1) 37,837개 섹션.

- 입력: sec_path + 본문 앞 700자 (clir-emb-dolphin, 1024차원)
- 출력: 02_데이터\03_가공\index\sections_emb.jsonl ― {"id", "vec"(float 목록 반올림 5자리)}
- **중단·재개 안전**: 이미 저장된 id 는 건너뛴다. 429 는 clovax.embed 가 백오프.
- 실행 (수 시간, 백그라운드 권장):  py -3 -X utf8 scripts\run_build_embed.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "gongsi" / "llm"))
sys.path.insert(0, str(ROOT / "src" / "gongsi" / "index"))
import clovax     # noqa: E402
import sectiondb  # noqa: E402
import loader     # noqa: E402


def main() -> int:
    out_dir = Path(loader.DERIVED_DIR) / "index"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "sections_emb.jsonl"

    done: set[int] = set()
    if out_path.exists():
        for line in open(out_path, encoding="utf-8"):
            try:
                done.add(json.loads(line)["id"])
            except (json.JSONDecodeError, KeyError):
                continue

    conn = sectiondb.connect()
    rows = conn.execute(
        "SELECT id, sec_path, text FROM sections WHERE is_latest=1 ORDER BY id").fetchall()
    todo = [r for r in rows if r["id"] not in done]
    print(f"대상 {len(rows):,} · 완료 {len(done):,} · 남음 {len(todo):,}")

    started = time.time()
    n_err = n_skip = 0
    with out_path.open("a", encoding="utf-8") as fh:
        for i, r in enumerate(todo, 1):
            vec = None
            # 실측 오류 2종 대응: 429 는 냉각 후 재시도(한도에 안 셈),
            # 400(Context length)은 입력을 줄여 재시도 ― 700자도 토큰 한도를 넘는 섹션이 있다
            for cut in (700, 350, 180):
                text = (r["sec_path"] or "") + "\n" + (r["text"] or "")[:cut]
                try:
                    vec = clovax.embed(text, use_cache=False)
                    break
                except Exception as exc:                    # noqa: BLE001
                    msg = str(exc)
                    if "429" in msg:
                        time.sleep(60)                      # 냉각 후 같은 길이로 1회 더
                        try:
                            vec = clovax.embed(text, use_cache=False)
                            break
                        except Exception:                   # noqa: BLE001
                            n_err += 1
                            break                           # 이번 항목 포기, 다음 재실행 때 재개
                    if "40003" not in msg and "Context length" not in msg:
                        n_err += 1
                        if n_err <= 5:
                            print(f"  [오류] id={r['id']}: {msg[:120]}", file=sys.stderr)
                        break
                    # Context length → 다음 cut 으로 축소 재시도
            if vec is None:
                n_skip += 1
                if n_err > 500:
                    print("영구 오류 500건 초과 ― 중단 (재실행하면 이어서 진행)", file=sys.stderr)
                    return 1
                continue
            fh.write(json.dumps({"id": r["id"], "vec": [round(v, 5) for v in vec]}) + "\n")
            if i % 500 == 0:
                rate = i / (time.time() - started)
                eta = (len(todo) - i) / rate / 60
                print(f"  {i:,}/{len(todo):,} · {rate:.1f}건/초 · 남은 시간 약 {eta:.0f}분")
                fh.flush()

    print(f"완료: 신규 {len(todo) - n_skip:,}건 · 건너뜀 {n_skip}건 (오류 {n_err}) → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
