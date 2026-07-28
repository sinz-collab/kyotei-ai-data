from itertools import permutations

def generate_tickets(boats, scenarios, max_tickets=8):
    probs={int(b['boat_no']):b for b in boats}; out=[]
    # scenario-linked approximation: joint score by positional probabilities and scenario support.
    scen_head={}
    for s in scenarios[:4]:
        scen_head[s['id']]=s['probability']
    for a,b,c in permutations(range(1,7),3):
        pa=probs[a]['win_prob']; pb=probs[b]['second_prob']; pc=probs[c]['third_prob']
        link=1.0
        if a==3 and b in (4,5,6): link*=1.18
        if a==4 and b in (5,6): link*=1.18
        if a in (2,3,4) and b==1: link*=1.10
        if c in (5,6): link*=1.06
        out.append({'combination':f'{a}-{b}-{c}','score':pa*pb*pc*link,'type':'main'})
    out=sorted(out,key=lambda x:x['score'],reverse=True)[:max_tickets]
    total=sum(x['score'] for x in out) or 1
    for i,x in enumerate(out): x['share']=round(x['score']/total,4); x['type']='main' if i<4 else ('deviation' if i<6 else 'upset')
    return out
