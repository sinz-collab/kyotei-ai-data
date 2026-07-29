from __future__ import annotations
from dataclasses import dataclass, field
from itertools import permutations
from typing import Dict, List, Optional, Sequence

@dataclass
class RacerInput:
    lane: int
    actual_course: int
    original_lane: Optional[int] = None
    course1_rate: Optional[float] = None
    course1_win_rate: Optional[float] = None
    entry_depth_risk: float = 0.0
    class_rank: str = "B1"
    nat_win: float = 0.0
    nat_top3: float = 0.0
    local_win: float = 0.0
    local_top3: float = 0.0
    avg_st: Optional[float] = None
    motor_2: float = 0.0
    motor_3: float = 0.0
    boat_2: float = 0.0
    boat_3: float = 0.0
    exhibition_time: Optional[float] = None
    exhibition_st: Optional[float] = None
    lap_time: Optional[float] = None
    turn_time: Optional[float] = None
    straight_time: Optional[float] = None
    tilt: float = 0.0
    withdrawn: bool = False

@dataclass
class RaceInput:
    racers: List[RacerInput]
    wind_speed: float = 0.0
    wave_height: float = 0.0
    tide_phase: str = "unknown"
    day_no: int = 1

@dataclass
class Scenario:
    name: str
    weight: float
    attack_course: int
    first: Dict[int, float]
    second: Dict[int, float]
    third: Dict[int, float]
    notes: List[str] = field(default_factory=list)

@dataclass
class Prediction:
    scenarios: List[Scenario]
    trifecta_probabilities: Dict[str, float]
    marginal_first: Dict[int, float]
    marginal_second: Dict[int, float]
    marginal_third: Dict[int, float]
    tickets: List[str]
    sab: str
    diagnostics: List[str]

