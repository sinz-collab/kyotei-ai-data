from toda_live_review_v5 import _build_live_attack_meta, _apply_inside_breakdown


def profiles():
    return {str(i): {"avg_st": .16, "win_rate": 10, "top3_vs_course_avg": 0} for i in range(1, 7)}


def racer(lane, escape=0, sashare=0, makurare=0, mz=0, sashi=0, makuri=0, ms=0, avg_st=.16):
    return {
        "lane": lane,
        "avg_st": avg_st,
        "local_st": "-",
        "nat_win": 5.0,
        "boaters_escape_rate": escape,
        "boaters_sashare_rate": sashare,
        "boaters_makurare_rate": makurare,
        "boaters_makurare_zashi_rate": mz,
        "boaters_sashi_rate": sashi,
        "boaters_makuri_rate": makuri,
        "boaters_makuri_sashi_rate": ms,
    }


def rows(ex_times, sts, straights):
    exhibit = [{"lane": i, "exhibition_time": ex_times[i-1], "start_time": sts[i-1]} for i in range(1, 7)]
    original = [{"lane": i, "straight_time": straights[i-1]} for i in range(1, 7)]
    exrank = {str(l): r for r, l in enumerate(sorted(range(1,7), key=lambda x: ex_times[x-1]), 1)}
    srank = {str(l): r for r, l in enumerate(sorted(range(1,7), key=lambda x: straights[x-1]), 1)}
    return exhibit, original, exrank, srank

# Case A: strong 1 defense, weak center attack -> small inside penalty.
racers = [
    racer(1, escape=65, sashare=7, makurare=7, mz=10, avg_st=.14),
    racer(2, sashi=4, avg_st=.17), racer(3, makuri=3), racer(4, makuri=2), racer(5), racer(6)
]
ex, orig, exr, sr = rows([6.66,6.76,6.81,6.88,6.77,6.76],[-.01,-.03,.09,.27,.03,.04],[6.97,7.15,7.13,7.19,7.11,7.09])
meta = _build_live_attack_meta(racers, profiles(), ex, orig, exr, sr, 5, 3)
adj = {"win": {str(i): 16.7 for i in range(1,7)}}
collapse, penalty, _ = _apply_inside_breakdown(adj, meta)
assert penalty < 2.5, (collapse, penalty, meta)

# Case B: 2 straight/ST attack + 4 aggressive start against less-defended 1 -> meaningful penalty.
racers = [
    racer(1, escape=45, sashare=20, makurare=15, mz=20, avg_st=.18),
    racer(2, sashi=9, avg_st=.16), racer(3, makuri=3), racer(4, makuri=10, ms=6, avg_st=.16), racer(5), racer(6)
]
ex, orig, exr, sr = rows([6.81,6.76,6.76,6.67,6.84,6.81],[.19,.09,.34,-.07,.05,.01],[7.17,7.07,7.10,7.21,7.18,7.21])
meta = _build_live_attack_meta(racers, profiles(), ex, orig, exr, sr, 5, 3)
adj = {"win": {str(i): 16.7 for i in range(1,7)}}
collapse, penalty, attacks = _apply_inside_breakdown(adj, meta)
assert penalty >= 2.0, (collapse, penalty, attacks, meta)
assert meta["2"]["headScore"] > 0.35, meta["2"]
print("toda_v5 attack/inside-breakdown tests passed")
