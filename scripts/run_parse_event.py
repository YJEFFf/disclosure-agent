"""이벤트공시 파싱 실행기.

    python scripts\run_parse_event.py                 전체 실행 + 추출률 리포트
    python scripts\run_parse_event.py --group exchange
    python scripts\run_parse_event.py --acode-report  미확인 ACODE 목록
    python scripts\run_parse_event.py --limit 50      빠른 점검
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gongsi.parse.parse_event import main  # noqa: E402

raise SystemExit(main())
