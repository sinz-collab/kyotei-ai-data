from __future__ import annotations

from dataclasses import dataclass, field
from itertools import permutations
from typing import Dict, List, Optional, Sequence, Tuple


@dataclass
class SeasonRun:
    finish: int
    course: int
    st: Optional[float] = None


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
    season_score: Optional[float] = None
    season_runs: List[SeasonRun] = field(default_factory=list)
    tilt: float = 0.0
    withdrawn: bool = False


@dataclass
class RaceInput:
    racers: List[RacerInput]
    wind_speed: float = 0.0
    wave_height: float = 0.0
    tide_phase: str = "unknown"
    day_no: int = 1
    same_day_water_bias: Dict[int, float] = field(default_factory=dict)


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
    VERSION = "1.2.0"
    ENGINE_NAME = "karatsu_scenario_engine_v1_2"

    CLASS_BASE = {"A1": 0.58, "A2": 0.52, "B1": 0.47, "B2": 0.42}
    RECENCY_WEIGHTS = (1.00, 0.82, 0.67, 0.55, 0.45)
    FINISH_POINTS = {1: 2.4, 2: 1.5, 3: 0.7, 4: -0.3, 5: -1.1, 6: -1.8}

    def predict(self, race: RaceInput, ticket_count: int = 10) -> Prediction:
        racers = [r for r in race.racers if not r.withdrawn]
        if len(racers) != 6:
            raise ValueError("Exactly six active racers are required.")

        self._validate_courses(racers)
        diagnostics: List[str] = []

        lanes_by_course = [r.lane for r in sorted(racers, key=lambda x: x.actual_course)]
        entry_changed = lanes_by_course != [1, 2, 3, 4, 5, 6]
        if entry_changed:
            diagnostics.append(f"entry_changed_full_rebuild:{lanes_by_course}")

        for r in racers:
            if r.season_score is None:
                r.season_score = self._season_score(r.season_runs)
            diagnostics.append(f"season_score_lane{r.lane}:{r.season_score:.3f}")

        base = {r.lane: self._base_strength(r, racers, race) for r in racers}
        attack = {r.lane: self._attack_strength(r, racers, race, base[r.lane]) for r in racers}
        second = {r.lane: self._second_strength(r, racers, race, base[r.lane]) for r in racers}
        remain = {r.lane: self._remain_strength(r, racers, race, base[r.lane]) for r in racers}

        scenarios = self._build_scenarios(racers, race, base, attack, second, remain)
        trifectas = self._aggregate_trifectas(scenarios)
        first, second_m, third = self._marginals(trifectas)

        tickets = self._select_tickets(trifectas, scenarios, ticket_count)
        sab = self._sab(
            first=first,
            second=second_m,
            third=third,
            scenarios=scenarios,
            tickets=tickets,
            trifectas=trifectas,
            entry_changed=entry_changed,
            race=race,
        )

        self._audit(tickets, trifectas, diagnostics)
        return Prediction(
            scenarios=scenarios,
            trifecta_probabilities=trifectas,
            marginal_first=first,
            marginal_second=second_m,
            marginal_third=third,
            tickets=tickets,
            sab=sab,
            diagnostics=diagnostics,
        )

    def _validate_courses(self, racers: Sequence[RacerInput]) -> None:
        courses = sorted(r.actual_course for r in racers)
        lanes = sorted(r.lane for r in racers)
        if courses != [1, 2, 3, 4, 5, 6]:
            raise ValueError(f"actual_course must be a permutation of 1..6: {courses}")
        if lanes != [1, 2, 3, 4, 5, 6]:
            raise ValueError(f"lane must be a permutation of 1..6: {lanes}")

    def _season_score(self, runs: Sequence[SeasonRun]) -> float:
        if not runs:
            return 0.0

        score = 0.0
        weight_sum = 0.0
        for idx, run in enumerate(runs[:5]):
            w = self.RECENCY_WEIGHTS[min(idx, len(self.RECENCY_WEIGHTS) - 1)]
            pts = self.FINISH_POINTS.get(run.finish, 0.0)

            if run.course in (5, 6) and run.finish == 1:
                pts += 0.8
            elif run.course >= 4 and run.finish == 2:
                pts += 0.5
            elif run.course >= 4 and run.finish == 3:
                pts += 0.3

            if run.course == 1 and run.finish >= 5:
                pts -= 0.5
            elif run.course == 1 and run.finish == 4:
                pts -= 0.2

            score += w * pts
            weight_sum += w

        normalized = score / max(weight_sum, 1e-9)
        return self._clip(normalized / 1.8, -1.5, 1.5)

    def _day_weights(self, day_no: int) -> Dict[str, float]:
        if day_no <= 2:
            return {"player": .29, "machine": .24, "live": .20, "season": .10, "course": .17}
        if day_no >= 5:
            return {"player": .21, "machine": .14, "live": .13, "season": .35, "course": .17}
        return {"player": .24, "machine": .19, "live": .17, "season": .23, "course": .17}

    def _state(self, r: RacerInput, racers: Sequence[RacerInput], race: RaceInput) -> float:
        machine = (
            .55 * self._scale(r.motor_3, 30, 70)
            + .25 * self._scale(r.motor_2, 15, 50)
            + .20 * self._scale(r.boat_3, 30, 70)
        )
        live = (
            .45 * self._rank_value(r.exhibition_time, racers, "exhibition_time")
            + .25 * self._rank_value(r.lap_time, racers, "lap_time")
            + .20 * self._rank_value(r.turn_time, racers, "turn_time")
            + .10 * self._rank_value(r.straight_time, racers, "straight_time")
        )
        season = self._clip(((r.season_score or 0.0) + 1.5) / 3.0, 0, 1)

        if race.day_no <= 2:
            return .55 * machine + .45 * live
        if race.day_no >= 5:
            return .70 * season + .20 * live + .10 * machine
        return .35 * machine + .30 * live + .35 * season

    def _class_position_multiplier(
        self,
        r: RacerInput,
        racers: Sequence[RacerInput],
        race: RaceInput,
    ) -> Tuple[float, float, float]:
        state = self._state(r, racers, race)
        if r.class_rank == "A1":
            return (1 + .03 * state, 1 + .08 * state, 1 + .08 * state)
        if r.class_rank == "A2":
            return (1 + .02 * state, 1 + .05 * state, 1 + .05 * state)
        if r.class_rank == "B2":
            return (.94 + .05 * state, .94 + .08 * state, .96 + .10 * state)
        return (1.0, 1.0, 1.0)

    def _base_strength(self, r: RacerInput, racers: Sequence[RacerInput], race: RaceInput) -> float:
        w = self._day_weights(race.day_no)
        player = .65 * self._scale(r.nat_win, 2, 8) + .35 * self._scale(r.nat_top3, 15, 85)
        local = .55 * self._scale(r.local_win, 1, 8) + .45 * self._scale(r.local_top3, 5, 85)
        machine = (
            .45 * self._scale(r.motor_2, 15, 50)
            + .25 * self._scale(r.motor_3, 30, 70)
            + .20 * self._scale(r.boat_2, 15, 50)
            + .10 * self._scale(r.boat_3, 30, 70)
        )
        live = (
            .62 * self._rank_value(r.exhibition_time, racers, "exhibition_time")
            + .38 * self._rank_value(r.lap_time, racers, "lap_time")
        )
        season = self._clip(((r.season_score or 0.0) + 1.5) / 3.0, 0, 1)
        cls = self.CLASS_BASE.get(r.class_rank, .47)
        player_mix = .78 * (.72 * player + .28 * local) + .22 * cls

        water = self._clip(race.same_day_water_bias.get(r.actual_course, 0.0), -.06, .06)

        return max(
            .01,
            w["player"] * player_mix
            + w["machine"] * machine
            + w["live"] * live
            + w["season"] * season
            + w["course"] * self._course_prior(r.actual_course)
            + water,
        )

    def _attack_strength(self, r, racers, race, base):
        score = (
            base
            + .11 * self._rank_value(r.exhibition_time, racers, "exhibition_time")
            + .07 * self._rank_value(r.straight_time, racers, "straight_time")
            + .07 * self._rank_value(r.turn_time, racers, "turn_time")
            + .05 * self._rank_value(r.lap_time, racers, "lap_time")
        )

        # Exhibition ST is context only. It never creates a large standalone jump.
        if r.exhibition_st is not None:
            score += .012 * self._rank_value(r.exhibition_st, racers, "exhibition_st")

        score += {1: .06, 2: .04, 3: .08, 4: .13, 5: .06}.get(r.actual_course, 0)

        if race.wind_speed >= 4 or race.wave_height >= 4:
            score += -.07 if r.actual_course == 1 else (.04 if r.actual_course in (2, 3, 4, 5) else 0)

        if r.tilt >= 2:
            score += .08 if r.actual_course >= 4 else .02

        f, _, _ = self._class_position_multiplier(r, racers, race)
        return max(.01, score * f)

    def _second_strength(self, r, racers, race, base):
        state = self._state(r, racers, race)
        score = base + .05 * self._rank_value(r.exhibition_time, racers, "exhibition_time")
        score += .10 * self._scale(r.motor_3, 30, 70) + .05 * self._scale(r.boat_3, 30, 70)
        score += .05 if r.actual_course in (2, 3) else (.025 if r.actual_course in (4, 5, 6) else 0)
        _, f2, _ = self._class_position_multiplier(r, racers, race)
        return max(.01, score * f2 * (.96 + .08 * state))

    def _remain_strength(self, r, racers, race, base):
        score = (
            base
            + .10 * self._rank_value(r.lap_time, racers, "lap_time")
            + .08 * self._rank_value(r.turn_time, racers, "turn_time")
            + .04 * self._rank_value(r.straight_time, racers, "straight_time")
        )
        score += .10 if r.actual_course <= 3 else 0
        score += .08 * self._scale(r.motor_3, 30, 70) + .04 * self._scale(r.boat_3, 30, 70)
        _, _, f3 = self._class_position_multiplier(r, racers, race)
        return max(.01, score * f3)

    def _inner_course_multiplier(self, r: RacerInput, entry_changed: bool) -> float:
        lane_match = 1.0 if r.lane == 1 else .74
        entry_factor = 1.0 if (not entry_changed or r.lane == 1) else .82
        depth = max(.72, 1 - .28 * self._clip(r.entry_depth_risk, 0, 1))

        suitability = 1.0
        if r.course1_win_rate is not None:
            suitability *= .78 + .44 * self._scale(r.course1_win_rate, 10, 75)
        elif r.course1_rate is not None:
            suitability *= .82 + .36 * self._scale(r.course1_rate, 15, 75)

        return self._clip(lane_match * entry_factor * depth * suitability, .42, 1.05)

    def _build_scenarios(self, racers, race, base, attack, second, remain):
        by = {r.actual_course: r for r in racers}
        inner = by[1]
        changed = [r.lane for r in sorted(racers, key=lambda x: x.actual_course)] != [1, 2, 3, 4, 5, 6]

        raw = .20 + .19 * base[inner.lane] + .10 * attack[inner.lane]
        if race.wind_speed >= 5 or race.wave_height >= 5:
            raw -= .10
        elif race.wind_speed >= 4 or race.wave_height >= 4:
            raw -= .07

        gap = max(attack[by[c].lane] for c in (2, 3, 4, 5, 6)) - attack[inner.lane]
        raw -= .08 if gap >= .30 else (.05 if gap >= .18 else (.03 if gap >= .10 else 0))

        hold = self._clip(raw * self._inner_course_multiplier(inner, changed), .06, .48)

        ranked = sorted((2, 3, 4, 5, 6), key=lambda c: attack[by[c].lane], reverse=True)
        primary, secondary = ranked[:2]

        scenarios = [
            self._scenario("inner_hold", hold, 1, racers, attack, second, remain, True),
            self._scenario(
                f"primary_{primary}_attack",
                (1 - hold) * .38,
                primary,
                racers,
                attack,
                second,
                remain,
                False,
            ),
            self._scenario(
                f"secondary_{secondary}_attack",
                (1 - hold) * .23,
                secondary,
                racers,
                attack,
                second,
                remain,
                False,
            ),
            self._attacker_out(
                "attacker_out_link",
                (1 - hold) * .14,
                primary,
                racers,
                attack,
                second,
                remain,
            ),
            self._mixed("residual", (1 - hold) * .25, base, second, remain),
        ]

        total = sum(s.weight for s in scenarios)
        for s in scenarios:
            s.weight /= total
        return scenarios

    def _scenario(
        self,
        name,
        weight,
        attack_course,
        racers,
        attack,
        second_strength,
        remain,
        inner_holds,
    ):
        by = {r.actual_course: r for r in racers}
        attacker = by[attack_course]
        inner = by[1]
        first: Dict[int, float] = {}
        second: Dict[int, float] = {}
        third: Dict[int, float] = {}

        if inner_holds:
            first[inner.lane] = .78
            primary = max((2, 3, 4, 5, 6), key=lambda c: attack[by[c].lane])
            for c in (2, 3, 4, 5, 6):
                r = by[c]
                second[r.lane] = second_strength[r.lane] * (
                    1.20 if c in (2, 3) else (1.02 if c == 4 else .92)
                )
                d = c - primary
                link = 1.34 if d == 1 else (1.20 if d == 2 else (1.08 if d >= 3 else 1.02))
                third[r.lane] = remain[r.lane] * link * (
                    .82 + .36 * self._scale(r.motor_3, 30, 70)
                )
        else:
            first[attacker.lane] = .56
            first[inner.lane] = .18

            for r in racers:
                c = r.actual_course
                if r.lane == attacker.lane:
                    second[r.lane] = attack[r.lane] * .34
                    third[r.lane] = remain[r.lane] * .56
                    continue

                sec = second_strength[r.lane]
                thi = remain[r.lane]

                if c == 1:
                    sec *= 1.24
                    thi *= 1.10
                elif c < attack_course:
                    sec *= 1.12
                    thi *= 1.08

                d = c - attack_course
                if d == 1:
                    sec *= 1.22
                    thi *= 1.34
                elif d == 2:
                    sec *= 1.10
                    thi *= 1.22
                elif d >= 3:
                    sec *= .96
                    thi *= 1.10

                second[r.lane] = sec
                third[r.lane] = thi * (.84 + .34 * self._scale(r.motor_3, 30, 70))

        return Scenario(
            name=name,
            weight=weight,
            attack_course=attack_course,
            first=self._normalize(first),
            second=self._normalize(second),
            third=self._normalize(third),
            notes=["head_conditional_second_third", "outer_linkage"],
        )

    def _attacker_out(self, name, weight, attack_course, racers, attack, second_strength, remain):
        by = {r.actual_course: r for r in racers}
        attacker = by[attack_course]
        inner = by[1]
        first = {inner.lane: .62}
        second: Dict[int, float] = {}
        third: Dict[int, float] = {}

        for r in racers:
            if r.lane == attacker.lane:
                third[r.lane] = remain[r.lane] * .25
                continue

            d = r.actual_course - attack_course
            second[r.lane] = second_strength[r.lane] * (
                1.55 if d == -1 else (
                    1.28 if d == -2 else (
                        1.18 if d == 1 else (
                            1.05 if r.actual_course < attack_course else .82
                        )
                    )
                )
            )
            third[r.lane] = remain[r.lane] * (
                1.38 if d == -2 else (
                    1.22 if d == -1 else (
                        1.16 if d == 1 else 1.0
                    )
                )
            )

        return Scenario(
            name=name,
            weight=weight,
            attack_course=attack_course,
            first=self._normalize(first),
            second=self._normalize(second),
            third=self._normalize(third),
            notes=["attacker_finishes_out", "inside_revival", "second_outer_link"],
        )

    def _mixed(self, name, weight, base, second, remain):
        return Scenario(
            name=name,
            weight=weight,
            attack_course=0,
            first=self._normalize(base),
            second=self._normalize(second),
            third=self._normalize(remain),
            notes=["residual"],
        )

    def _aggregate_trifectas(self, scenarios: Sequence[Scenario]) -> Dict[str, float]:
        tri: Dict[str, float] = {}
        for s in scenarios:
            for a, b, c in permutations(range(1, 7), 3):
                p = s.weight * s.first.get(a, 0) * s.second.get(b, 0) * s.third.get(c, 0)
                if p > 0:
                    tri[f"{a}-{b}-{c}"] = tri.get(f"{a}-{b}-{c}", 0.0) + p

        total = sum(tri.values())
        if total <= 0:
            raise ValueError("No trifecta probabilities were generated.")

        normalized = ((k, v / total) for k, v in tri.items())
        return dict(sorted(normalized, key=lambda x: x[1], reverse=True))

    def _marginals(self, tri):
        first = {i: 0.0 for i in range(1, 7)}
        second = {i: 0.0 for i in range(1, 7)}
        third = {i: 0.0 for i in range(1, 7)}

        for combo, p in tri.items():
            a, b, c = map(int, combo.split("-"))
            first[a] += p
            second[b] += p
            third[c] += p
        return first, second, third

    def _scenario_top_ticket(self, scenario: Scenario) -> Optional[str]:
        best = None
        best_prob = -1.0
        for a, b, c in permutations(range(1, 7), 3):
            p = (
                scenario.first.get(a, 0)
                * scenario.second.get(b, 0)
                * scenario.third.get(c, 0)
            )
            if p > best_prob:
                best_prob = p
                best = f"{a}-{b}-{c}"
        return best

    def _select_tickets(self, tri, scenarios, ticket_count):
        selected: List[str] = []

        # Preserve at least one ticket from major scenarios, then fill by all-120 score.
        for scenario in sorted(scenarios, key=lambda x: x.weight, reverse=True):
            ticket = self._scenario_top_ticket(scenario)
            if ticket and ticket not in selected:
                selected.append(ticket)

        for ticket in tri:
            if ticket not in selected:
                selected.append(ticket)
            if len(selected) >= ticket_count:
                break

        return selected[:ticket_count]

    def _sab(self, first, second, third, scenarios, tickets, trifectas, entry_changed, race):
        first_vals = sorted(first.values(), reverse=True)
        head_gap = first_vals[0] - first_vals[1]
        top_scenario = max((s.weight for s in scenarios), default=0.0)
        coverage = sum(trifectas.get(k, 0.0) for k in tickets)
        top10_share = sum(list(trifectas.values())[:10])
        scenario_consistency = sum(
            1 for s in sorted(scenarios, key=lambda x: x.weight, reverse=True)[:4]
            if self._scenario_top_ticket(s) in tickets
        )

        # Entry changes always reduce reproducibility. Large changes with multiple head candidates are B.
        if entry_changed:
            if head_gap < .16 or race.wind_speed >= 4 or race.wave_height >= 4:
                return "B"
            return "A"

        # S requires a clearly concentrated head and combinations, not only a high lane-1 prior.
        if (
            head_gap >= .20
            and top_scenario >= .34
            and coverage >= .20
            and top10_share >= .38
            and scenario_consistency >= 3
        ):
            return "S"

        if (
            head_gap >= .09
            and top_scenario >= .23
            and coverage >= .14
            and top10_share >= .28
            and scenario_consistency >= 3
        ):
            return "A"

        return "B"

    def _audit(self, tickets, tri, diagnostics):
        if len(set(tickets)) != len(tickets):
            diagnostics.append("duplicate_ticket")
        if not all(k in tri for k in tickets):
            diagnostics.append("ticket_not_in_probability_table")
        if len(tri) != 120:
            diagnostics.append(f"unexpected_trifecta_count:{len(tri)}")

    @staticmethod
    def _course_prior(course: int) -> float:
        return {1: .74, 2: .54, 3: .49, 4: .43, 5: .33, 6: .22}.get(course, .20)

    @staticmethod
    def _scale(value, low, high):
        if value is None or high <= low:
            return .5
        return max(0.0, min(1.0, (value - low) / (high - low)))

    @staticmethod
    def _clip(value, low, high):
        return max(low, min(high, value))

    @staticmethod
    def _normalize(values):
        clean = {k: max(0.0, v) for k, v in values.items()}
        total = sum(clean.values())
        if total <= 0:
            if not clean:
                return {}
            return {k: 1.0 / len(clean) for k in clean}
        return {k: v / total for k, v in clean.items()}

    @staticmethod
    def _rank_value(value, racers, attr):
        if value is None:
            return .5

        vals = [getattr(r, attr) for r in racers if getattr(r, attr) is not None]
        if len(vals) < 2:
            return .5

        # Lower is better for all currently supported exhibition metrics.
        ordered = sorted(vals)
        rank = ordered.index(value)
        return 1.0 - rank / max(1, len(ordered) - 1)
