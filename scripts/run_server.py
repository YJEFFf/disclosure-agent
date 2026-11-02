"""평가용 API 서버 실행.

    py -3 scripts\run_server.py            0.0.0.0:8000
    py -3 scripts\run_server.py --port 80  표준 포트(관리자 권한 필요)
"""
import argparse, sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "gongsi" / "api"))
import uvicorn

ap = argparse.ArgumentParser()
ap.add_argument("--host", default="0.0.0.0")
ap.add_argument("--port", type=int, default=8000)
a = ap.parse_args()
print(f"서버 시작 → http://127.0.0.1:{a.port}/answer?question_id=Q-001&question=테스트")
uvicorn.run("server:app", host=a.host, port=a.port, workers=1)
