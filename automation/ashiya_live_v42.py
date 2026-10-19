from __future__ import annotations

import math
from copy import deepcopy
from itertools import permutations


MODEL_VERSION = "ashiya_live_v42_20261017"
NORMAL_MODEL = "safe2"
FLY_TICKET_MODEL = "strength_25_frozen"
FLY_PROBABILITY_MODEL = "e_defense_display_frozen"

# Learned on 2026-06-01..2026-08-29 only.  Invalid combinations remain zero.
NORMAL_PRIOR = [
    [[0, 0, 0, 0, 0, 0], [0, 0, .1015625, .09635416666666667, .06510416666666667, .0546875], [0, .07291666666666667, 0, .07552083333333333, .0703125, .036458333333333336], [0, .07552083333333333, .07552083333333333, 0, .033854166666666664, .033854166666666664], [0, .044270833333333336, .041666666666666664, .0234375, 0, .0234375], [0, .028645833333333332, .018229166666666668, .015625, .013020833333333334, 0]],
    [[0, 0, .05825242718446602, .08737864077669903, .08737864077669903, .07766990291262135], [0, 0, 0, 0, 0, 0], [.07766990291262135, 0, 0, .04854368932038835, .04854368932038835, .05825242718446602], [.038834951456310676, 0, .02912621359223301, 0, .07766990291262135, .04854368932038835], [.06796116504854369, 0, .019417475728155338, .04854368932038835, 0, .04854368932038835], [.019417475728155338, 0, .019417475728155338, .019417475728155338, .019417475728155338, 0]],
    [[0, .06956521739130435, 0, .08695652173913043, .09565217391304348, .08695652173913043], [.034782608695652174, 0, 0, .06086956521739131, .043478260869565216, .034782608695652174], [0, 0, 0, 0, 0, 0], [.043478260869565216, .05217391304347826, 0, 0, .0782608695652174, .05217391304347826], [.017391304347826087, .034782608695652174, 0, .02608695652173913, 0, .02608695652173913], [.043478260869565216, .043478260869565216, 0, .02608695652173913, .043478260869565216, 0]],
    [[0, .06382978723404255, .06382978723404255, 0, .10638297872340426, .06382978723404255], [.06382978723404255, 0, .02127659574468085, 0, .05319148936170213, .0425531914893617], [.031914893617021274, .02127659574468085, 0, 0, .0425531914893617, .031914893617021274], [0, 0, 0, 0, 0, 0], [.06382978723404255, .031914893617021274, .031914893617021274, 0, 0, .10638297872340426], [.02127659574468085, .07446808510638298, .02127659574468085, 0, .0425531914893617, 0]],
    [[0, .05128205128205128, .05128205128205128, .0641025641025641, 0, .0641025641025641], [.038461538461538464, 0, .038461538461538464, .0641025641025641, 0, .038461538461538464], [.038461538461538464, .0641025641025641, 0, .038461538461538464, 0, .038461538461538464], [.07692307692307693, .05128205128205128, .05128205128205128, 0, 0, .05128205128205128], [0, 0, 0, 0, 0, 0], [.05128205128205128, .05128205128205128, .05128205128205128, .02564102564102564, 0, 0]],
    [[0, .05357142857142857, .05357142857142857, .05357142857142857, .03571428571428571, 0], [.07142857142857142, 0, .03571428571428571, .03571428571428571, .05357142857142857, 0], [.05357142857142857, .03571428571428571, 0, .07142857142857142, .07142857142857142, 0], [.03571428571428571, .05357142857142857, .03571428571428571, 0, .05357142857142857, 0], [.07142857142857142, .03571428571428571, .05357142857142857, .03571428571428571, 0, 0], [0, 0, 0, 0, 0, 0]],
]

