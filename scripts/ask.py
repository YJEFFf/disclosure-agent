"""CLI 질의 ― 서버 없이 바로 물어본다.

    py -3 scripts\ask.py "삼성중공업이 2025년에 해지한 계약 금액은?"
    py -3 scripts\ask.py            (대화형)
"""
import sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "gongsi" / "agent"))
import answerer

def show(q):
    out = answerer.answer(q, "CLI")
    print("\n" + "=" * 60)
    print(out["answer"])
    print("=" * 60)
    print("[think_trace]")
    print(out["think_trace"][:1500])
    print(f"\n[retrieved_context {len(out['retrieved_context']):,}자]")

if len(sys.argv) > 1:
    show(" ".join(sys.argv[1:]))
else:
    print("질문을 입력하세요. 엔터만 누르면 종료.")
    while True:
        q = input("\n질문: ").strip()
        if not q:
            break
        try:
            show(q)
        except Exception as exc:
            print("실패:", exc)
