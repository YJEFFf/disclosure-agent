import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src" / "gongsi" / "parse"))
import parse_periodic
raise SystemExit(parse_periodic.main())
