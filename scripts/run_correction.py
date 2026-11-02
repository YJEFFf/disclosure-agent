import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src" / "gongsi" / "index"))
import correction
raise SystemExit(correction.main())
