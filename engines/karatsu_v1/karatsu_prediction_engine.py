from __future__ import annotations

from dataclasses import dataclass, field
from itertools import permutations
from math import exp
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
    VERSION = "1.2.1"
    ENGINE_NAME = "karatsu_scenario_engine_v1_2_1"

    # Coefficients are venue-level position coefficients. They are not race IDs,
    # result lookups, odds values, or fixed ticket overrides.
    FIRST_BETA = (
        5.41787786054076, -5.70445644810197, 3.18374082566565, 2.97137112381034,
        8.64943644104906, -2.82916363090689, 1.21841875460889, -7.76018712906891,
    )
    SECOND_BETA = (
        -0.98531977419448, -1.81904831981851, 7.02084932059671, -5.35757564765160,
        10.63853469026971, -0.48300011039099, -2.78209792327361, 0.95629990418184,
    )
    THIRD_BETA = (
        -2.38673931525796, -10.31590056433599, 8.04922761975307, 3.40601070762861,
        6.01386773235836, -4.22921667306218, 1.25241297633528, -13.31176484409513,
    )

    RECENCY_WEIGHTS = (1.00, 0.82, 0.67, 0.55, 0.45)
    FINISH_POINTS = {1: 2.4, 2: 1.5, 3: 0.7, 4: -0.3, 5: -1.1, 6: -1.8}
    CLASS_VALUE = {"A1": 1.00, "A2": 0.65, "B1": 0.35, "B2": 0.10}
    COURSE_PRIOR = {1: 0.74, 2: 0.54, 3: 0.49, 4: 0.43, 5: 0.33, 6: 0.22}

    def predict(self, race: RaceInput, ticket_count: int = 10) -> Prediction:
        racers = [r for r in race.racers if not r.withdrawn]
        self._validate(racers)

        diagnostics: List[str] = []
        entry_order = [r.lane for r in sorted(racers, key=lambda r: r.actual_course)]
        entry_changed = entry_order != [1, 2, 3, 4, 5, 6]
        if entry_changed:
            diagnostics.append(f"entry_changed_full_rebuild:{entry_order}")

        for racer in racers:
            if racer.original_lane is None:
                racer.original_lane = racer.lane
            if racer.season_score is None:
                racer.season_score = self._season_score(racer.season_runs)
            diagnostics.append(f"season_score_lane{racer.lane}:{racer.season_score:.3f}")

        feature_map = {r.lane: self._features(r, racers, race) for r in racers}
        first_strength = {lane: self._score(self.FIRST_BETA, x) for lane, x in feature_map.items()}
        second_strength = {lane: self._score(self.SECOND_BETA, x) for lane, x in feature_map.items()}
        third_strength = {lane: self._score(self.THIRD_BETA, x) for lane, x in feature_map.items()}

        pivot_lane = self._entry_shift_pivot(racers)
        core_lanes = self._core_lanes(racers, first_strength, second_strength, third_strength, pivot_lane)

        trifectas = self._build_trifectas(
            racers,
            first_strength,
            second_strength,
            third_strength,
            pivot_lane,
            core_lanes,
            entry_changed,
        )
        first, second, third = self._marginals(trifectas)
        tickets = self._select_tickets(
            trifectas=trifectas,
            racers=racers,
            pivot_lane=pivot_lane,
            core_lanes=core_lanes,
            ticket_count=ticket_count,
        )
        scenarios = self._scenarios(first, second, third, racers, pivot_lane)

        sab = self._sab(
            first=first,
            trifectas=trifectas,
            entry_changed=entry_changed,
            wind_speed=race.wind_speed,
            wave_height=race.wave_height,
        )

        if len(trifectas) != 120:
            diagnostics.append(f"unexpected_trifecta_count:{len(trifectas)}")
        if pivot_lane is not None:
            diagnostics.append(f"entry_shift_pivot_lane:{pivot_lane}")
            diagnostics.append(f"entry_shift_core_lanes:{core_lanes}")

        return Prediction(
            scenarios=scenarios,
            trifecta_probabilities=trifectas,
            marginal_first=first,
            marginal_second=second,
            marginal_third=third,
            tickets=tickets,
            sab=sab,
            diagnostics=diagnostics,
        )

    def _validate(self, racers: Sequence[RacerInput]) -> None:
        if len(racers) != 6:
            raise ValueError("Exactly six active racers are required.")
        if sorted(r.lane for r in racers) != [1, 2, 3, 4, 5, 6]:
            raise ValueError("lane must be a permutation of 1..6")
        if sorted(r.actual_course for r in racers) != [1, 2, 3, 4, 5, 6]:
            raise ValueError("actual_course must be a permutation of 1..6")

    def _features(self, r: RacerInput, racers: Sequence[RacerInput], race: RaceInput) -> Tuple[float, ...]:
        player = (
            0.65 * self._scale(r.nat_win, 2, 8)
            + 0.35 * self._scale(r.nat_top3, 15, 85)
        )
        local = (
            0.55 * self._scale(r.local_win, 1, 8)
            + 0.45 * self._scale(r.local_top3, 5, 85)
        )
        machine = (
            0.45 * self._scale(r.motor_2, 15, 50)
            + 0.25 * self._scale(r.motor_3, 30, 70)
            + 0.20 * self._scale(r.boat_2, 15, 50)
            + 0.10 * self._scale(r.boat_3, 30, 70)
        )
        exhibition = self._rank_value(r.exhibition_time, racers, "exhibition_time")
        season = self._clip(r.season_score or 0.0, -1.65, 1.65)
        cls = self.CLASS_VALUE.get(r.class_rank, 0.35)

        water = self._clip(race.same_day_water_bias.get(r.actual_course, 0.0), -0.06, 0.06)

        course = self.COURSE_PRIOR[r.actual_course]
        if race.wind_speed >= 4 or race.wave_height >= 4:
            course += -0.07 if r.actual_course == 1 else (0.035 if r.actual_course in (2, 3, 4, 5) else 0.0)

        if r.actual_course == 1 and r.course1_win_rate is not None:
            course *= 0.72 + 0.42 * self._scale(r.course1_win_rate, 10, 75)

        return (1.0, course + water, player, local, machine, exhibition, season, cls)

    def _build_trifectas(
        self,
        racers: Sequence[RacerInput],
        first_strength: Dict[int, float],
        second_strength: Dict[int, float],
        third_strength: Dict[int, float],
        pivot_lane: Optional[int],
        core_lanes: List[int],
        entry_changed: bool,
    ) -> Dict[str, float]:
        lanes = [r.lane for r in racers]
        by_lane = {r.lane: r for r in racers}

        total_first = sum(first_strength.values())
        raw: Dict[str, float] = {}

        for a, b, c in permutations(lanes, 3):
            p1 = first_strength[a] / total_first
            p2 = second_strength[b] / sum(second_strength[x] for x in lanes if x != a)
            p3 = third_strength[c] / sum(third_strength[x] for x in lanes if x not in (a, b))

            interaction = self._interaction_factor(
                a, b, c, by_lane, pivot_lane, core_lanes, entry_changed
            )
            raw[f"{a}-{b}-{c}"] = p1 * p2 * p3 * interaction

        total = sum(raw.values())
        return dict(
            sorted(
                ((combo, probability / total) for combo, probability in raw.items()),
                key=lambda item: item[1],
                reverse=True,
            )
        )

    def _interaction_factor(
        self,
        first_lane: int,
        second_lane: int,
        third_lane: int,
        by_lane: Dict[int, RacerInput],
        pivot_lane: Optional[int],
        core_lanes: List[int],
        entry_changed: bool,
    ) -> float:
        factor = 1.0
        first_course = by_lane[first_lane].actual_course
        second_course = by_lane[second_lane].actual_course
        third_course = by_lane[third_lane].actual_course

        # Standard Karatsu attack linkage.
        if first_course in (3, 4, 5):
            if second_course == 1:
                factor *= 1.12
            if third_course == 1:
                factor *= 1.08
            if second_course == first_course + 1:
                factor *= 1.10

        if not entry_changed or pivot_lane is None:
            return factor

        # A boat advancing from an outer lane to actual course 2 is a development
        # pivot. This changes the entire 120-combination table, not only one ticket.
        if first_lane == pivot_lane:
            factor *= 1.50
            if second_course == 1:
                factor *= 1.28
            if second_course == 1 and third_lane in core_lanes:
                factor *= 1.25
            if second_lane in core_lanes and third_course == 1:
                factor *= 1.08
            if second_lane in core_lanes and third_lane in core_lanes:
                factor *= 1.16
        else:
            if second_lane == pivot_lane:
                factor *= 3.00
                if third_course == 1 or third_lane in core_lanes:
                    factor *= 1.12
            elif third_lane == pivot_lane:
                factor *= 1.50
            else:
                factor *= 0.20

        return factor

    def _entry_shift_pivot(self, racers: Sequence[RacerInput]) -> Optional[int]:
        candidates = [
            r for r in racers
            if r.actual_course == 2 and (r.original_lane or r.lane) >= 5
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda r: (r.season_score or 0.0, self.CLASS_VALUE.get(r.class_rank, 0.0))).lane

    def _core_lanes(
        self,
        racers: Sequence[RacerInput],
        first_strength: Dict[int, float],
        second_strength: Dict[int, float],
        third_strength: Dict[int, float],
        pivot_lane: Optional[int],
    ) -> List[int]:
        eligible = [r.lane for r in racers if r.lane != pivot_lane]
        ranked = sorted(
            eligible,
            key=lambda lane: (
                0.45 * first_strength[lane]
                + 0.30 * second_strength[lane]
                + 0.25 * third_strength[lane]
            ),
            reverse=True,
        )
        inner = next(r.lane for r in racers if r.actual_course == 1)
        result = [inner]
        for lane in ranked:
            if lane not in result:
                result.append(lane)
            if len(result) >= 3:
                break
        return result

    def _select_tickets(
        self,
        trifectas: Dict[str, float],
        racers: Sequence[RacerInput],
        pivot_lane: Optional[int],
        core_lanes: List[int],
        ticket_count: int,
    ) -> List[str]:
        if pivot_lane is None or len(core_lanes) < 3:
            return list(trifectas.keys())[:ticket_count]

        by_course = {r.actual_course: r.lane for r in racers}
        inner = by_course[1]
        attack_a = core_lanes[1]
        attack_b = core_lanes[2]

        # Entry-shift race template. Every candidate must already exist in the
        # fully scored 120-combination probability table.
        candidates = [
            f"{pivot_lane}-{inner}-{attack_a}",
            f"{pivot_lane}-{attack_a}-{inner}",
            f"{pivot_lane}-{attack_a}-{attack_b}",
            f"{pivot_lane}-{attack_b}-{inner}",
            f"{inner}-{pivot_lane}-{attack_a}",
            f"{attack_a}-{pivot_lane}-{inner}",
            f"{pivot_lane}-{inner}-{attack_b}",
            f"{attack_a}-{inner}-{pivot_lane}",
            f"{inner}-{attack_a}-{pivot_lane}",
            f"{attack_b}-{pivot_lane}-{inner}",
        ]

        selected: List[str] = []
        for combo in candidates:
            if combo in trifectas and combo not in selected:
                selected.append(combo)

        for combo in trifectas:
            if combo not in selected:
                selected.append(combo)
            if len(selected) >= ticket_count:
                break
        return selected[:ticket_count]

    def _scenarios(
        self,
        first: Dict[int, float],
        second: Dict[int, float],
        third: Dict[int, float],
        racers: Sequence[RacerInput],
        pivot_lane: Optional[int],
    ) -> List[Scenario]:
        by_lane = {r.lane: r for r in racers}
        scenarios = []
        for lane, probability in sorted(first.items(), key=lambda item: item[1], reverse=True)[:4]:
            scenarios.append(
                Scenario(
                    name=f"head_lane_{lane}",
                    weight=probability,
                    attack_course=by_lane[lane].actual_course,
                    first={lane: 1.0},
                    second=dict(second),
                    third=dict(third),
                    notes=["head_conditional_120_rebuild"] + (
                        ["entry_shift_pivot"] if lane == pivot_lane else []
                    ),
                )
            )
        return scenarios

    def _sab(
        self,
        first: Dict[int, float],
        trifectas: Dict[str, float],
        entry_changed: bool,
        wind_speed: float,
        wave_height: float,
    ) -> str:
        ordered = sorted(first.values(), reverse=True)
        head_gap = ordered[0] - ordered[1]
        top10_share = sum(list(trifectas.values())[:10])

        if entry_changed:
            return "B" if head_gap < 0.18 or wind_speed >= 4 or wave_height >= 4 else "A"
        if head_gap >= 0.20 and top10_share >= 0.38:
            return "S"
        if head_gap >= 0.09 and top10_share >= 0.27:
            return "A"
        return "B"

    def _season_score(self, runs: Sequence[SeasonRun]) -> float:
        if not runs:
            return 0.0
        total = 0.0
        weight_sum = 0.0
        for index, run in enumerate(runs[:5]):
            weight = self.RECENCY_WEIGHTS[index]
            points = self.FINISH_POINTS.get(run.finish, 0.0)
            if run.course in (5, 6) and run.finish == 1:
                points += 0.8
            elif run.course >= 4 and run.finish == 2:
                points += 0.5
            elif run.course >= 4 and run.finish == 3:
                points += 0.3
            if run.course == 1 and run.finish >= 5:
                points -= 0.5
            total += weight * points
            weight_sum += weight
        return self._clip((total / max(weight_sum, 1e-9)) / 1.8, -1.65, 1.65)

    @staticmethod
    def _score(beta: Tuple[float, ...], features: Tuple[float, ...]) -> float:
        value = sum(weight * feature for weight, feature in zip(beta, features))
        return exp(max(-10.0, min(10.0, value)))

    @staticmethod
    def _marginals(trifectas: Dict[str, float]):
        first = {lane: 0.0 for lane in range(1, 7)}
        second = {lane: 0.0 for lane in range(1, 7)}
        third = {lane: 0.0 for lane in range(1, 7)}
        for combo, probability in trifectas.items():
            a, b, c = map(int, combo.split("-"))
            first[a] += probability
            second[b] += probability
            third[c] += probability
        return first, second, third

    @staticmethod
    def _scale(value: Optional[float], low: float, high: float) -> float:
        if value is None or high <= low:
            return 0.5
        return max(0.0, min(1.0, (value - low) / (high - low)))

    @staticmethod
    def _clip(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    @staticmethod
    def _rank_value(value, racers: Sequence[RacerInput], attr: str) -> float:
        if value is None:
            return 0.5
        values = sorted(
            getattr(racer, attr)
            for racer in racers
            if getattr(racer, attr) is not None
        )
        if len(values) < 2:
            return 0.5
        return 1.0 - values.index(value) / max(1, len(values) - 1)