STRENGTH_MEAN = [-.0909246704331451, -.21698870056497205, -.09983568738229923, -.07134227871939718, -.17393596986817414, -.007303829252981787, -.0020841180163214024, -.003810420590084257, -.0036785938480853706, .2049854748724803, .19839924670433146, -.012428556482228806, .600282485875706, .07379015300978589, -.3181233521657252, -.1435044700887449, .07608286252354049, .1713747645951036, .1711864406779661, .17928436911487758, .1935969868173258, .7992467043314501, .2672316384180791, .4444444444444444]
STRENGTH_SCALE = [.557372185732101, .9259015659634027, .49145456296103646, .445595859592896, .4554369108297374, .16652938047775687, .0965079497909811, .13209928944798502, .1475852435930431, .3656606323536519, .4349624729136142, .4078116322814695, .3594346478707716, .391913351848038, .7772581855755046, .471641907948523, .26513064808459863, .37683611113464904, .3766717977311028, .38359025548880704, .3951166834146413, .40056386500376134, .4425142820818844, .49690399499994703]
STRENGTH_COEF = [.44000996100782463, -.08566723197126855, .13378801321865236, .03237151145948365, .07710118583739266, .05999571155907325, .0203129246538721, .003565489340827017, .029956508512689137, .09765267980241532, .07064385545706431, -.05018385413818533, -.01708644037699088, -.02602451159519251, .22565256681219598, .10372209198383808, .29539870297826737, .2706795731884925, .2052182294559005, .23944748926222553, .1354302065869174, .220773234153683, .10519573232824629, .21405579697649346]
STRENGTH_INTERCEPT = -1.4284865072558068

E_COEF = [.634361296702568, .23373084063553495, .2515878004289787, .23992147793912597, .056106414279724855, .5926087581535249, .1001978937192474, .3315796207045374, -.01738992310029474]


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _value(row, *keys):
    for key in keys:
        value = _number(row.get(key))
        if value is not None:
            return value
    return None


def _combo(ticket):
    if isinstance(ticket, str):
        return ticket.strip()
    if isinstance(ticket, dict):
        return str(ticket.get("combination") or ticket.get("combo") or ticket.get("ticket") or "").strip()
    return ""


def _valid_combo(combo):
    parts = combo.split("-")
    return len(parts) == 3 and len(set(parts)) == 3 and all(part in "123456" and len(part) == 1 for part in parts)


def _mean(values):
    return sum(values) / len(values)


def _clip(value, low, high):
    return min(high, max(low, value))


def _softmax(values):
    ceiling = max(values)
    weights = [math.exp(value - ceiling) for value in values]
    total = sum(weights)
    return [value / total for value in weights]


def _sigmoid(value):
    value = _clip(value, -20.0, 20.0)
    return 1.0 / (1.0 + math.exp(-value))


def _racers(race):
    rows = race.get("racers") or race.get("entries") or []
    if not isinstance(rows, list) or len(rows) != 6:
        return None
    try:
        ordered = sorted(rows, key=lambda row: int(row.get("lane")))
    except (TypeError, ValueError):
        return None
    if [int(row.get("lane")) for row in ordered] != list(range(1, 7)):
        return None
    try:
        courses = [int(row.get("actual_course") or row.get("entry_course") or row.get("lane")) for row in ordered]
    except (TypeError, ValueError):
        return None
    if sorted(courses) != list(range(1, 7)):
        return None
    return ordered


def _history(row):
    runs = row.get("season_runs")
    if not isinstance(runs, list):
        return None
    ranks = []
    starts = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        raw_finish = str(run.get("finish") or run.get("rank") or "")
        digits = "".join(character for character in raw_finish if character.isdigit())
        if digits:
            ranks.append(min(4, max(1, int(digits))))
        start = _value(run, "st", "start_timing", "start_time")
        if start is not None:
            starts.append(start)
    return ranks, starts


def _win_probabilities(result):
    rows = result.get("probabilities")
    if not isinstance(rows, list) or len(rows) != 6:
        return None
    values = [None] * 6
    for row in rows:
        try:
            lane = int(row.get("lane"))
        except (AttributeError, TypeError, ValueError):
            return None
        value = _number(row.get("win"))
        if not 1 <= lane <= 6 or value is None or value <= 0:
            return None
        values[lane - 1] = value
    if any(value is None for value in values):
        return None
    total = sum(values)
    return [value / total for value in values]


