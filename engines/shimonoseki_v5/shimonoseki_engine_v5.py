from __future__ import annotations

import csv
import json
import math
import re
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, MutableMapping

from .shimonoseki_final_reconciler_v5_0 import (
    LocalCourseST,
    build_tickets,
    load_course_and_tide,
    reconcile_final,
)

ENGINE_ID = "shimonoseki_engine_v5.0"
ENGINE_VERSION = "5.0"
LANES = (1, 2, 3, 4, 5, 6)
REL_MULT = {"A": 1.0, "B": 0.8, "C": 0.55, "参考": 0.30}
MOTOR_BETA = {"first": 0.00855, "second": 0.01045, "third": 0.01188}


def _f(v: Any, default: float = 0.0) -> float:
    try:
        if v in (None, "", "-"):
            return default
        s = str(v).strip().replace("%", "")
        if s.startswith("."):
            s = "0" + s
        return float(s)
    except Exception:
        return default


def _i(v: Any, default: int = 0) -> int:
    try:
        return int(float(str(v).strip()))
    except Exception:
        return default


def _norm_name(v: Any) -> str:
    return re.sub(r"[\s\u3000]+", "", str(v or "")).replace("髙", "高").replace("﨑", "崎")


def _normalize(values: Mapping[int, float]) -> dict[int, float]:
    positive = {lane: max(1e-8, float(value)) for lane, value in values.items()}
    total = sum(positive.values()) or 1.0
    return {lane: value / total * 100.0 for lane, value in positive.items()}


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _prob_valid(values: Mapping[str, Any]) -> bool:
    if set(map(str, values)) != {str(x) for x in LANES}:
        return False
    nums = [_f(values[str(x)], -1.0) for x in LANES]
    return all(0 <= x <= 100 for x in nums) and abs(sum(nums) - 100.0) <= 0.5


