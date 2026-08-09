from toda_utils_v5 import LANES, num
def combo_prob(combo, win, second_by_head, third_by_head):
    a,b,c=map(int,combo.split("-")); return round(num(win[str(a)])*num(second_by_head[str(a)][str(b)])*num(third_by_head[str(a)][str(c)])/10000,1)
def build_head_conditionals(head, scores, scenario):
    links = (scenario or {}).get("links") or [x for x in LANES if x!=head]
    rank_bonus={str(l):max(0,.72-i*.13) for i,l in enumerate(links)}
    import math
    second={}; third={}
    for lane in LANES:
        k=str(lane)
        if lane==head: second[k]=.03; third[k]=.03; continue
        second[k]=math.exp(scores[k]*.34+rank_bonus.get(k,0)); third[k]=math.exp(scores[k]*.22+rank_bonus.get(k,0)+(.14 if lane in (4,5,6) else 0))
    from toda_utils_v5 import normalize_map
    return normalize_map(second), normalize_map(third)
def build_tickets(win, second_by_head, third_by_head, scenarios, sab):
    heads=sorted(LANES,key=lambda x:win[str(x)],reverse=True); head_limit={"S":1,"A":2,"B":3}.get(sab,3); out=[]; smap={s["head"]:s for s in scenarios}
    for h in heads[:head_limit]:
        s=smap.get(h,{}); links=s.get("links") or [x for x in LANES if x!=h]
        secs=sorted([x for x in links if x!=h],key=lambda x:second_by_head[str(h)][str(x)],reverse=True)[:3]
        pairs=[]
        for b in secs:
            thirds=sorted([x for x in LANES if x not in (h,b)],key=lambda x:third_by_head[str(h)][str(x)],reverse=True)
            best_third=third_by_head[str(h)][str(thirds[0])]
            for c in thirds:
                if third_by_head[str(h)][str(c)] < best_third*.20: continue
                pairs.append((second_by_head[str(h)][str(b)]*third_by_head[str(h)][str(c)],b,c))
        pairs.sort(reverse=True)
        for _,b,c in pairs[:6]:
            combo=f"{h}-{b}-{c}"; out.append({"combo":combo,"role":"本線" if len(out)<3 else "展開保険","prob":combo_prob(combo,win,second_by_head,third_by_head),"odds":"-"})
    seen=[]; final=[]
    for x in out:
        if x["combo"] not in seen: seen.append(x["combo"]); final.append(x)
    return final[:6 if sab=="S" else 9 if sab=="A" else 10]
def build_upset_tickets(win, second_by_head, third_by_head, scenarios):
    heads=sorted([x for x in LANES if x!=1],key=lambda x:win[str(x)],reverse=True)[:2]; out=[]
    for h in heads:
        secs=sorted([x for x in LANES if x!=h],key=lambda x:second_by_head[str(h)][str(x)],reverse=True)[:2]
        for b in secs:
            thirds=sorted([x for x in LANES if x not in (h,b)],key=lambda x:third_by_head[str(h)][str(x)],reverse=True)[:2]
            for c in thirds:
                combo=f"{h}-{b}-{c}"; out.append({"combo":combo,"role":"荒れ対応","prob":combo_prob(combo,win,second_by_head,third_by_head),"odds":"-"})
    return out[:8]