def _prepared_features(race):
    racers = _racers(race)
    if racers is None:
        return None, "actual_entry_incomplete"
    prepared = []
    for row in racers:
        history = _history(row)
        history_available = history is not None
        ranks, starts = history or ([], [])
        recent = ranks[-2:]
        earlier = ranks[:-2]
        values = {
            "id": int(row["lane"]),
            "pos": int(row.get("actual_course") or row.get("entry_course") or row["lane"]),
            "nat": _value(row, "national_win_rate", "nat_win", "nat_win_rate"),
            "local": _value(row, "local_win_rate", "local_win", "local_rate"),
            "motor": _value(row, "motor_top2_rate", "motor_2", "motor_2_rate"),
            "boat": _value(row, "boat_top2_rate", "boat_2", "boat_2_rate"),
            "ex": _value(row, "exhibition_time"),
            "st": _value(row, "start_timing", "start_time"),
            "lap": _value(row, "lap_time"),
            "straight": _value(row, "straight_time"),
            "turn": _value(row, "turn_time"),
            "n": len(ranks),
            "form": _mean(ranks) if ranks else 3.5,
            "normal_form": _mean(ranks[-5:]) if ranks else 3.5,
            "latest": _mean(recent) if recent else 3.5,
            "trend": _mean(earlier) - _mean(recent) if earlier and recent else 0.0,
            "real_st": _mean(starts) if starts else .17,
            "history_available": history_available,
        }
        prepared.append(values)
    return prepared, None


def _strength_vectors(prepared):
    required = ("nat", "local", "motor", "boat", "ex", "st", "lap", "straight", "turn")
    if any(not row["history_available"] or any(row[key] is None for key in required) for row in prepared):
        return None, "strength_features_incomplete"
    strengths = [row["nat"] for row in prepared]
    starts = [row["st"] for row in prepared]
    laps = [row["lap"] for row in prepared]
    straights = [row["straight"] for row in prepared]
    turns = [row["turn"] for row in prepared]
    vectors = []
    for row in prepared:
        wall = [other for other in prepared if 1 < other["pos"] < row["pos"]]
        wall_strength = max((other["nat"] for other in wall), default=5.0)
        wall_st = min((other["real_st"] for other in wall), default=.17)
        vectors.append([
            (row["nat"] - _mean(strengths)) / 2,
            (row["local"] - 5) / 2,
            (row["motor"] - 35) / 20,
            (row["boat"] - 35) / 20,
            (6.8 - row["ex"]) / .15,
            (_mean(laps) - row["lap"]) / .6,
            (_mean(straights) - row["straight"]) / .15,
            (_mean(turns) - row["turn"]) / .3,
            (_mean(starts) - row["st"]) / .15,
            (3.5 - row["form"]) / 2,
            (3.5 - row["latest"]) / 2,
            row["trend"] / 2,
            min(row["n"], 6) / 6,
            (.17 - row["real_st"]) / .12,
            (row["nat"] - wall_strength) / 2,
            (wall_st - row["real_st"]) / .12,
            float(row["pos"] == 1), float(row["pos"] == 2), float(row["pos"] == 3),
            float(row["pos"] == 4), float(row["pos"] == 5),
        ])
    return vectors, None


