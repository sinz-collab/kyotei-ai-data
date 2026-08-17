#!/usr/bin/env python3
from __future__ import annotations

import argparse
import itertools
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

EPS = 1e-12
POSITIONS = ("win", "second", "third")


def _clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(v)))


def _safe_float(v: Any) -> Optional[float]:
    if v is None or v == "" or v == "-":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _rate01(v: Any) -> Optional[float]:
    x = _safe_float(v)
    if x is None:
        return None
    return x / 100.0 if x > 1.0 else x


def _reg_no(v: Any) -> str:
    return str(v).strip().split(".")[0].zfill(4)


def _softmax(xs: Iterable[float]) -> List[float]:
    xs = list(xs)
    m = max(xs)
    exps = [math.exp(x - m) for x in xs]
    s = sum(exps) or 1.0
    return [x / s for x in exps]


def _normalize_dict(d: Dict[int, float]) -> Dict[int, float]:
    s = sum(max(0.0, v) for v in d.values()) or 1.0
    return {k: max(0.0, v) / s for k, v in d.items()}


def _entropy(ps: List[float]) -> float:
    return -sum(p * math.log(max(p, EPS)) for p in ps) / math.log(len(ps))


def _season(month: int) -> str:
    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    return "autumn"


def _time_zone(race_no: int) -> str:
    if race_no <= 4:
        return "early"
    if race_no <= 8:
        return "middle"
    return "late"


def _wind_band(v: Optional[float]) -> str:
    if v is None:
        return "unknown"
    if v <= 1:
        return "0-1"
    if v <= 3:
        return "2-3"
    if v <= 5:
        return "4-5"
    return "6+"


def _wave_band(v: Optional[float]) -> str:
    if v is None:
        return "unknown"
    if v <= 2:
        return "0-2"
    if v <= 5:
        return "3-5"
    return "6+"


def _mean(xs: Iterable[float]) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def _rank_low_better(values: Dict[int, Optional[float]]) -> Dict[int, int]:
    pairs = sorted((v, lane) for lane, v in values.items() if v is not None)
    return {lane: i + 1 for i, (_, lane) in enumerate(pairs)}


def _rank_score(rank: Optional[int], n: int = 6) -> float:
    if rank is None:
        return 0.0
    # +1 best, -1 worst; ties are intentionally handled by caller if needed.
    if n <= 1:
        return 0.0
    return 1.0 - 2.0 * (rank - 1) / (n - 1)


@dataclass
class BoatState:
    lane: int
    course: int
    reg_no: str
    name: str
    logs: Dict[str, float]
    diagnostics: Dict[str, Any]


