
def judge_sab(win, scenarios, second_by_head, third_by_head):
    heads=sorted(range(1,7),key=lambda x:win[str(x)],reverse=True)
    axis=heads[0]
    gap=win[str(axis)]-win[str(heads[1])]
    scenario=next((s for s in scenarios if s["head"]==axis),None)
    aligned=bool(scenario and scenario["weight"]>=.75)
    linked=0
    if axis in second_by_head:
        linked=sum(1 for k,v in second_by_head[str(axis)].items() if int(k)!=axis and v>=14)
    if gap>=9 and aligned and 2<=linked<=4:
        return "S", axis, gap
    if gap>=4.2 and aligned:
        return "A", axis, gap
    return "B", axis, gap