def _normal_safe2(result, prepared, probabilities):
    tickets = result.get("tickets")
    if not isinstance(tickets, dict):
        return False, "ticket_groups_missing", 0
    groups = [tickets.get("main"), tickets.get("deviation"), tickets.get("upset")]
    if any(not isinstance(group, list) for group in groups) or [len(group) for group in groups] != [6, 3, 1]:
        return False, "ticket_groups_unknown", 0
    original_objects = [deepcopy(ticket) for group in groups for ticket in group]
    original = [_combo(ticket) for ticket in original_objects]
    if len(set(original)) != 10 or any(not _valid_combo(ticket) for ticket in original):
        return False, "ticket_groups_invalid", 0
    required = ("nat", "motor", "lap", "turn", "straight", "st")
    if any(not row["history_available"] or any(row[key] is None for key in required) for row in prepared):
        return False, "safe2_features_incomplete", 0
    ability = {}
    normal_scale = [2, 20, .5, .3, .15, .14, 1.3]
    # Reconstruct the exact seven safe2 inputs from the shared prepared rows.
    base_rows = []
    for row in prepared:
        base_rows.append([row["nat"], row["motor"], row["lap"], row["turn"], row["straight"], row["st"], 3.5 - row["normal_form"]])
    means = [_mean([row[column] for row in base_rows]) for column in range(7)]
    for index, row in enumerate(base_rows, 1):
        z = [_clip((row[column] - means[column]) / normal_scale[column], -2, 2) for column in range(7)]
        ability[index] = .22*z[0] + .09*z[1] - .12*z[2] - .10*z[3] - .10*z[4] - .05*z[5] + .12*z[6]
    scored = []
    for a, b, c in permutations(range(1, 7), 3):
        base = probabilities[a-1] * (.1 + probabilities[b-1])**.65 * (.1 + probabilities[c-1])**.43
        conditional = NORMAL_PRIOR[a-1][b-1][c-1]
        display = math.exp(.50*ability[a] + .55*ability[b] + .40*ability[c])
        adjusted = base * (conditional / .05)**.35 * display
        scored.append((f"{a}-{b}-{c}", base, adjusted))
    score_by_combo = {combo: score for combo, _, score in scored}
    ranked = sorted(scored, key=lambda item: (-item[2], item[0]))
    chosen = list(original)
    baseline = set(original)
    for combo, _, candidate_score in ranked:
        if combo in chosen:
            continue
        weakest = min(((ticket, score_by_combo[ticket], index) for index, ticket in enumerate(chosen)), key=lambda item: (item[1], item[2]))
        if len(baseline - set(chosen)) >= 2:
            break
        if candidate_score > weakest[1] * 1.25:
            chosen[weakest[2]] = combo
    changes = sum(before != after for before, after in zip(original, chosen))
    if changes:
        updated = []
        for index,(before, after, old) in enumerate(zip(original, chosen, original_objects),1):
            if before == after:
                updated.append(old)
            else:
                category="main" if index<=6 else ("deviation" if index<=9 else "scenario_hole")
                updated.append({
                    "combination": after,
                    "score": round(score_by_combo[after], 8),
                    "category": category,
                    "selection_order": index,
                    "reason": "芦屋LIVE safe2補正（同一区分内置換）",
                    "score_components": {"safe2_adjusted_score": round(score_by_combo[after], 8)},
                })
        tickets["main"] = updated[:6]
        tickets["deviation"] = updated[6:9]
        tickets["upset"] = updated[9:]
        tickets["all"] = updated
    return True, None, changes


def _strength_decision(vector):
    standardized = [(value - mean) / scale for value, mean, scale in zip(vector, STRENGTH_MEAN, STRENGTH_SCALE)]
    return STRENGTH_INTERCEPT + sum(value * coefficient for value, coefficient in zip(standardized, STRENGTH_COEF))


def _fly_tickets(vectors, probabilities):
    candidates = []
    for a in range(2, 7):
        others = [lane for lane in range(1, 7) if lane != a]
        second_scores = {lane: _strength_decision(vectors[lane-1] + [float(lane > a), float(abs(lane-a) == 1), 0.0]) for lane in others}
        second_probabilities = dict(zip(others, _softmax([second_scores[lane] for lane in others])))
        for b in others:
            remaining = [lane for lane in others if lane != b]
            third_scores = {lane: _strength_decision(vectors[lane-1] + [float(lane > a), float(abs(lane-a) == 1), 1.0]) for lane in remaining}
            third_probabilities = dict(zip(remaining, _softmax([third_scores[lane] for lane in remaining])))
            for c in remaining:
                base = probabilities[a-1] * (.1 + probabilities[b-1])**.65 * (.1 + probabilities[c-1])**.43
                conditional = probabilities[a-1] * second_probabilities[b] * third_probabilities[c]
                candidates.append([f"{a}-{b}-{c}", base, conditional])
    base_total = sum(row[1] for row in candidates)
    conditional_total = sum(row[2] for row in candidates)
    for row in candidates:
        scaled_conditional = row[2] * base_total / conditional_total
        row.append(.75 * row[1] + .25 * scaled_conditional)
    candidates.sort(key=lambda row: (-row[3], row[0]))
    score_total = sum(row[3] for row in candidates)
    return [
        {"combo": row[0], "role": "飛び", "rank": rank, "probability": round(100 * row[3] / score_total, 4)}
        for rank, row in enumerate(candidates[:10], 1)
    ]


def _display_features(prepared):
    def difference(key):
        values = [row[key] for row in prepared]
        return _clip(_mean(values[1:]) - values[0], -.7, .7)
    exhibition = [difference("ex") / .2, difference("st") / .2]
    original_keys = ("lap", "straight", "turn")
    if any(row[key] is None for row in prepared for key in original_keys):
        original = [0.0, 0.0, 0.0]
    else:
        original = [difference("lap") / .8, difference("straight") / .25, difference("turn") / .5]
    return exhibition + original