class KaratsuScenarioEngine:
    VERSION = "1.0"
    CLASS_SCORE = {"A1": 1.00, "A2": 0.68, "B1": 0.38, "B2": 0.12}

    def predict(self, race: RaceInput, ticket_count: int = 10) -> Prediction:
        racers = [r for r in race.racers if not r.withdrawn]
        if len(racers) != 6:
            raise ValueError("Exactly six active racers are required.")

        diagnostics: List[str] = []
        lanes_by_course = [r.lane for r in sorted(racers, key=lambda x: x.actual_course)]
        entry_changed = lanes_by_course != [1, 2, 3, 4, 5, 6]
        if entry_changed:
            diagnostics.append(
                f"Entry changed by actual-course comparison: {lanes_by_course}"
            )
        inner = min(racers, key=lambda r: r.actual_course)
        diagnostics.append(
            f"Course-1 multiplier: "
            f"{self._inner_course_multiplier(inner, entry_changed):.3f}"
        )

        base = {r.lane: self._base_strength(r) for r in racers}
        attack = {r.lane: self._attack_strength(r, racers, race) for r in racers}
        remain = {r.lane: self._remain_strength(r, racers) for r in racers}

        scenarios = self._build_scenarios(racers, race, base, attack, remain)
        tri = self._aggregate_trifectas(scenarios)
        first, second, third = self._marginals(tri)
        first = self._calibrate_first_marginal(first, racers, race, attack)
        tickets = self._select_tickets(tri, first, ticket_count)
        sab = self._sab(first, scenarios, entry_changed)
        self._audit(tickets, tri, first, diagnostics)
        return Prediction(scenarios, tri, first, second, third, tickets, sab, diagnostics)

    def _base_strength(self, r: RacerInput) -> float:
        cls = self.CLASS_SCORE.get(r.class_rank, 0.35)
        national = 0.65*self._scale(r.nat_win, 2, 8) + 0.35*self._scale(r.nat_top3, 15, 85)
        local = 0.55*self._scale(r.local_win, 1, 8) + 0.45*self._scale(r.local_top3, 5, 85)
        machine = (
            0.45*self._scale(r.motor_2, 15, 50)
            + 0.25*self._scale(r.motor_3, 30, 70)
            + 0.20*self._scale(r.boat_2, 15, 50)
            + 0.10*self._scale(r.boat_3, 30, 70)
        )
        return max(0.01, 0.26*cls + 0.27*national + 0.15*local + 0.17*machine + 0.15*self._course_prior(r.actual_course))

    def _attack_strength(self, r: RacerInput, racers: Sequence[RacerInput], race: RaceInput) -> float:
        score = self._base_strength(r)
        score += 0.20*self._rank_value(r.exhibition_time, racers, "exhibition_time")
        score += 0.15*self._rank_value(r.straight_time, racers, "straight_time")
        score += 0.12*self._rank_value(r.turn_time, racers, "turn_time")
        score += 0.10*self._rank_value(r.lap_time, racers, "lap_time")
        if r.exhibition_st is not None:
            score += 0.05*self._rank_value(r.exhibition_st, racers, "exhibition_st")
        score += {1:0.08, 3:0.09, 4:0.14, 5:0.06}.get(r.actual_course, 0.0)
        if race.wind_speed >= 4 or race.wave_height >= 4:
            score += -0.08 if r.actual_course == 1 else (0.04 if r.actual_course in (2,3,4,5) else 0.0)
        if r.tilt >= 2.0:
            score += 0.08 if r.actual_course >= 4 else 0.02
        return max(0.01, score)

    def _remain_strength(self, r: RacerInput, racers: Sequence[RacerInput]) -> float:
        score = self._base_strength(r)
        score += 0.18*self._rank_value(r.lap_time, racers, "lap_time")
        score += 0.13*self._rank_value(r.turn_time, racers, "turn_time")
        score += 0.08*self._rank_value(r.straight_time, racers, "straight_time")
        if r.actual_course <= 3:
            score += 0.10
        if r.actual_course >= 5 and self._rank_value(r.lap_time, racers, "lap_time") > 0.65:
            score += 0.08
        return max(0.01, score)

    def _inner_course_multiplier(self, r: RacerInput, entry_changed: bool) -> float:
        lane_match = 1.00 if r.lane == 1 else 0.74
        entry_factor = 1.00 if (not entry_changed or r.lane == 1) else 0.82
        depth_factor = max(
            0.72,
            1.00 - 0.28 * max(0.0, min(1.0, r.entry_depth_risk)),
        )

        suitability = 1.00
        if r.course1_win_rate is not None:
            suitability *= 0.82 + 0.36 * self._scale(
                r.course1_win_rate, 20, 75
            )
        elif r.course1_rate is not None:
            suitability *= 0.84 + 0.32 * self._scale(
                r.course1_rate, 20, 75
            )

        return self._clip(
            lane_match * entry_factor * depth_factor * suitability,
            0.48,
            1.00,
        )

    def _build_scenarios(self, racers, race, base, attack, remain):
        by_course = {r.actual_course: r for r in racers}
        inner = by_course[1]
        lanes_by_course = [
            r.lane for r in sorted(racers, key=lambda x: x.actual_course)
        ]
        entry_changed = lanes_by_course != [1, 2, 3, 4, 5, 6]
        inner_multiplier = self._inner_course_multiplier(
            inner, entry_changed
        )

        raw_hold = (
            0.22
            + 0.21 * base[inner.lane]
            + 0.10 * attack[inner.lane]
        )

        if race.wind_speed >= 5 or race.wave_height >= 5:
            raw_hold -= 0.10
        elif race.wind_speed >= 4 or race.wave_height >= 4:
            raw_hold -= 0.07

        if inner.class_rank == "B1":
            raw_hold -= 0.04
        elif inner.class_rank == "B2":
            raw_hold -= 0.08

        outer_attackers = [
            attack[r.lane] for r in racers
            if r.actual_course in (3, 4, 5)
        ]
        strongest_attacker_gap = (
            max(outer_attackers) - attack[inner.lane]
            if outer_attackers else 0.0
        )
        if strongest_attacker_gap >= 0.30:
            raw_hold -= 0.08
        elif strongest_attacker_gap >= 0.18:
            raw_hold -= 0.05
        elif strongest_attacker_gap >= 0.10:
            raw_hold -= 0.03

        hold = self._clip(raw_hold * inner_multiplier, 0.08, 0.46)

        candidates = [2,3,4,5]
        ranked = sorted(candidates, key=lambda c: attack[by_course[c].lane], reverse=True)
        primary, secondary = ranked[:2]

        scenarios = [
            self._scenario("inner_hold", hold, 1, racers, attack, remain, True),
            self._scenario(f"course_{primary}_attack", (1-hold)*0.48, primary, racers, attack, remain, False),
            self._scenario(f"course_{secondary}_attack", (1-hold)*0.32, secondary, racers, attack, remain, False),
            self._mixed("mixed_residual", (1-hold)*0.20, base, remain),
        ]
        total = sum(s.weight for s in scenarios)
        for s in scenarios:
            s.weight /= total
        return scenarios

    def _scenario(self, name, weight, attack_course, racers, attack, remain, inner_holds):
        by_course = {r.actual_course: r for r in racers}
        attacker = by_course[attack_course]
        inner = by_course[1]
        first, second, third = {}, {}, {}

        if inner_holds:
            first[inner.lane] = 0.78
            primary_course = max([2,3,4,5], key=lambda c: attack[by_course[c].lane])

            for c in (2,3,4,5,6):
                r = by_course[c]
                second_pos = 1.20 if c in (2,3) else (1.02 if c == 4 else 0.88)
                second[r.lane] = attack[r.lane] * second_pos

                distance = c - primary_course
                link = 1.00
                if distance == 1:
                    link = 1.34
                elif distance == 2:
                    link = 1.20
                elif distance >= 3:
                    link = 1.08
                elif distance < 0:
                    link = 1.02

                motor_pickup = 0.82 + 0.36*self._scale(r.motor_3, 30, 70)
                slit = 1.0 if r.exhibition_st is None else 0.92 + 0.18*self._scale(0.35-r.exhibition_st, 0, 0.35)
                third[r.lane] = remain[r.lane] * link * motor_pickup * slit

            third[by_course[primary_course].lane] *= 1.12
        else:
            first[attacker.lane] = 0.56
            first[inner.lane] = 0.18

            for r in racers:
                c = r.actual_course
                if r.lane == attacker.lane:
                    second[r.lane] = attack[r.lane]*0.34
                    third[r.lane] = remain[r.lane]*0.56
                    continue

                sec = remain[r.lane]
                thi = remain[r.lane]

                if c == 1:
                    sec *= 1.24
                    thi *= 1.10
                elif c < attack_course:
                    sec *= 1.12
                    thi *= 1.08

                distance = c - attack_course
                if distance == 1:
                    sec *= 1.22
                    thi *= 1.34
                elif distance == 2:
                    sec *= 1.10
                    thi *= 1.22
                elif distance >= 3:
                    sec *= 0.96
                    thi *= 1.10

                thi *= 0.84 + 0.34*self._scale(r.motor_3, 30, 70)
                if r.exhibition_st is not None:
                    thi *= 0.92 + 0.18*self._scale(0.35-r.exhibition_st, 0, 0.35)

                second[r.lane] = sec
                third[r.lane] = thi

        return Scenario(
            name=name,
            weight=weight,
            attack_course=attack_course,
            first=self._normalize(first),
            second=self._normalize(second),
            third=self._normalize(third),
            notes=["hierarchical outer linkage v0.3"],
        )

    def _mixed(self, name, weight, base, remain):
        return Scenario(name, weight, 0, self._normalize(base), self._normalize(remain), self._normalize(remain), ["residual"])

    def _aggregate_trifectas(self, scenarios):
        tri = {}
        for s in scenarios:
            for a,b,c in permutations(range(1,7),3):
                p = s.weight*s.first.get(a,0)*s.second.get(b,0)*s.third.get(c,0)
                if p > 0:
                    key = f"{a}-{b}-{c}"
                    tri[key] = tri.get(key,0)+p
        total = sum(tri.values())
        if total <= 0:
            raise ValueError("No trifecta probability mass.")
        return dict(sorted(((k,v/total) for k,v in tri.items()), key=lambda x:x[1], reverse=True))

    def _calibrate_first_marginal(self, first, racers, race, attack):
        by_course = {r.actual_course: r for r in racers}
        inner = by_course[1]
        inner_lane = inner.lane

        cap = {"A1": 0.58, "A2": 0.54, "B1": 0.50, "B2": 0.44}.get(inner.class_rank, 0.50)
        if race.wind_speed >= 5 or race.wave_height >= 5:
            cap -= 0.06
        elif race.wind_speed >= 4 or race.wave_height >= 4:
            cap -= 0.04

        outer_best = max(attack[r.lane] for r in racers if r.actual_course in (3,4,5))
        gap = outer_best - attack[inner_lane]
        if gap >= 0.30:
            cap -= 0.06
        elif gap >= 0.18:
            cap -= 0.04
        elif gap >= 0.10:
            cap -= 0.02

        lanes_by_course = [r.lane for r in sorted(racers, key=lambda x: x.actual_course)]
        if lanes_by_course != [1,2,3,4,5,6]:
            cap -= 0.08 if inner_lane != 1 else 0.02

        cap = self._clip(cap, 0.34, 0.60)
        if first[inner_lane] <= cap:
            return first

        excess = first[inner_lane] - cap
        calibrated = dict(first)
        calibrated[inner_lane] = cap
        others = [lane for lane in calibrated if lane != inner_lane]
        total_other = sum(calibrated[lane] for lane in others)
        for lane in others:
            calibrated[lane] += excess * (calibrated[lane] / total_other)
        return self._normalize(calibrated)

    def _marginals(self, tri):
        first = {i:0.0 for i in range(1,7)}
        second = {i:0.0 for i in range(1,7)}
        third = {i:0.0 for i in range(1,7)}
        for key,p in tri.items():
            a,b,c = map(int,key.split("-"))
            first[a]+=p; second[b]+=p; third[c]+=p
        return first, second, third

    def _select_tickets(self, tri, first, ticket_count):
        ranked = list(tri)
        heads = sorted(first, key=first.get, reverse=True)
        allocations = {heads[0]:4, heads[1]:3, heads[2]:1}
        selected = []
        for head,n in allocations.items():
            selected.extend([k for k in ranked if int(k.split("-")[0])==head][:n])
        for k in ranked:
            if k not in selected:
                selected.append(k)
            if len(selected) >= ticket_count:
                break
        return selected[:ticket_count]

    def _audit(self, tickets, tri, first, diagnostics):
        if len(set(tickets)) != len(tickets):
            diagnostics.append("Duplicate ticket detected.")
        missing = [k for k in list(tri)[:10] if k not in tickets]
        if len(missing) >= 5:
            diagnostics.append("Ticket list diverges from top trifecta probabilities.")
        for head in sorted(first, key=first.get, reverse=True)[:2]:
            if not any(int(t.split("-")[0]) == head for t in tickets):
                diagnostics.append(f"Top head {head} has no ticket.")

    def _sab(self, first, scenarios, entry_changed):
        vals = sorted(first.values(), reverse=True)
        if entry_changed:
            return "B"
        if vals[0]>=0.52 and vals[0]-vals[1]>=0.22:
            return "S"
        if vals[0]>=0.38 and vals[0]-vals[1]>=0.12:
            return "A"
        return "B"

    @staticmethod
    def _course_prior(c):
        return {1:0.82,2:0.52,3:0.45,4:0.40,5:0.28,6:0.15}.get(c,0.2)

    @staticmethod
    def _scale(v,lo,hi):
        return max(0.0,min(1.0,(v-lo)/(hi-lo))) if hi>lo else 0.5

    @staticmethod
    def _clip(v,lo,hi):
        return max(lo,min(hi,v))

    @staticmethod
    def _normalize(d):
        clean = {k:max(0.0,v) for k,v in d.items()}
        total = sum(clean.values())
        return {k:v/total for k,v in clean.items()} if total else {k:1/len(clean) for k in clean}

    @staticmethod
    def _rank_value(value, racers, attr):
        if value is None:
            return 0.5
        vals = [getattr(r,attr) for r in racers if getattr(r,attr) is not None]
        if len(vals)<2:
            return 0.5
        ordered = sorted(vals)
        return 1.0 - ordered.index(value)/max(1,len(ordered)-1)
