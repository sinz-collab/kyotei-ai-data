
from pathlib import Path
import importlib.util
import sys
import traceback

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
test_file = root / "tests" / "test_engine.py"
spec = importlib.util.spec_from_file_location("test_engine", test_file)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

tests = [getattr(mod, n) for n in dir(mod) if n.startswith("test_")]
failed = 0
for test in tests:
    try:
        test()
        print(f"PASS {test.__name__}")
    except Exception:
        failed += 1
        print(f"FAIL {test.__name__}")
        traceback.print_exc()
if failed:
    raise SystemExit(1)
print(f"ALL PASS: {len(tests)}")