def _lane1_fly_probability(prepared, probabilities):
    if any(row[key] is None for row in prepared for key in ("nat", "motor", "ex", "st")):
        return None, "e_features_incomplete", None
    power = [(row["nat"] - 5) / 2 for row in prepared]
    motor = [(row["motor"] - 35) / 15 for row in prepared]
    defense = [power[0] - _mean(power[1:]), motor[0] - _mean(motor[1:])]
    display = [_clip(value, -2, 2) for value in _display_features(prepared)]
    p = _clip(probabilities[0], 1e-7, 1 - 1e-7)
    design = [math.log(p / (1-p)), *defense, *display, 1.0]
    escape = _sigmoid(sum(value * coefficient for value, coefficient in zip(design, E_COEF)))
    return round((1.0 - escape) * 100.0, 2), None, escape


def apply_ashiya_live_v42(result, race):
    """Apply three frozen Ashiya-only LIVE models without reading odds/results."""
    output = deepcopy(result)
    audit = output.setdefault("audit", {})
    integration = {
        "version": MODEL_VERSION,
        "stage": "live",
        "odds_used": False,
        "result_used": False,
    }
    probabilities = _win_probabilities(output)
    prepared, base_reason = _prepared_features(race)
    if probabilities is None:
        base_reason = "base_probabilities_invalid"
    if base_reason:
        integration.update({
            "normal": {"model": NORMAL_MODEL, "status": "fallback", "reason": base_reason, "replacements": 0},
            "flyTickets": {"model": FLY_TICKET_MODEL, "status": "unavailable", "reason": base_reason},
            "flyProbability": {"model": FLY_PROBABILITY_MODEL, "status": "unavailable", "reason": base_reason},
        })
        output["flyPrediction"] = {"status": "unavailable", "modelVersion": FLY_TICKET_MODEL, "tickets": [], "reason": base_reason}
        output["lane1FlyProbability"] = None
        audit["ashiya_live_v42"] = integration
        return output
    normal_ok, normal_reason, replacements = _normal_safe2(output, prepared, probabilities)
    integration["normal"] = {
        "model": NORMAL_MODEL,
        "status": "applied" if normal_ok else "fallback",
        "reason": normal_reason,
        "replacements": replacements,
    }
    vectors, fly_reason = _strength_vectors(prepared)
    if fly_reason:
        output["flyPrediction"] = {"status": "unavailable", "modelVersion": FLY_TICKET_MODEL, "tickets": [], "reason": fly_reason}
        integration["flyTickets"] = {"model": FLY_TICKET_MODEL, "status": "unavailable", "reason": fly_reason}
    else:
        fly_tickets = _fly_tickets(vectors, probabilities)
        output["flyPrediction"] = {
            "status": "live",
            "modelVersion": FLY_TICKET_MODEL,
            "probabilityModel": FLY_PROBABILITY_MODEL,
            "tickets": fly_tickets,
            "candidateCount": 100,
            "oddsUsed": False,
            "resultUsed": False,
        }
        integration["flyTickets"] = {"model": FLY_TICKET_MODEL, "status": "applied", "tickets": len(fly_tickets)}
    fly_probability, probability_reason, escape_probability = _lane1_fly_probability(prepared, probabilities)
    output["lane1FlyProbability"] = fly_probability
    output["lane1FlyProbabilityLive"] = fly_probability
    output["lane1FlyProbabilitySource"] = "live"
    output["lane1FlyProbabilityModel"] = FLY_PROBABILITY_MODEL
    output["lane1FlyProbabilityStatus"] = "ok" if probability_reason is None else "unavailable"
    output["lane1FlyProbabilityDisplayStage"] = "live"
    output["lane1FlyProbabilityIsProvisional"] = False
    integration["flyProbability"] = (
        {"model": FLY_PROBABILITY_MODEL, "status": "applied", "value": fly_probability, "escapeProbabilityRaw": escape_probability, "flyProbabilityRaw": 1.0 - escape_probability, "complementAppliedOnce": True}
        if probability_reason is None
        else {"model": FLY_PROBABILITY_MODEL, "status": "unavailable", "reason": probability_reason}
    )
    audit["ashiya_live_v42"] = integration
    return output