class MasterStore:
    """Read-only Shimonoseki master bundle used by both pre and final phases."""

    def __init__(self, master_dir: str | Path):
        self.root = Path(master_dir)
        required = [
            "shimonoseki_course_remaining_master_v1.csv",
            "shimonoseki_tide_time_course_master_v1.csv",
            "shimonoseki_motor_type_master_v1.csv",
            "shimonoseki_player_course_db_v6_1.csv",
            "shimonoseki_player_type_master_by_lane.csv",
            "shimonoseki_player_st_course_db_v6_1.csv",
            "shimonoseki_player_master_from_fan_normalized.csv",
        ]
        missing = [name for name in required if not (self.root / name).is_file()]
        if missing:
            raise FileNotFoundError("missing Shimonoseki master files: " + ", ".join(missing))

        self.course_rows = _read_csv(self.root / required[0])
        self.tide_rows = _read_csv(self.root / required[1])
        self.motor_type_rows = _read_csv(self.root / required[2])
        self.player_course_rows = _read_csv(self.root / required[3])
        self.player_type_lane_rows = _read_csv(self.root / required[4])
        self.player_st_rows = _read_csv(self.root / required[5])
        self.player_master_rows = _read_csv(self.root / required[6])

        self.course = {int(r["lane"]): r for r in self.course_rows}
        self.player_course = {}
        for r in self.player_course_rows:
            lane = _i(r.get("entry_course"))
            pid = str(r.get("player_id") or "").strip()
            name = _norm_name(r.get("player_name"))
            if pid:
                self.player_course[("id", pid, lane)] = r
            if name:
                self.player_course[("name", name, lane)] = r

        self.player_type_lane = {}
        for r in self.player_type_lane_rows:
            lane = _i(r.get("lane_std"))
            pid = str(r.get("player_id_std") or "").strip()
            name = _norm_name(r.get("player_name_std"))
            if pid:
                self.player_type_lane[("id", pid, lane)] = r
            if name:
                self.player_type_lane[("name", name, lane)] = r

        self.name_to_id = {}
        for r in self.player_master_rows:
            name = _norm_name(r.get("name") or r.get("player_name"))
            pid = str(r.get("reg_no") or r.get("player_id") or "").strip()
            if name and pid:
                self.name_to_id[name] = pid

        self.motor_fallback = {}
        fallback = self.root / "shimonoseki_motor_recent10_master_v1.csv"
        if fallback.is_file():
            for r in _read_csv(fallback):
                self.motor_fallback[str(r.get("motor_no") or "").lstrip("0") or "0"] = r

        self.course_master, self.tide_rates = load_course_and_tide(self.root)

    def resolve_player_id(self, racer: Mapping[str, Any]) -> str:
        pid = str(racer.get("player_id") or racer.get("reg_no") or racer.get("registration_no") or "").strip()
        if pid:
            return pid
        return self.name_to_id.get(_norm_name(racer.get("name")), "")

    def player_course_row(self, racer: Mapping[str, Any], course: int) -> Mapping[str, Any] | None:
        pid = self.resolve_player_id(racer)
        if pid and ("id", pid, course) in self.player_course:
            return self.player_course[("id", pid, course)]
        return self.player_course.get(("name", _norm_name(racer.get("name")), course))

    def player_type_lane_row(self, racer: Mapping[str, Any], lane: int) -> Mapping[str, Any] | None:
        pid = self.resolve_player_id(racer)
        if pid and ("id", pid, lane) in self.player_type_lane:
            return self.player_type_lane[("id", pid, lane)]
        return self.player_type_lane.get(("name", _norm_name(racer.get("name")), lane))

    def motor_row(self, racer: Mapping[str, Any], dynamic_motor: Mapping[str, Mapping[str, Any]] | None = None) -> Mapping[str, Any] | None:
        ev = racer.get("motorEvaluation") or racer.get("motor_evaluation")
        if isinstance(ev, Mapping) and ev.get("score") is not None:
            return {"motor_score_100": ev.get("score"), "motor_rank": ev.get("rank"), "reliability": ev.get("reliability", 1.0)}
        motor = str(racer.get("motor_no") or "").lstrip("0") or "0"
        if dynamic_motor and motor in dynamic_motor:
            return dynamic_motor[motor]
        return self.motor_fallback.get(motor)