class BiwakoPredictionEngineV11:
    """びわこAI v1.1 — scenario-conditional two-stage engine.

    PRELIMINARY
      pre-data -> current-layout course/player-course -> ability -> water/time/season
      -> day-dependent motor/setsukan blend -> normalized marginals -> attack/defense
      -> scenario ranking -> conditional order -> 10 tickets

    FINAL
      preliminary structure + actual course + exhibition rank + slit geometry
      + original exhibition (SUM/turn/straight by scenario) -> scenario re-evaluation
      -> conditional 2nd/3rd -> 10 tickets

    Odds/result fields are never read.
    """

    def __init__(self, db_path: str | Path, config_path: str | Path):
        self.db_path = Path(db_path)
        self.config_path = Path(config_path)
        self.cfg = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.current_start = self.cfg["layout_policy"]["configured_current_data_start"]
        self._player_course_cache = self._build_current_player_course()
        self._kimarite_cache = self._build_current_kimarite()
        self._conditional_cache: Dict[Tuple[int, int], Dict[int, Tuple[float, int]]] = {}

    def close(self) -> None:
        self.conn.close()

    # ---------- schema normalization ----------
    def _canonical_boat(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        b = dict(raw)
        aliases = {
            "reg_no": ("reg_no", "player_id", "registration_no"),
            "national_win_rate": ("national_win_rate", "nat_win"),
            "local_win_rate": ("local_win_rate", "local_win"),
            "motor_top2_rate": ("motor_top2_rate", "motor_2"),
            "motor_top3_rate": ("motor_top3_rate", "motor_3"),
            "exhibition_st": ("exhibition_st", "start_time", "start_raw"),
            "original_sum": ("original_sum", "sum", "sum_value"),
            "original_turn": ("original_turn", "turn_time"),
            "original_straight": ("original_straight", "straight_time"),
            "setsukan_runs": ("setsukan_runs", "season_runs"),
        }
        for dst, srcs in aliases.items():
            if b.get(dst) is None:
                for src in srcs:
                    if raw.get(src) is not None:
                        b[dst] = raw[src]
                        break
        if b.get("reg_no") is None:
            raise ValueError(f"boat lane={b.get('lane')} missing reg_no/player_id")
        b["reg_no"] = _reg_no(b["reg_no"])
        b["lane"] = int(b["lane"])
        b["actual_course"] = int(b.get("actual_course") or b.get("entry_course") or b["lane"])
        return b

    def _canonical_race(self, race: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(race)
        if "race_no" not in out and "race" in out:
            out["race_no"] = out["race"]
        out["boats"] = [self._canonical_boat(b) for b in race.get("boats", race.get("racers", race.get("entries", [])))]
        return out

    # ---------- current-layout DB ----------
    def _build_current_player_course(self) -> Dict[Tuple[str, int], Dict[str, float]]:
        sql = """
        SELECT reg_no, actual_course, COUNT(*) starts,
               SUM(CASE WHEN finish=1 THEN 1 ELSE 0 END) wins,
               SUM(CASE WHEN finish=2 THEN 1 ELSE 0 END) seconds,
               SUM(CASE WHEN finish=3 THEN 1 ELSE 0 END) thirds,
               AVG(st) avg_st
        FROM race_history
        WHERE date >= ?
        GROUP BY reg_no, actual_course
        """
        out = {}
        for r in self.conn.execute(sql, (self.current_start,)):
            out[(_reg_no(r["reg_no"]), int(r["actual_course"]))] = dict(r)
        return out

    def _build_current_kimarite(self) -> Dict[Tuple[str, int], Dict[str, float]]:
        # Recompute from current-layout race_history so old-layout data never leaks in.
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(race_history)")}
        method_col = next((c for c in ("kimarite", "winning_method", "deciding_factor") if c in cols), None)
        if not method_col:
            return {}
        sql = f"""
        SELECT reg_no, actual_course, COUNT(*) starts,
               SUM(CASE WHEN finish=1 THEN 1 ELSE 0 END) wins,
               SUM(CASE WHEN finish=1 AND {method_col} IN ('逃げ','nige','escape') THEN 1 ELSE 0 END) escape_count,
               SUM(CASE WHEN finish=1 AND {method_col} IN ('差し','sashi') THEN 1 ELSE 0 END) sashi_count,
               SUM(CASE WHEN finish=1 AND {method_col} IN ('まくり','makuri') THEN 1 ELSE 0 END) makuri_count,
               SUM(CASE WHEN finish=1 AND {method_col} IN ('まくり差し','makurizashi','makuri_sashi') THEN 1 ELSE 0 END) makurizashi_count
        FROM race_history WHERE date >= ? GROUP BY reg_no, actual_course
        """
        out = {}
        for r in self.conn.execute(sql, (self.current_start,)):
            out[(_reg_no(r["reg_no"]), int(r["actual_course"]))] = dict(r)
        return out

    def _course_prior(self, course: int, pos: str) -> float:
        return float(self.cfg["course_prior"][str(course)][pos])

    def _player_course_factor(self, reg: str, course: int, pos: str) -> Tuple[float, Dict[str, Any]]:
        rec = self._player_course_cache.get((_reg_no(reg), course))
        prior = self._course_prior(course, pos)
        if not rec:
            return 0.0, {"n": 0, "shrunk_rate": prior, "factor_log": 0.0}
        n = int(rec["starts"])
        successes = float(rec[{"win": "wins", "second": "seconds", "third": "thirds"}[pos]])
        k = float(self.cfg["fixed_parameters"]["player_course_prior_strength"])
        shrunk = (successes + k * prior) / (n + k)
        raw = math.log(max(shrunk, EPS) / max(prior, EPS))
        cap = float(self.cfg["fixed_parameters"]["correction_caps_log"]["player_course"])
        w = float(self.cfg["fixed_parameters"]["player_course_log_weight"][pos])
        f = _clip(raw * w, -cap, cap)
        return f, {"n": n, "shrunk_rate": shrunk, "factor_log": f}

    def _lookup_nested_factor(self, table: str, keys: List[str], course: int, pos: str) -> float:
        node: Any = self.cfg.get(table, {})
        for key in keys:
            if not isinstance(node, dict) or str(key) not in node:
                return 0.0
            node = node[str(key)]
        if not isinstance(node, dict) or str(course) not in node:
            return 0.0
        return float(node[str(course)].get(pos, 0.0))

    def _water_factor(self, course: int, pos: str, race: Dict[str, Any]) -> Tuple[float, Dict[str, float]]:
        wind_dir = str(race.get("wind_direction") or "unknown")
        wind_speed = _safe_float(race.get("wind_speed"))
        wave = _safe_float(race.get("wave_height"))
        date = datetime.fromisoformat(str(race["date"])[:10])
        f_wind = self._lookup_nested_factor("wind_factors_log", [wind_dir, _wind_band(wind_speed)], course, pos)
        f_wave = self._lookup_nested_factor("wave_factors_log", [_wave_band(wave)], course, pos)
        f_time = self._lookup_nested_factor("time_factors_log", [_time_zone(int(race["race_no"]))], course, pos)
        f_season = self._lookup_nested_factor("season_factors_log", [_season(date.month)], course, pos)
        f_final = 0.0
        if bool(race.get("final_day_flag")):
            f_final = float(self.cfg.get("final_day_factors_log", {}).get(str(course), {}).get(pos, 0.0))
        total = f_wind + f_wave + 0.70 * f_time + 0.55 * f_season + f_final
        cap = float(self.cfg["fixed_parameters"]["correction_caps_log"]["water_total"])
        total = _clip(total, -cap, cap)
        return total, {"wind": f_wind, "wave": f_wave, "time": 0.70 * f_time, "season": 0.55 * f_season, "final_day": f_final, "total": total}

    def _ability_factor(self, boat: Dict[str, Any], pos: str) -> float:
        if pos != "win":
            return 0.0
        nat = _safe_float(boat.get("national_win_rate"))
        loc = _safe_float(boat.get("local_win_rate"))
        adj = 0.0
        if nat is not None:
            adj += (nat - 5.5) * float(self.cfg["fixed_parameters"]["live_national_win_weight"]) / 2.5
        if loc is not None and loc > 0:
            adj += (loc - 5.5) * float(self.cfg["fixed_parameters"]["live_local_win_weight"]) / 2.5
        cap = float(self.cfg["fixed_parameters"]["correction_caps_log"]["live_ability_total"])
        return _clip(adj, -cap, cap)

    # ---------- event-day motor/setsukan blend ----------
    def _event_day_key(self, race: Dict[str, Any]) -> str:
        if race.get("final_day_flag"):
            return "final"
        label = str(race.get("event_day_label") or race.get("eventDayLabel") or "")
        if "準優" in label or "semifinal" in label.lower():
            return "semifinal"
        day = int(race.get("event_day") or race.get("eventDay") or race.get("day_no") or 1)
        return f"day{max(1, min(day, 5))}"

    def _motor_signal(self, boat: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
        top2 = _rate01(boat.get("motor_top2_rate"))
        top3 = _rate01(boat.get("motor_top3_rate"))
        if top2 is None or top3 is None:
            motor_no = boat.get("motor_no")
            if motor_no is not None:
                row = self.conn.execute(
                    "SELECT top2_rate_total, top3_rate_total, top3_rate_recent10 FROM motor_recent WHERE motor_no=? ORDER BY date DESC LIMIT 1",
                    (int(motor_no),),
                ).fetchone()
                if row:
                    top2 = top2 if top2 is not None else _rate01(row["top2_rate_total"])
                    top3 = top3 if top3 is not None else _rate01(row["top3_rate_recent10"] or row["top3_rate_total"])
        cfg = self.cfg["fixed_parameters"]["motor_signal"]
        zs = []
        if top2 is not None:
            zs.append((top2 - cfg["center_top2"]) / cfg["scale_top2"])
        if top3 is not None:
            zs.append((top3 - cfg["center_top3"]) / cfg["scale_top3"])
        z = _clip(_mean(zs), -cfg["cap_z"], cfg["cap_z"]) if zs else 0.0
        return z, {"top2": top2, "top3": top3, "z": z}

    def _setsukan_runs(self, boat: Dict[str, Any], race: Dict[str, Any]) -> List[Dict[str, Any]]:
        live = boat.get("setsukan_runs") or []
        if live:
            return [dict(r) for r in live]
        event_id = race.get("event_id")
        if not event_id:
            return []
        rows = self.conn.execute(
            "SELECT date,event_day,race_no,course,finish,st,exhibition_time FROM setsukan_runs WHERE event_id=? AND reg_no=? ORDER BY date,race_no",
            (str(event_id), _reg_no(boat["reg_no"])),
        ).fetchall()
        return [dict(r) for r in rows]

    def _setsukan_signal(self, boat: Dict[str, Any], race: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
        runs = self._setsukan_runs(boat, race)
        cfg = self.cfg["fixed_parameters"]["setsukan_signal"]
        target_course = int(boat["actual_course"])
        if not runs:
            return 0.0, {"runs": 0, "z": 0.0}
        weighted = []
        st_terms = []
        n = len(runs)
        for i, r in enumerate(runs):
            fv = r.get("finish")
            finish = _safe_float(str(fv).replace("着", ""))
            course = int(_safe_float(r.get("course") or r.get("entry_course") or target_course) or target_course)
            if finish is None:
                continue
            course_sim = math.exp(-cfg["course_distance_decay"] * abs(course - target_course))
            recency = 0.55 + 0.45 * (i + 1) / n
            w = course_sim * recency
            weighted.append((finish, w))
            st = _safe_float(r.get("st"))
            if st is not None:
                st_terms.append((st, w))
        if not weighted:
            return 0.0, {"runs": len(runs), "z": 0.0}
        avg_finish = sum(f * w for f, w in weighted) / sum(w for _, w in weighted)
        finish_z = (cfg["neutral_finish"] - avg_finish) / 1.8
        if st_terms:
            avg_st = sum(st * w for st, w in st_terms) / sum(w for _, w in st_terms)
            st_z = (cfg["neutral_st"] - avg_st) / 0.10
        else:
            avg_st, st_z = None, 0.0
        # Recency trend: later weighted finish minus earlier weighted finish; improvement is positive.
        finishes = [f for f, _ in weighted]
        trend_z = 0.0
        if len(finishes) >= 2:
            trend_z = (finishes[0] - finishes[-1]) / 2.5
        same_course_n = sum(1 for r in runs if int(_safe_float(r.get("course") or target_course) or target_course) == target_course)
        course_z = min(1.0, same_course_n / 2.0)
        z = (
            cfg["finish_weight"] * finish_z
            + cfg["st_weight"] * st_z
            + cfg["recency_weight"] * trend_z
            + cfg["course_similarity_weight"] * course_z
        )
        z = _clip(z, -cfg["cap_z"], cfg["cap_z"])
        return z, {"runs": len(runs), "avg_finish_adj": avg_finish, "avg_st": avg_st, "same_course_n": same_course_n, "trend_z": trend_z, "z": z}

    def _event_blend_logs(self, boat: Dict[str, Any], race: Dict[str, Any], stage: str) -> Tuple[Dict[str, float], Dict[str, Any]]:
        key = self._event_day_key(race)
        blend = self.cfg["fixed_parameters"]["event_day_blend"][key]
        motor_z, motor_d = self._motor_signal(boat)
        setsukan_z, setsukan_d = self._setsukan_signal(boat, race)
        # Exhibition part is added in final after ranks are available; reserve same budget here.
        machine_z = motor_z
        budgets = self.cfg["fixed_parameters"]["machine_event_budget_log"]
        out = {}
        for pos in POSITIONS:
            b = float(budgets[pos])
            out[pos] = b * (float(blend["motor_exhibition"]) * machine_z + float(blend["setsukan"]) * setsukan_z)
            out[pos] = _clip(out[pos], -b, b)
        return out, {"day_key": key, "blend": blend, "motor": motor_d, "setsukan": setsukan_d, "logs": out}

    # ---------- base states ----------
    def _build_states(self, race: Dict[str, Any], stage: str) -> List[BoatState]:
        out = []
        for boat in race["boats"]:
            lane, course, reg = boat["lane"], boat["actual_course"], boat["reg_no"]
            d: Dict[str, Any] = {"lane": lane, "course": course, "reg_no": reg, "features": {}}
            blend_logs, blend_d = self._event_blend_logs(boat, race, stage)
            logs = {}
            for pos in POSITIONS:
                prior = self._course_prior(course, pos)
                pcf, pcd = self._player_course_factor(reg, course, pos)
                ability = self._ability_factor(boat, pos)
                water, water_d = self._water_factor(course, pos, race)
                logv = math.log(max(prior, EPS)) + pcf + ability + water + blend_logs[pos]
                logs[pos] = logv
                d["features"][pos] = {
                    "course_prior": prior,
                    "player_course": pcd,
                    "ability_log": ability,
                    "water": water_d,
                    "event_day_blend_log": blend_logs[pos],
                }
            d["event_day_blend"] = blend_d
            out.append(BoatState(lane, course, reg, str(boat.get("name") or ""), logs, d))
        return out

    # ---------- final exhibition/slit ----------
    def _exhibition_signals(self, race: Dict[str, Any]) -> Dict[int, float]:
        vals = {b["lane"]: _safe_float(b.get("exhibition_time")) for b in race["boats"]}
        ranks = _rank_low_better(vals)
        return {lane: _rank_score(ranks.get(lane)) for lane in vals}

    def _slit_geometry(self, race: Dict[str, Any]) -> Dict[str, Any]:
        by_course = {b["actual_course"]: b for b in race["boats"]}
        st = {c: _safe_float(b.get("exhibition_st")) for c, b in by_course.items()}
        cfg = self.cfg["fixed_parameters"]["slit_geometry"]
        dent = []
        attack = []
        wall_strength: Dict[int, float] = {}
        outer_link: Dict[int, float] = {}
        for c in range(1, 7):
            x = st.get(c)
            if x is None:
                continue
            prev, nxt = st.get(c - 1), st.get(c + 1)
            if c in (3, 4) and prev is not None and x <= prev - cfg["attack_margin"]:
                attack.append(c)
            if prev is not None and nxt is not None and x >= min(prev, nxt) + cfg["dent_margin"]:
                dent.append(c)
            if c in (2, 3) and prev is not None:
                wall_strength[c] = _clip(1.0 - abs(x - prev) / max(cfg["wall_margin"], EPS), 0.0, 1.0)
        if st.get(4) is not None and st.get(5) is not None:
            outer_link[5] = _clip(1.0 - abs(st[5] - st[4]) / cfg["link_margin"], 0.0, 1.0)
        if st.get(5) is not None and st.get(6) is not None:
            outer_link[6] = _clip(1.0 - abs(st[6] - st[5]) / (cfg["link_margin"] + 0.01), 0.0, 1.0)
        attack_position = None
        candidates = [(c, st[c]) for c in (3, 4) if st.get(c) is not None]
        if candidates:
            attack_position = min(candidates, key=lambda x: x[1])[0]
        return {
            "lineup": [by_course[c]["lane"] for c in sorted(by_course)],
            "st_by_course": st,
            "dent_by_course": dent,
            "attack_courses": attack,
            "attack_position": attack_position,
            "wall_strength": wall_strength,
            "outer_link": outer_link,
        }

    def _apply_final_machine_exhibition(self, states: List[BoatState], race: Dict[str, Any]) -> Dict[str, Any]:
        by_lane = {s.lane: s for s in states}
        ex_sig = self._exhibition_signals(race)
        day_key = self._event_day_key(race)
        blend = self.cfg["fixed_parameters"]["event_day_blend"][day_key]
        budgets = self.cfg["fixed_parameters"]["machine_event_budget_log"]
        # Because preliminary already included motor part, final adds only exhibition's share of machine side.
        for s in states:
            sig = ex_sig.get(s.lane, 0.0)
            for pos in POSITIONS:
                scale = {"win": 1.0, "second": 0.8, "third": 0.65}[pos]
                add = float(budgets[pos]) * float(blend["motor_exhibition"]) * 0.35 * sig * scale
                s.logs[pos] += add
            s.diagnostics["exhibition_signal"] = sig
        geom = self._slit_geometry(race)
        by_course_lane = {s.course: s.lane for s in states}
        cap = float(self.cfg["fixed_parameters"]["correction_caps_log"]["slit"])
        # Geometry, not raw-ST linear scoring.
        for c in geom["attack_courses"]:
            lane = by_course_lane.get(c)
            if lane:
                by_lane[lane].logs["win"] += min(cap, 0.08)
                by_lane[lane].logs["second"] += min(cap, 0.05)
        for c in geom["dent_by_course"]:
            lane = by_course_lane.get(c)
            if lane:
                by_lane[lane].logs["win"] -= min(cap, 0.08)
                by_lane[lane].logs["second"] -= min(cap, 0.05)
        for c, strength in geom["outer_link"].items():
            lane = by_course_lane.get(c)
            if lane:
                by_lane[lane].logs["second"] += min(cap, 0.03 * strength)
                by_lane[lane].logs["third"] += min(cap, 0.05 * strength)
        return {"exhibition_signal_by_lane": ex_sig, "slit_geometry": geom}

    def _apply_original_exhibition_to_states(self, states: List[BoatState], race: Dict[str, Any]) -> Dict[int, Dict[str, float]]:
        """Apply Biwako-specific original-exhibition correction to finish marginals.

        This is deliberately scenario-shaped by course rather than a flat rank bonus:
        C1 escape -> turn+SUM, C2 sashi -> turn, C3 attack -> straight+turn,
        C4 corner attack -> straight, C5/C6 residual -> straight+SUM.
        """
        sig = self._original_signals(race)
        cfg = self.cfg["fixed_parameters"]["original_exhibition"]
        cap = float(cfg["cap_log"])
        course_weights = {
            1: {"sum": 0.60, "turn": 0.65, "straight": 0.10},
            2: {"sum": 0.35, "turn": 0.75, "straight": 0.10},
            3: {"sum": 0.35, "turn": 0.35, "straight": 0.65},
            4: {"sum": 0.25, "turn": 0.20, "straight": 0.80},
            5: {"sum": 0.45, "turn": 0.15, "straight": 0.65},
            6: {"sum": 0.40, "turn": 0.10, "straight": 0.65},
        }
        for s in states:
            w = course_weights[s.course]
            raw = w["sum"] * sig[s.lane]["sum"] + w["turn"] * sig[s.lane]["turn"] + w["straight"] * sig[s.lane]["straight"]
            adj = _clip(0.04 * raw, -cap, cap)
            s.logs["win"] += adj
            s.logs["second"] += 0.85 * adj
            s.logs["third"] += 0.75 * adj
            s.diagnostics["original_exhibition"] = {"signals": sig[s.lane], "scenario_shaped_log": adj}
        return sig

    # ---------- attack-defense ----------
    def _input_attack_defense(self, boat: Dict[str, Any]) -> Dict[str, float]:
        # BOATERS fields may be percentages. For lane1 these are allowed-rates; for attackers they are method-rates.
        def r(key: str) -> Optional[float]:
            return _rate01(boat.get(key))
        return {
            "escape": r("boaters_escape_rate") or 0.0,
            "sashi_allowed": r("boaters_sashare_rate") or 0.0,
            "makuri_allowed": r("boaters_makurare_rate") or 0.0,
            "makurizashi_allowed": r("boaters_makurare_zashi_rate") or 0.0,
            "sashi": r("boaters_sashi_rate") or 0.0,
            "makuri": r("boaters_makuri_rate") or 0.0,
            "makurizashi": r("boaters_makuri_sashi_rate") or 0.0,
        }

    def _db_attack_profile(self, boat: Dict[str, Any]) -> Dict[str, float]:
        rec = self._kimarite_cache.get((boat["reg_no"], boat["actual_course"]))
        if not rec or not rec.get("starts"):
            return {"sashi": 0.0, "makuri": 0.0, "makurizashi": 0.0}
        n = float(rec["starts"])
        return {
            "sashi": float(rec.get("sashi_count") or 0) / n,
            "makuri": float(rec.get("makuri_count") or 0) / n,
            "makurizashi": float(rec.get("makurizashi_count") or 0) / n,
        }

    def _attack_defense(self, states: List[BoatState], race: Dict[str, Any]) -> Dict[str, Any]:
        by_course = {b["actual_course"]: b for b in race["boats"]}
        c1 = by_course.get(1)
        if not c1:
            return {"matches": [], "c1_penalty_log": 0.0}
        defense = self._input_attack_defense(c1)
        matches = []
        total = 0.0
        for c in (2, 3, 4):
            b = by_course.get(c)
            if not b:
                continue
            inp = self._input_attack_defense(b)
            dbp = self._db_attack_profile(b)
            attacks = {k: max(inp[k], dbp[k]) for k in ("sashi", "makuri", "makurizashi")}
            method_score = (
                defense["sashi_allowed"] * attacks["sashi"]
                + defense["makuri_allowed"] * attacks["makuri"]
                + defense["makurizashi_allowed"] * attacks["makurizashi"]
            )
            if method_score > 0:
                matches.append({"course": c, "lane": b["lane"], "score": method_score, "attacks": attacks})
                total += method_score
        cfg = self.cfg["fixed_parameters"]["attack_defense"]
        penalty = 0.0
        if matches:
            penalty = min(cfg["max_head_log_shift"], cfg["match_weight"] * total + cfg["multiple_attacker_bonus"] * max(0, len(matches) - 1))
            c1_lane = c1["lane"]
            state_by_lane = {s.lane: s for s in states}
            state_by_lane[c1_lane].logs["win"] -= penalty
            # redistribute structurally to matching attackers, not equally to all boats
            denom = sum(m["score"] for m in matches) or 1.0
            for m in matches:
                state_by_lane[m["lane"]].logs["win"] += penalty * 0.85 * m["score"] / denom
        return {"defense": defense, "matches": matches, "c1_penalty_log": penalty}

    # ---------- probabilities/scenarios ----------
    def _marginals(self, states: List[BoatState]) -> Dict[int, Dict[str, float]]:
        p = {}
        for pos in POSITIONS:
            vals = _softmax([s.logs[pos] for s in states])
            for i, s in enumerate(states):
                p.setdefault(s.lane, {})[pos] = vals[i]
        for lane in p:
            p[lane]["top3"] = min(1.0, p[lane]["win"] + p[lane]["second"] + p[lane]["third"])
        return p

    def _original_signals(self, race: Dict[str, Any]) -> Dict[int, Dict[str, float]]:
        metrics = {
            "sum": {b["lane"]: _safe_float(b.get("original_sum")) for b in race["boats"]},
            "turn": {b["lane"]: _safe_float(b.get("original_turn")) for b in race["boats"]},
            "straight": {b["lane"]: _safe_float(b.get("original_straight")) for b in race["boats"]},
        }
        out = {b["lane"]: {"sum": 0.0, "turn": 0.0, "straight": 0.0} for b in race["boats"]}
        for metric, vals in metrics.items():
            ranks = _rank_low_better(vals)
            for lane in out:
                out[lane][metric] = _rank_score(ranks.get(lane))
        return out

    def _scenario_scores(self, probs: Dict[int, Dict[str, float]], states: List[BoatState], race: Dict[str, Any], stage: str, live: Dict[str, Any]) -> List[Dict[str, Any]]:
        by_course = {s.course: s.lane for s in states}
        p = lambda c, pos: probs.get(by_course.get(c, -1), {}).get(pos, 0.0)
        raw = {
            "1_escape_2_sashi": 1.20 * p(1, "win") + 0.50 * p(2, "second"),
            "2_sashi": 1.08 * p(2, "win") + 0.32 * p(1, "second"),
            "3_attack": 1.05 * p(3, "win") + 0.38 * p(3, "second"),
            "4_attack": 1.08 * p(4, "win") + 0.40 * p(4, "second"),
            "4_to_5_link": 0.75 * p(4, "win") + 0.42 * p(5, "second") + 0.52 * p(5, "third"),
            "outer_chaos": 0.82 * (p(5, "win") + p(6, "win")) + 0.25 * (p(5, "third") + p(6, "third")),
        }
        oe = self._original_signals(race) if stage == "final" else {}
        if stage == "final" and self.cfg["fixed_parameters"]["original_exhibition"]["enabled"]:
            oecfg = self.cfg["fixed_parameters"]["original_exhibition"]
            scen_w = oecfg["scenario_weights"]
            scenario_course = {"1_escape_2_sashi": 1, "2_sashi": 2, "3_attack": 3, "4_attack": 4, "4_to_5_link": 4, "outer_chaos": 5}
            for name in raw:
                c = scenario_course[name]
                lane = by_course.get(c)
                if lane and lane in oe:
                    sw = scen_w[name]
                    signal = sw["sum"] * oe[lane]["sum"] + sw["turn"] * oe[lane]["turn"] + sw["straight"] * oe[lane]["straight"]
                    raw[name] *= math.exp(_clip(0.035 * signal, -oecfg["cap_log"], oecfg["cap_log"]))
            # 4->5 link explicitly includes 5's straight/SUM.
            l5 = by_course.get(5)
            if l5 and l5 in oe:
                link_sig = 0.55 * oe[l5]["straight"] + 0.45 * oe[l5]["sum"]
                raw["4_to_5_link"] *= math.exp(_clip(0.035 * link_sig, -oecfg["cap_log"], oecfg["cap_log"]))
        # slit geometry affects scenario scores, not raw ST as a boat coefficient.
        geom = live.get("slit_geometry", {}) if live else {}
        if 3 in geom.get("attack_courses", []):
            raw["3_attack"] *= 1.10
        if 4 in geom.get("attack_courses", []):
            raw["4_attack"] *= 1.12
            raw["4_to_5_link"] *= 1.06
        if geom.get("outer_link", {}).get(5, 0) > 0.5:
            raw["4_to_5_link"] *= 1.08
        total = sum(max(v, 0.0) for v in raw.values()) or 1.0
        ranking = [{"name": k, "score": v, "share": v / total} for k, v in raw.items()]
        ranking.sort(key=lambda x: x["score"], reverse=True)
        return ranking

    # ---------- conditional order ----------
    def _course_lane_maps(self, states: List[BoatState]) -> Tuple[Dict[int, int], Dict[int, int]]:
        course_to_lane = {s.course: s.lane for s in states}
        lane_to_course = {s.lane: s.course for s in states}
        return course_to_lane, lane_to_course

    def _conditional_order_db(self, first_course: int, second_course: int) -> Dict[int, Tuple[float, int]]:
        # Recompute from current-layout race_history for production correctness.
        key = (first_course, second_course)
        if key in self._conditional_cache:
            return self._conditional_cache[key]
        rows = self.conn.execute(
            """
            WITH races AS (
              SELECT date, race_no,
                     MAX(CASE WHEN finish=1 THEN actual_course END) c1,
                     MAX(CASE WHEN finish=2 THEN actual_course END) c2,
                     MAX(CASE WHEN finish=3 THEN actual_course END) c3
              FROM race_history WHERE date >= ? GROUP BY date,race_no
            )
            SELECT c3 third_course, COUNT(*) occurrences
            FROM races WHERE c1=? AND c2=? GROUP BY c3
            """,
            (self.current_start, first_course, second_course),
        ).fetchall()
        counts = {int(r["third_course"]): int(r["occurrences"]) for r in rows if r["third_course"] is not None}
        den = sum(counts.values())
        if den == 0:
            self._conditional_cache[key] = {}
            return {}
        result = {c: (n / den, den) for c, n in counts.items()}
        self._conditional_cache[key] = result
        return result

    def _branch_bonus(self, first_course: int, second_course: int, third_course: int, scenario: str, live: Dict[str, Any]) -> float:
        cfg = self.cfg["fixed_parameters"]["conditional_order"]
        bonus = 1.0
        geom = live.get("slit_geometry", {}) if live else {}
        if scenario in ("3_attack", "4_attack", "4_to_5_link"):
            # When an attacker wins, inside residual + outer linked boats get structural support.
            if third_course in (1, 2):
                bonus *= 1.0 + cfg["inside_residual_bonus"]
            if third_course in (5, 6) and geom.get("outer_link", {}).get(third_course, 0) > 0:
                bonus *= 1.0 + cfg["branch_link_bonus"] * geom["outer_link"][third_course]
        return bonus

    def _conditional_ticket_probs(self, probs: Dict[int, Dict[str, float]], states: List[BoatState], scenarios: List[Dict[str, Any]], live: Dict[str, Any]) -> List[Dict[str, Any]]:
        course_to_lane, lane_to_course = self._course_lane_maps(states)
        scen_by_head_course = {1: "1_escape_2_sashi", 2: "2_sashi", 3: "3_attack", 4: "4_attack", 5: "outer_chaos", 6: "outer_chaos"}
        cfg = self.cfg["fixed_parameters"]["conditional_order"]
        out = []
        for a, b, c in itertools.permutations(sorted(probs), 3):
            ca, cb, cc = lane_to_course[a], lane_to_course[b], lane_to_course[c]
            p1 = probs[a]["win"]
            second_raw = {lane: probs[lane]["second"] for lane in probs if lane != a}
            # scenario-conditioned second: inside residual after outside attack; linked 5 after 4 attack.
            scen = scen_by_head_course.get(ca, "outer_chaos")
            if ca in (3, 4):
                lane1 = course_to_lane.get(1)
                if lane1 in second_raw:
                    second_raw[lane1] *= 1.08
            if ca == 4:
                lane5 = course_to_lane.get(5)
                link = live.get("slit_geometry", {}).get("outer_link", {}).get(5, 0) if live else 0
                if lane5 in second_raw and link > 0:
                    second_raw[lane5] *= 1.0 + 0.14 * link
            second_cond = _normalize_dict(second_raw)
            p2 = second_cond[b]

            third_raw = {lane: probs[lane]["third"] for lane in probs if lane not in (a, b)}
            db = self._conditional_order_db(ca, cb)
            for lane in list(third_raw):
                tc = lane_to_course[lane]
                if tc in db:
                    q, n = db[tc]
                    if n >= cfg["n_strong"]:
                        blend = cfg["blend_strong"]
                    elif n >= cfg["n_medium"]:
                        blend = cfg["blend_medium"]
                    elif n >= cfg["n_weak"]:
                        blend = cfg["blend_weak"]
                    else:
                        blend = cfg["blend_none"]
                    # Blend on normalized candidate space; q is absolute conditional frequency.
                    third_raw[lane] = (1.0 - blend) * third_raw[lane] + blend * q
                third_raw[lane] *= self._branch_bonus(ca, cb, tc, scen, live)
            third_cond = _normalize_dict(third_raw)
            p3 = third_cond[c]
            out.append({
                "ticket": f"{a}-{b}-{c}", "lanes": (a, b, c), "courses": (ca, cb, cc),
                "score": p1 * p2 * p3, "p_first": p1, "p_second_given_first": p2,
                "p_third_given_first_second": p3, "scenario": scen,
            })
        out.sort(key=lambda x: x["score"], reverse=True)
        return out

    def _generate_tickets(self, joint: List[Dict[str, Any]], probs: Dict[int, Dict[str, float]]) -> Dict[str, Any]:
        cfg = self.cfg["fixed_parameters"]["ticket_generation"]
        top_head = max(probs, key=lambda x: probs[x]["win"])
        used = set()
        main, deviation, upset = [], [], []
        # Main is top joint combinations, but enforce scenario diversity by allowing up to 4 per head before fill.
        per_head: Dict[int, int] = {}
        for row in joint:
            t = row["ticket"]
            head = row["lanes"][0]
            if t in used or per_head.get(head, 0) >= 4:
                continue
            main.append(row); used.add(t); per_head[head] = per_head.get(head, 0) + 1
            if len(main) == cfg["main"]:
                break
        for row in joint:
            if len(main) == cfg["main"]:
                break
            if row["ticket"] not in used:
                main.append(row); used.add(row["ticket"])
        for row in joint:
            if row["ticket"] in used:
                continue
            if row["lanes"][0] == top_head:
                deviation.append(row); used.add(row["ticket"])
            if len(deviation) == cfg["deviation"]:
                break
        for row in joint:
            if row["ticket"] in used:
                continue
            if row["lanes"][0] != top_head:
                upset.append(row); used.add(row["ticket"])
            if len(upset) == cfg["upset"]:
                break
        while len(deviation) < cfg["deviation"]:
            row = next(r for r in joint if r["ticket"] not in used)
            deviation.append(row); used.add(row["ticket"])
        while len(upset) < cfg["upset"]:
            row = next(r for r in joint if r["ticket"] not in used)
            upset.append(row); used.add(row["ticket"])
        fmt = lambda xs: [r["ticket"] for r in xs]
        return {
            "main": fmt(main), "deviation": fmt(deviation), "upset": fmt(upset),
            "ranked_top20": [{k: r[k] for k in ("ticket", "score", "p_first", "p_second_given_first", "p_third_given_first_second", "scenario")} for r in joint[:20]],
        }

    def _sab(self, probs: Dict[int, Dict[str, float]], scenarios: List[Dict[str, Any]], race: Dict[str, Any], stage: str) -> Dict[str, Any]:
        wins = sorted((v["win"] for v in probs.values()), reverse=True)
        gap = wins[0] - wins[1]
        ent = _entropy([probs[i]["win"] for i in sorted(probs)])
        score = 40 + min(22, wins[0] * 28) + min(18, gap * 65) + min(12, scenarios[0]["share"] * 32) + max(0, (1 - ent) * 10)
        reasons = [f"head_max={wins[0]:.3f}", f"head_gap={gap:.3f}", f"primary_scenario_share={scenarios[0]['share']:.3f}", f"win_entropy={ent:.3f}"]
        if not race.get("wind_direction") or race.get("wind_speed") is None:
            score -= 4; reasons.append("water_incomplete")
        if any(b["actual_course"] != b["lane"] for b in race["boats"]):
            score -= 3; reasons.append("entry_changed")
        if stage == "final":
            exn = sum(_safe_float(b.get("exhibition_time")) is not None for b in race["boats"])
            oen = sum(_safe_float(b.get("original_sum")) is not None for b in race["boats"])
            if exn == 6:
                score += 2; reasons.append("exhibition_complete")
            if oen == 6:
                score += 2; reasons.append("original_exhibition_complete")
        score = _clip(score, 0, 100)
        th = self.cfg["fixed_parameters"]["sab_thresholds"]
        grade = "S" if score >= th["S"] else ("A" if score >= th["A"] else "B")
        return {"grade": grade, "score": round(score, 1), "reasons": reasons}

    def predict(self, race: Dict[str, Any], stage: str = "preliminary") -> Dict[str, Any]:
        if stage not in ("preliminary", "final"):
            raise ValueError("stage must be preliminary or final")
        race = self._canonical_race(race)
        if len(race.get("boats", [])) != 6:
            raise ValueError("Biwako v1.1 requires exactly 6 boats")
        if not race.get("date") or not race.get("race_no"):
            raise ValueError("date and race_no are required")

        states = self._build_states(race, stage)
        live: Dict[str, Any] = {}
        if stage == "final":
            live = self._apply_final_machine_exhibition(states, race)
            if self.cfg["fixed_parameters"]["original_exhibition"]["enabled"]:
                oe_sig = self._apply_original_exhibition_to_states(states, race)
                live["original_exhibition_signal_by_lane"] = oe_sig
        attack_defense = self._attack_defense(states, race)
        probs = self._marginals(states)
        scenarios = self._scenario_scores(probs, states, race, stage, live)
        joint = self._conditional_ticket_probs(probs, states, scenarios, live)
        tickets = self._generate_tickets(joint, probs)
        sab = self._sab(probs, scenarios, race, stage)

        boats_out = []
        for s in sorted(states, key=lambda x: x.lane):
            pr = probs[s.lane]
            boats_out.append({
                "lane": s.lane, "actual_course": s.course, "reg_no": s.reg_no, "name": s.name,
                "win_prob": round(pr["win"], 6), "second_prob": round(pr["second"], 6),
                "third_prob": round(pr["third"], 6), "top3_prob": round(pr["top3"], 6),
                "diagnostics": s.diagnostics,
            })
        return {
            "venue": "biwako", "engine_version": self.cfg["engine_version"], "parameter_version": self.cfg["parameter_version"],
            "stage": stage, "date": race["date"], "race_no": int(race["race_no"]), "event_id": race.get("event_id"),
            "boats": boats_out, "scenario": {"primary": scenarios[0], "ranking": scenarios},
            "attack_defense": attack_defense, "sab": sab, "tickets": tickets, "live_adjustment": live or None,
            "rules": {
                "odds_used": False, "result_used": False, "ticket_count": 10,
                "ticket_structure": {"main": 6, "deviation": 2, "upset": 2},
                "original_exhibition_used_in_final": True,
                "event_day_blend": self.cfg["fixed_parameters"]["event_day_blend"][self._event_day_key(race)],
                "conditional_order_current_layout_only": True,
            },
        }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--stage", choices=["preliminary", "final"], default="preliminary")
    args = ap.parse_args()
    race = json.loads(Path(args.input).read_text(encoding="utf-8"))
    eng = BiwakoPredictionEngineV11(args.db, args.config)
    try:
        out = eng.predict(race, args.stage)
    finally:
        eng.close()
    Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
