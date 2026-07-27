
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
from toda_master_loader_v5 import TodaMasterV5
m=TodaMasterV5()
p=m.course_profile({"name":"近藤 友宝"},2)
assert p["matched"]
assert p["top3_rate"] is not None
print("Toda v5 master test passed")