class ShimonosekiSiteEngineV5:
    def __init__(self, master_dir: str | Path):
        self.m = MasterStore(master_dir)
        self.master_dir = Path(master_dir)
        self.local_st = LocalCourseST(self.master_dir / "shimonoseki_player_st_course_db_v6_1.csv")

    def _attach_ids(self, race: MutableMapping[str, Any]) -> None:
        for racer in race.get("racers") or []:
            if not racer.get("player_id"):
                pid = self.m.resolve_player_id(racer)
                if pid:
                    racer["player_id"] = int(pid) if pid.isdigit() else pid

    def _base_predata(self, race: Mapping[str, Any]) -> tuple[dict[str, dict[int, float]], list[dict[str, Any]]]:
        racers = list(race.get("racers") or [])
        if len(racers) != 6:
            raise ValueError("Shimonoseki race must have six racers")

        strengths = []
        sts = []
        for r in racers:
            nat = _f(r.get("nat_win"), float("nan"))
            local = _f(r.get("local_win"), float("nan"))
            if math.isnan(local) or local <= 0:
                strengths.append(nat)
            else:
                strengths.append(0.45 * nat + 0.55 * local)
            sts.append(_f(r.get("avg_st"), float("nan")))

        valid_strengths = [x for x in strengths if math.isfinite(x)]
        valid_sts = [x for x in sts if math.isfinite(x)]
        mean_strength = sum(valid_strengths) / len(valid_strengths) if valid_strengths else 4.8
        mean_st = sum(valid_sts) / len(valid_sts) if valid_sts else 0.18

        lane1_escape = _f(racers[0].get("boaters_escape_rate"), float("nan"))
        attacks = []
        for r in racers[1:5]:
            vals = [_f(r.get(k), float("nan")) for k in ("boaters_sashi_rate", "boaters_makuri_rate", "boaters_makuri_sashi_rate")]
            vals = [v for v in vals if math.isfinite(v)]
            attacks.append(max(vals) if vals else 0.0)
        max_attack = max(attacks) if attacks else 0.0

        settings = {
            "first": ("win_rate", "lane_win_score", 0.18, -2.00, 0.58, 1.55, 14),
            "second": ("second_rate", "lane_top2_score", 0.10, -1.00, 0.65, 1.45, 18),
            "third": ("third_rate", "lane_top3_score", 0.06, -0.45, 0.70, 1.35, 18),
        }
        raw = {pos: {} for pos in settings}
        diagnostics = []

        for i, racer in enumerate(racers):
            lane = _i(racer.get("lane"))
            course = _i(racer.get("actual_course") or racer.get("entry_course") or lane, lane)
            pc = self.m.player_course_row(racer, course)
            pt = self.m.player_type_lane_row(racer, lane)
            diag = {"lane": lane, "name": racer.get("name"), "player_id": self.m.resolve_player_id(racer)}

            for pos, (emp_col, type_col, beta_strength, beta_st, cap_lo, cap_hi, prior_n) in settings.items():
                p0 = _f(self.m.course[lane].get({"first":"win_rate","second":"second_rate","third":"third_rate"}[pos]))
                score = p0
                if pc:
                    starts = _f(pc.get("starts"), 0.0)
                    rel = str(pc.get("reliability") or "")
                    emp = _f(pc.get(emp_col)) / 100.0
                    eff_n = starts * REL_MULT.get(rel, 0.45)
                    blended = (eff_n * emp + prior_n * p0) / (eff_n + prior_n) if eff_n + prior_n else p0
                    ratio = max(cap_lo, min(cap_hi, blended / p0 if p0 else 1.0))
                    score *= ratio
                    diag[f"{pos}_course_blend"] = round(blended * 100, 2)
                    diag[f"{pos}_course_starts"] = int(starts)
                    diag[f"{pos}_course_reliability"] = rel

                if pt:
                    type_score = _f(pt.get(type_col), float("nan"))
                    reliability = _f(pt.get("sample_reliability"), 0.0)
                    if p0 > 0 and math.isfinite(type_score):
                        ratio = max(0.60, min(1.70, type_score / p0))
                        score *= ratio ** (0.22 * reliability)

                if math.isfinite(strengths[i]):
                    score *= math.exp(beta_strength * (strengths[i] - mean_strength))
                if math.isfinite(sts[i]):
                    score *= math.exp(beta_st * (sts[i] - mean_st))

                if pos == "first":
                    if lane == 1 and math.isfinite(lane1_escape) and max_attack >= 6.0:
                        gap = max(0.0, 55.0 - lane1_escape) / 55.0
                        intensity = min(max_attack / 20.0, 1.0)
                        score *= max(0.80, 1.0 - 0.25 * gap * intensity)
                    elif lane in (2, 3, 4, 5) and math.isfinite(lane1_escape):
                        vals = [_f(racer.get(k), float("nan")) for k in ("boaters_sashi_rate", "boaters_makuri_rate", "boaters_makuri_sashi_rate")]
                        vals = [v for v in vals if math.isfinite(v)]
                        attack = max(vals) if vals else 0.0
                        gap = max(0.0, 55.0 - lane1_escape) / 55.0
                        score *= 1.0 + min(0.18, 0.18 * (attack / 20.0) * gap)
                raw[pos][lane] = score
            diagnostics.append(diag)

        return {pos: _normalize(values) for pos, values in raw.items()}, diagnostics

    def _motor_adjust(self, probs: dict[str, dict[int, float]], race: Mapping[str, Any], dynamic_motor: Mapping[str, Mapping[str, Any]] | None) -> tuple[dict[str, dict[int, float]], dict[int, dict[str, Any]]]:
        racers = {int(r["lane"]): r for r in race["racers"]}
        out = {pos: dict(values) for pos, values in probs.items()}
        meta = {}
        for lane in LANES:
            mr = self.m.motor_row(racers[lane], dynamic_motor)
            score = _f((mr or {}).get("motor_score_100"), 50.0)
            rank = str((mr or {}).get("motor_rank") or "")
            rel = _f((mr or {}).get("reliability"), 0.0)
            meta[lane] = {"score": round(score, 3), "rank": rank, "reliability": round(rel, 3)}
            for pos in ("first", "second", "third"):
                out[pos][lane] *= math.exp(MOTOR_BETA[pos] * (score - 50.0))
        return {pos: _normalize(values) for pos, values in out.items()}, meta

    @staticmethod
    def _bucket_from_deadline(deadline: str, tide_events: list[Mapping[str, Any]]) -> str | None:
        if not deadline or not tide_events:
            return None
        def mins(hm: str) -> int:
            h, m = map(int, hm.split(":")); return h * 60 + m
        t = mins(deadline)
        ev = sorted([(mins(str(e["time"])), str(e["type"])) for e in tide_events])
        prev = next((x for x in reversed(ev) if x[0] <= t), None)
        nxt = next((x for x in ev if x[0] > t), None)
        if not prev or not nxt:
            return None
        duration = nxt[0] - prev[0]
        if duration <= 0:
            return None
        progress = (t - prev[0]) / duration
        if "満" in prev[1] and "干" in nxt[1]:
            if progress <= .50: return "falling_mid"
            if progress <= .75: return "falling_late"
            return "near_low"
        if "干" in prev[1] and "満" in nxt[1]:
            if progress <= .25: return "rising_early"
            if progress <= .50: return "rising_mid"
            if progress <= .75: return "rising_late"
            return "near_high"
        return None

    def _tide_adjust(self, probs: dict[str, dict[int, float]], race: Mapping[str, Any], tide_events: list[Mapping[str, Any]]) -> tuple[dict[str, dict[int, float]], str | None]:
        bucket = self._bucket_from_deadline(str(race.get("deadline") or ""), tide_events)
        if not bucket or bucket not in self.m.tide_rates:
            return probs, bucket
        out = {pos: dict(values) for pos, values in probs.items()}
        for pos in ("first", "second", "third"):
            for lane in LANES:
                empirical = self.m.tide_rates[bucket][lane][pos]
                overall = self.m.course_master[lane][pos]
                ratio = max(.65, min(1.35, empirical / overall if overall else 1.0))
                out[pos][lane] *= ratio ** .35
        return {pos: _normalize(values) for pos, values in out.items()}, bucket

    def preliminary_race(self, race: MutableMapping[str, Any], tide_events: list[Mapping[str, Any]], dynamic_motor: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
        self._attach_ids(race)
        probs, diag = self._base_predata(race)
        probs, motor = self._motor_adjust(probs, race, dynamic_motor)
        probs, bucket = self._tide_adjust(probs, race, tide_events)
        rows = []
        for lane in LANES:
            rows.append({
                "lane": lane,
                "first": round(probs["first"][lane], 2),
                "second": round(probs["second"][lane], 2),
                "third": round(probs["third"][lane], 2),
                "motor_score": motor[lane]["score"],
                "motor_rank": motor[lane]["rank"],
            })
            racer = next(r for r in race["racers"] if int(r["lane"]) == lane)
            racer["motorEvaluation"] = {"available": True, **motor[lane], "source": "shimonoseki_v5_preliminary"}
        maps = {
            "win": {str(l): round(probs["first"][l], 2) for l in LANES},
            "second": {str(l): round(probs["second"][l], 2) for l in LANES},
            "third": {str(l): round(probs["third"][l], 2) for l in LANES},
        }
        tickets = self._preliminary_tickets(maps, race)
        sab = self._sab(maps, tickets, phase="preliminary")
        return {
            "engine": ENGINE_ID,
            "engineVersion": ENGINE_VERSION,
            "phase": "preliminary",
            "status": "complete",
            "probabilities": maps,
            "win": maps["win"], "second": maps["second"], "third": maps["third"],
            "sab": sab,
            "tickets": tickets,
            "tideBucket": bucket,
            "rows": rows,
            "debug": {"predata": diag, "result_used": False, "odds_used": False},
        }

    @staticmethod
    def _preliminary_tickets(maps: Mapping[str, Mapping[str, float]], race: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
        # Same 6/2/2 envelope; pre phase has no slit scenario, so upset = best alternate heads.
        first = {l: _f(maps["win"][str(l)]) for l in LANES}
        second = {l: _f(maps["second"][str(l)]) for l in LANES}
        third = {l: _f(maps["third"][str(l)]) for l in LANES}
        all_t = []
        for a in LANES:
            for b in LANES:
                if b == a: continue
                for c in LANES:
                    if c in (a, b): continue
                    all_t.append(((a,b,c), first[a]/100 * second[b]/100 * third[c]/100))
        all_t.sort(key=lambda x: x[1], reverse=True)
        main = all_t[:6]; used = {t for t,_ in main}
        head = main[0][0][0]
        deviation = []
        for t, sc in all_t[6:]:
            if t[0] == head and t not in used:
                deviation.append((t, sc)); used.add(t)
            if len(deviation) == 2: break
        upset = []
        for t, sc in all_t:
            if t[0] != head and t not in used:
                upset.append((t, sc)); used.add(t)
            if len(upset) == 2: break
        fmt = lambda arr: [{"combo":"-".join(map(str,t)), "score":round(sc*100,4)} for t,sc in arr]
        return {"main":fmt(main), "deviation":fmt(deviation), "upset":fmt(upset)}

    @staticmethod
    def _sab(maps: Mapping[str, Mapping[str, float]], tickets: Mapping[str, list[Mapping[str, Any]]], phase: str) -> str:
        win = sorted((_f(v) for v in maps["win"].values()), reverse=True)
        margin = win[0] - win[1]
        head = win[0]
        if head >= 52 and margin >= 18:
            return "S"
        if head >= 40 and margin >= 10:
            return "A"
        return "B"

    def final_race(self, race: MutableMapping[str, Any], prediction_pre: Mapping[str, Any], direct: Mapping[str, Any], exhibition: Mapping[str, Any], original: Mapping[str, Any], tide_events: list[Mapping[str, Any]]) -> dict[str, Any]:
        self._attach_ids(race)
        direct_racers = {int(r["lane"]): r for r in (direct.get("data") or {}).get("racers", [])}
        for racer in race.get("racers") or []:
            incoming = direct_racers.get(int(racer["lane"]))
            if incoming and incoming.get("player_id"):
                racer["player_id"] = incoming["player_id"]

        result = reconcile_final(
            prediction_pre,
            race,
            direct,
            exhibition,
            original,
            self.local_st,
            course_master=self.m.course_master,
            tide_rates=self.m.tide_rates,
            tide_events=tide_events,
        )
        maps = result["probabilities"]
        tickets = build_tickets(maps, race, result["debug"])
        sab = self._sab(maps, tickets, phase="final")
        return {
            "engine": ENGINE_ID,
            "engineVersion": ENGINE_VERSION,
            "phase": "final",
            "status": "complete",
            "probabilities": maps,
            "win": maps["win"], "second": maps["second"], "third": maps["third"],
            "sab": sab,
            "tickets": tickets,
            "debug": {**result["debug"], "result_used": False, "odds_used": False},
        }

    @staticmethod
    def legacy_pred(
        prediction: Mapping[str, Any],
        prediction_pre: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        tickets = prediction["tickets"]
        main = [x["combo"] for x in tickets.get("main", [])]
        deviation = [x["combo"] for x in tickets.get("deviation", [])]
        upset = [x["combo"] for x in tickets.get("upset", [])]
        all10 = main + deviation + upset
        legacy = {
            "win": deepcopy(prediction["win"]),
            "second": deepcopy(prediction["second"]),
            "third": deepcopy(prediction["third"]),
            "sab": prediction["sab"],
            "ai": main,
            "balance": deviation,
            "aiUpset": upset,
            "tickets": all10,
            "engine": ENGINE_ID,
            "engineVersion": ENGINE_VERSION,
            "predictionStage": prediction["phase"],
            "fallback": False,
        }
        if prediction["phase"] == "final" and prediction_pre is not None:
            legacy["predictionPre"] = {
                key: deepcopy(prediction_pre[key])
                for key in ("win", "second", "third")
            }
            legacy["probabilityReviewStatus"] = "reviewed"
            legacy["probabilityFlow"] = {"reviewed": True}
        return legacy

    def apply_preliminary_daily(self, payload: MutableMapping[str, Any], dynamic_motor: Mapping[str, Mapping[str, Any]] | None = None) -> MutableMapping[str, Any]:
        tide_events = list((payload.get("tide") or {}).get("events") or [])
        preds = payload.setdefault("preds", {})
        for race in payload.get("races") or []:
            pred = self.preliminary_race(race, tide_events, dynamic_motor)
            race["predictionPre"] = deepcopy(pred)
            race["prediction"] = deepcopy(pred)
            preds[str(race["race"])] = self.legacy_pred(pred)
        payload["engine"] = ENGINE_ID
        payload["engineVersion"] = ENGINE_VERSION
        payload["predictionAvailable"] = True
        payload["predictionStatus"] = "ready"
        return payload

    def apply_final_race(self, payload: MutableMapping[str, Any], race_no: int, direct: Mapping[str, Any], exhibition: Mapping[str, Any], original: Mapping[str, Any]) -> MutableMapping[str, Any]:
        race = next((r for r in payload.get("races") or [] if int(r.get("race") or 0) == int(race_no)), None)
        if race is None:
            raise KeyError(f"race {race_no} not found")
        pre = race.get("predictionPre")
        if not isinstance(pre, Mapping):
            raise RuntimeError(f"race {race_no}: predictionPre missing; run preliminary first")
        tide_events = list((payload.get("tide") or {}).get("events") or [])
        final = self.final_race(race, pre, direct, exhibition, original, tide_events)
        race["predictionFinal"] = deepcopy(final)
        race["prediction"] = deepcopy(final)
        payload.setdefault("preds", {})[str(race_no)] = self.legacy_pred(final, pre)
        payload["engine"] = ENGINE_ID
        payload["engineVersion"] = ENGINE_VERSION
        payload["predictionAvailable"] = True
        payload["predictionStatus"] = "ready"
        return payload

    @staticmethod
    def validate_payload(payload: Mapping[str, Any], require_all: bool = True) -> tuple[bool, str]:
        if payload.get("engine") != ENGINE_ID:
            return False, "engine_mismatch"
        preds = payload.get("preds")
        if not isinstance(preds, Mapping):
            return False, "preds_missing"
        expected = {str(x) for x in range(1,13)} if require_all else set(preds)
        if require_all and set(preds) != expected:
            return False, "race_count_invalid"
        for key in expected:
            p = preds.get(key)
            if not isinstance(p, Mapping): return False, f"prediction_{key}_missing"
            for pos in ("win","second","third"):
                if not isinstance(p.get(pos), Mapping) or not _prob_valid(p[pos]):
                    return False, f"prediction_{key}_{pos}_invalid"
            tickets = p.get("tickets")
            if not isinstance(tickets, list) or len(tickets) != 10 or len(set(tickets)) != 10:
                return False, f"prediction_{key}_tickets_invalid"
            if p.get("fallback"):
                return False, f"prediction_{key}_fallback"
        return True, "ok"
