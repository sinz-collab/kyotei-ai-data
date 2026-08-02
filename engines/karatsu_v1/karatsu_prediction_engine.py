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
    season_score: float = 0.0
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
    VERSION = "1.1.1"
    # Class is a small baseline only. Current form controls the meaningful class amplification.
    CLASS_BASE = {"A1": 0.58, "A2": 0.52, "B1": 0.47, "B2": 0.42}

    def predict(self, race: RaceInput, ticket_count: int = 10) -> Prediction:
        racers = [r for r in race.racers if not r.withdrawn]
        if len(racers) != 6:
            raise ValueError("Exactly six active racers are required.")
        diagnostics: List[str] = []
        lanes_by_course = [r.lane for r in sorted(racers, key=lambda x: x.actual_course)]
        entry_changed = lanes_by_course != [1,2,3,4,5,6]
        if entry_changed:
            diagnostics.append(f"entry_changed:{lanes_by_course}")

        base = {r.lane: self._base_strength(r, racers, race) for r in racers}
        attack = {r.lane: self._attack_strength(r, racers, race, base[r.lane]) for r in racers}
        second = {r.lane: self._second_strength(r, racers, race, base[r.lane]) for r in racers}
        remain = {r.lane: self._remain_strength(r, racers, race, base[r.lane]) for r in racers}

        scenarios = self._build_scenarios(racers, race, base, attack, second, remain)
        # Correct pipeline: state-adjusted strengths -> scenarios -> trifectas -> marginals -> tickets.
        tri = self._aggregate_trifectas(scenarios)
        first, second, third = self._marginals(tri)
        tickets = self._select_tickets_with_scenario_floors(tri, scenarios, ticket_count)
        sab = self._sab(first, second, third, scenarios, tickets, entry_changed, race)
        self._audit(tickets, tri, diagnostics)
        return Prediction(scenarios, tri, first, second, third, tickets, sab, diagnostics)

    def _day_weights(self, day_no: int):
        if day_no <= 2:
            return {"player":.29,"machine":.24,"live":.20,"season":.10,"course":.17}
        if day_no >= 5:
            return {"player":.21,"machine":.14,"live":.13,"season":.35,"course":.17}
        return {"player":.24,"machine":.19,"live":.17,"season":.23,"course":.17}

    def _state(self, r: RacerInput, racers: Sequence[RacerInput], race: RaceInput) -> float:
        machine = 0.55*self._scale(r.motor_3,30,70)+0.25*self._scale(r.motor_2,15,50)+0.20*self._scale(r.boat_3,30,70)
        live = (0.45*self._rank_value(r.exhibition_time,racers,"exhibition_time")+
                0.25*self._rank_value(r.lap_time,racers,"lap_time")+
                0.20*self._rank_value(r.turn_time,racers,"turn_time")+
                0.10*self._rank_value(r.straight_time,racers,"straight_time"))
        season = self._clip((r.season_score+1)/2,0,1)
        if race.day_no <= 2: return 0.55*machine+0.45*live
        if race.day_no >= 5: return 0.70*season+0.20*live+0.10*machine
        return 0.35*machine+0.30*live+0.35*season

    def _class_position_multiplier(self, r: RacerInput, racers, race):
        state=self._state(r,racers,race)
        if r.class_rank=="A1": return (1+.03*state,1+.08*state,1+.08*state)
        if r.class_rank=="A2": return (1+.02*state,1+.05*state,1+.05*state)
        if r.class_rank=="B2": return (.94+.05*state,.94+.08*state,.96+.10*state)
        return (1.0,1.0,1.0)

    def _base_strength(self, r: RacerInput, racers: Sequence[RacerInput], race: RaceInput) -> float:
        w=self._day_weights(race.day_no)
        player=.65*self._scale(r.nat_win,2,8)+.35*self._scale(r.nat_top3,15,85)
        local=.55*self._scale(r.local_win,1,8)+.45*self._scale(r.local_top3,5,85)
        machine=.45*self._scale(r.motor_2,15,50)+.25*self._scale(r.motor_3,30,70)+.20*self._scale(r.boat_2,15,50)+.10*self._scale(r.boat_3,30,70)
        live=.62*self._rank_value(r.exhibition_time,racers,"exhibition_time")+.38*self._rank_value(r.lap_time,racers,"lap_time")
        season=self._clip((r.season_score+1)/2,0,1)
        cls=self.CLASS_BASE.get(r.class_rank,.47)
        player_mix=.78*(.72*player+.28*local)+.22*cls
        return max(.01,w["player"]*player_mix+w["machine"]*machine+w["live"]*live+w["season"]*season+w["course"]*self._course_prior(r.actual_course))

    def _attack_strength(self,r,racers,race,base):
        score=base+.11*self._rank_value(r.exhibition_time,racers,"exhibition_time")+.07*self._rank_value(r.straight_time,racers,"straight_time")+.07*self._rank_value(r.turn_time,racers,"turn_time")+.05*self._rank_value(r.lap_time,racers,"lap_time")
        if r.exhibition_st is not None: score+=.012*self._rank_value(r.exhibition_st,racers,"exhibition_st")
        score+={1:.06,3:.08,4:.13,5:.06}.get(r.actual_course,0)
        if race.wind_speed>=4 or race.wave_height>=4: score+=-.07 if r.actual_course==1 else (.04 if r.actual_course in (2,3,4,5) else 0)
        if r.tilt>=2: score+=.08 if r.actual_course>=4 else .02
        f,_,_=self._class_position_multiplier(r,racers,race)
        return max(.01,score*f)

    def _second_strength(self,r,racers,race,base):
        # Motor/boat and current form are routed more strongly to second place than to first.
        state=self._state(r,racers,race)
        score=base+.05*self._rank_value(r.exhibition_time,racers,"exhibition_time")
        score+=.10*self._scale(r.motor_3,30,70)+.05*self._scale(r.boat_3,30,70)
        score+=.05 if r.actual_course in (2,3) else (.025 if r.actual_course in (4,5,6) else 0)
        _,f2,_=self._class_position_multiplier(r,racers,race)
        return max(.01,score*f2*(.96+.08*state))

    def _remain_strength(self,r,racers,race,base):
        # Third-place exhibition impact is deliberately lower than first-place impact.
        score=base+.10*self._rank_value(r.lap_time,racers,"lap_time")+.08*self._rank_value(r.turn_time,racers,"turn_time")+.04*self._rank_value(r.straight_time,racers,"straight_time")
        score+=.10 if r.actual_course<=3 else 0
        score+=.08*self._scale(r.motor_3,30,70)+.04*self._scale(r.boat_3,30,70)
        _,_,f3=self._class_position_multiplier(r,racers,race)
        return max(.01,score*f3)

    def _inner_course_multiplier(self,r,entry_changed):
        lane_match=1 if r.lane==1 else .74
        entry_factor=1 if (not entry_changed or r.lane==1) else .82
        depth=max(.72,1-.28*self._clip(r.entry_depth_risk,0,1))
        return self._clip(lane_match*entry_factor*depth,.48,1)

    def _build_scenarios(self,racers,race,base,attack,second,remain):
        by={r.actual_course:r for r in racers}; inner=by[1]
        changed=[r.lane for r in sorted(racers,key=lambda x:x.actual_course)]!=[1,2,3,4,5,6]
        raw=.20+.19*base[inner.lane]+.10*attack[inner.lane]
        if race.wind_speed>=5 or race.wave_height>=5: raw-=.10
        elif race.wind_speed>=4 or race.wave_height>=4: raw-=.07
        gap=max(attack[by[c].lane] for c in (3,4,5))-attack[inner.lane]
        raw-=.08 if gap>=.30 else (.05 if gap>=.18 else (.03 if gap>=.10 else 0))
        hold=self._clip(raw*self._inner_course_multiplier(inner,changed),.08,.46)
        ranked=sorted((2,3,4,5,6),key=lambda c:attack[by[c].lane],reverse=True)
        primary,secondary=ranked[:2]
        catalyst6 = by[6].tilt >= 2.0 or self._rank_value(by[6].exhibition_time, racers, "exhibition_time") >= 0.80
        catalyst_weight = (1-hold)*.14 if catalyst6 else 0.0
        scenarios=[
          self._scenario("inner_hold",hold,1,racers,attack,second,remain,True),
          self._scenario(f"primary_{primary}_attack",(1-hold)*.38,primary,racers,attack,second,remain,False),
          self._scenario(f"secondary_{secondary}_attack",(1-hold)*.23,secondary,racers,attack,second,remain,False),
          self._attacker_out("attacker_out_link",(1-hold)*.14,primary,racers,attack,second,remain),
          self._course6_catalyst_out("course6_catalyst_out",catalyst_weight,racers,second,remain),
          self._mixed("residual",(1-hold)*(.11 if catalyst6 else .25),base,second,remain),
        ]
        total=sum(s.weight for s in scenarios)
        for s in scenarios:s.weight/=total
        return scenarios

    def _scenario(self,name,weight,attack_course,racers,attack,second_strength,remain,inner_holds):
        by={r.actual_course:r for r in racers}; attacker=by[attack_course]; inner=by[1]; first={};second={};third={}
        if inner_holds:
            first[inner.lane]=.78; primary=max((2,3,4,5,6),key=lambda c:attack[by[c].lane])
            for c in (2,3,4,5,6):
                r=by[c]; second[r.lane]=second_strength[r.lane]*(1.20 if c in (2,3) else (1.02 if c==4 else .92))
                d=c-primary; link=1.34 if d==1 else (1.20 if d==2 else (1.08 if d>=3 else 1.02))
                third[r.lane]=remain[r.lane]*link*(.82+.36*self._scale(r.motor_3,30,70))
        else:
            first[attacker.lane]=.56; first[inner.lane]=.18
            for r in racers:
                c=r.actual_course
                if r.lane==attacker.lane: second[r.lane]=attack[r.lane]*.34; third[r.lane]=remain[r.lane]*.56; continue
                sec=second_strength[r.lane]; thi=remain[r.lane]
                if c==1:sec*=1.24;thi*=1.10
                elif c<attack_course:sec*=1.12;thi*=1.08
                d=c-attack_course
                if d==1:sec*=1.22;thi*=1.34
                elif d==2:sec*=1.10;thi*=1.22
                elif d>=3:sec*=.96;thi*=1.10
                second[r.lane]=sec;third[r.lane]=thi*(.84+.34*self._scale(r.motor_3,30,70))
        return Scenario(name,weight,attack_course,self._normalize(first),self._normalize(second),self._normalize(third),["dynamic_class_state","outer_linkage"])

    def _attacker_out(self,name,weight,attack_course,racers,attack,second_strength,remain):
        by={r.actual_course:r for r in racers}; attacker=by[attack_course]; inner=by[1]
        first={inner.lane:.62}; second={}; third={}
        for r in racers:
            if r.lane==attacker.lane: third[r.lane]=remain[r.lane]*.25; continue
            d=r.actual_course-attack_course
            second[r.lane]=second_strength[r.lane]*(1.55 if d==-1 else (1.28 if d==-2 else (1.18 if d==1 else (1.05 if r.actual_course<attack_course else .82))))
            third[r.lane]=remain[r.lane]*(1.38 if d==-2 else (1.22 if d==-1 else (1.16 if d==1 else 1.0)))
        return Scenario(name,weight,attack_course,self._normalize(first),self._normalize(second),self._normalize(third),["attacker_finishes_out","inside_revival","second_outer_link"])

    def _course6_catalyst_out(self,name,weight,racers,second_strength,remain):
        by={r.actual_course:r for r in racers}; inner=by[1]
        # Course 6 creates the development but drops out; course 5 links to second,
        # and course 4/inside survival is protected for third.
        first={inner.lane:1.0}
        second={by[5].lane:1.75*second_strength[by[5].lane], by[4].lane:.65*second_strength[by[4].lane], by[3].lane:.45*second_strength[by[3].lane]}
        third={by[4].lane:1.60, by[3].lane:1.05, by[2].lane:.90, by[5].lane:.55}
        for c in (2,3,4,5):
            third[by[c].lane]=third.get(by[c].lane,0)*(.88+.28*self._scale(by[c].motor_3,30,70))
        return Scenario(name,weight,6,self._normalize(first),self._normalize(second),self._normalize(third),["course6_attack_catalyst","course5_second_link","course4_inside_third"])

    def _mixed(self,name,weight,base,second,remain):
        return Scenario(name,weight,0,self._normalize(base),self._normalize(second),self._normalize(remain),["residual"])

    def _aggregate_trifectas(self,scenarios):
        tri={}
        for s in scenarios:
            for a,b,c in permutations(range(1,7),3):
                p=s.weight*s.first.get(a,0)*s.second.get(b,0)*s.third.get(c,0)
                if p>0:tri[f"{a}-{b}-{c}"]=tri.get(f"{a}-{b}-{c}",0)+p
        total=sum(tri.values())
        return dict(sorted(((k,v/total) for k,v in tri.items()),key=lambda x:x[1],reverse=True))

    def _marginals(self,tri):
        f={i:0 for i in range(1,7)};s={i:0 for i in range(1,7)};t={i:0 for i in range(1,7)}
        for k,p in tri.items():a,b,c=map(int,k.split("-"));f[a]+=p;s[b]+=p;t[c]+=p
        return f,s,t

    def _scenario_top_ticket(self,scenario):
        best=None;bp=-1
        for a,b,c in permutations(range(1,7),3):
            p=scenario.first.get(a,0)*scenario.second.get(b,0)*scenario.third.get(c,0)
            if p>bp:bp=p;best=f"{a}-{b}-{c}"
        return best

    def _select_tickets_with_scenario_floors(self,tri,scenarios,ticket_count):
        selected=[]
        for s in sorted(scenarios,key=lambda x:x.weight,reverse=True):
            k=self._scenario_top_ticket(s)
            if k and k not in selected:selected.append(k)
        for k in tri:
            if k not in selected:selected.append(k)
            if len(selected)>=ticket_count:break
        return selected[:ticket_count]

    def _sab(self,first,second,third,scenarios,tickets,entry_changed,race):
        vals=sorted(first.values(),reverse=True); gap=vals[0]-vals[1]; top=scenarios[0].weight
        coverage=sum(dict(self._aggregate_trifectas(scenarios)).get(k,0) for k in tickets)
        scenario_consistency=sum(1 for s in scenarios[:4] if self._scenario_top_ticket(s) in tickets)
        if entry_changed:return "B"
        if gap>=.16 and top>=.30 and coverage>=.18 and scenario_consistency>=3:return "S"
        if gap>=.08 and top>=.22 and coverage>=.13 and scenario_consistency>=3:return "A"
        return "B"

    def _audit(self,tickets,tri,diagnostics):
        if len(set(tickets))!=len(tickets):diagnostics.append("duplicate_ticket")
        if not all(k in tri for k in tickets):diagnostics.append("ticket_not_in_probability_table")

    @staticmethod
    def _course_prior(c):return {1:.74,2:.54,3:.49,4:.43,5:.33,6:.22}.get(c,.2)
    @staticmethod
    def _scale(v,lo,hi):return max(0,min(1,(v-lo)/(hi-lo))) if v is not None and hi>lo else .5
    @staticmethod
    def _clip(v,lo,hi):return max(lo,min(hi,v))
    @staticmethod
    def _normalize(d):
        clean={k:max(0,v) for k,v in d.items()}; total=sum(clean.values())
        return {k:v/total for k,v in clean.items()} if total else {k:1/len(clean) for k in clean}
    @staticmethod
    def _rank_value(value,racers,attr):
        if value is None:return .5
        vals=[getattr(r,attr) for r in racers if getattr(r,attr) is not None]
        if len(vals)<2:return .5
        ordered=sorted(vals); return 1-ordered.index(value)/max(1,len(ordered)-1)
