def judge_sab(win, scenarios, second_by_head, third_by_head):
    heads=sorted(range(1,7),key=lambda x:win[str(x)],reverse=True)
    axis=heads[0]
    gap=win[str(axis)]-win[str(heads[1])]
    scenario=next((s for s in scenarios if s["head"]==axis),None)
    aligned=bool(scenario and scenario["weight"]>=.75)
    linked=0
    axis_key=str(axis)
    # second_by_head is keyed by strings. The old int lookup kept linked=0 and blocked S.
    if axis_key in second_by_head:
        linked=sum(1 for k,v in second_by_head[axis_key].items() if int(k)!=axis and v>=14)
    if gap>=9 and aligned and 2<=linked<=4:
        return "S", axis, gap
    if gap>=4.2 and aligned:
        return "A", axis, gap
    return "B", axis, gap
