def judge_sab(boats, scenarios, completeness, ticket_count):
    win=sorted([b['win_prob'] for b in boats],reverse=True)
    axis_gap=win[0]-win[1]
    major_missing=sum(1 for x in completeness.get('missing_codes',[]) if x not in ('original_exhibition_pending','exhibition_pending'))
    top_s=scenarios[0]['probability'] if scenarios else 0
    if axis_gap>=.12 and top_s>=.16 and ticket_count<=6 and major_missing==0: grade='S'
    elif axis_gap>=.06 and ticket_count<=9 and major_missing<=1: grade='A'
    else: grade='B'
    confidence=max(0,min(100,round(45+axis_gap*150+top_s*60-major_missing*8-ticket_count,1)))
    return {'grade':grade,'confidence':confidence,'axis_gap':round(axis_gap,4),'top_scenario_probability':round(top_s,4)}
