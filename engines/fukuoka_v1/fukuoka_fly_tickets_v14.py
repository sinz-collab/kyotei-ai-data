"""Reference reproduction of the Fukuoka fly-only 10-ticket v1.4 prototype.

IMPORTANT: Reference code, not a fitted or independently validated betting model.
Existing win probabilities are inputs; no odds/results are used.
No mutation of input. The prototype assumed standard entry (boat number = course).
For changed entry it deliberately raises rather than silently using wrong courses.
"""
from __future__ import annotations

from typing import Any
import math

GRADE = {'A1': 3, 'A2': 2, 'B1': 1, 'B2': 0}


def num(value: Any) -> float:
    try:
        x = float(value)
        return x if math.isfinite(x) else 0.0
    except (ValueError, TypeError):
        return 0.0


def tickets_v14(race: dict) -> dict:
    racers = {int(z['lane']): z for z in race.get('racers', []) if isinstance(z, dict) and str(z.get('lane', '')).isdigit()}
    if set(racers) != set(range(1, 7)):
        raise ValueError('six_racers_required')
    win = (race.get('prediction') or {}).get('win') or {}
    if any(str(i) not in win for i in range(1, 7)):
        raise ValueError('existing_win_probabilities_required')
    live = race.get('live') or {}
    direct = live.get('direct') or {}
    entry = direct.get('actual_entry')
    if entry and list(map(int, entry)) != [1, 2, 3, 4, 5, 6]:
        raise ValueError('variable_entry_not_supported_by_original_v14')
    if any(int(racers[i].get('actual_course') or i) != i for i in range(1, 7)):
        raise ValueError('variable_entry_not_supported_by_original_v14')
    exhibition = {int(z['lane']): z for z in (live.get('exhibition') or {}).get('entries', []) if isinstance(z, dict) and str(z.get('lane', '')).isdigit()}

    def grade(i: int) -> int:
        return GRADE.get(str(racers[i].get('class') or ''), 0)

    def st(i: int) -> float:
        return num(racers[i].get('avg_st'))

    def quality(i: int) -> float:
        z = racers[i]
        return num(z.get('nat_win')) + 0.65 * grade(i) + num(z.get('motor_2')) / 35 - (st(i) - 0.16) * 14

    def attack(i: int) -> float:
        z = racers[i]
        return max(num(z.get('boaters_sashi_rate')) * (1 if i == 2 else 0.35),
                   num(z.get('boaters_makuri_rate')) * (0.2 if i == 2 else 1),
                   num(z.get('boaters_makuri_sashi_rate')))

    heads = []
    for i in range(2, 7):
        score = num(win[str(i)]) * 0.5 + quality(i) + attack(i) * 0.45
        if i in exhibition:
            own = exhibition[i]
            inner = exhibition.get(i - 1) or exhibition.get(1) or {}
            score += max(-6.0, min(4.0, (num(inner.get('start_time')) - num(own.get('start_time'))) * 14))
            score += (6.93 - num(own.get('exhibition_time'))) * 4
            score += (grade(i) - 1) * 1.5
            if num(own.get('start_time')) > 0.20:
                score -= 4
        heads.append((i, score))
    # Python stable sort reproduces JS stable sort; original order is boat 2 through 6.
    heads.sort(key=lambda z: -z[1])
    main, alternate = heads[0][0], heads[1][0]

    def link(i: int) -> float:
        return quality(i) + (1.6 if i == main + 1 else 0) + (0.5 if i == main + 2 else 0) - (1.2 if i >= main + 3 else 0)

    followers = [i for i in range(2, 7) if i != main]
    followers.sort(key=lambda i: -link(i))
    tickets: list[str] = []

    def add(h: int, j: int, k: int) -> None:
        if len({h, j, k}) == 3:
            t = f'{h}-{j}-{k}'
            if t not in tickets:
                tickets.append(t)

    focus = followers[:3]
    for i in focus:
        add(main, 1, i)
        add(main, i, 1)
    for i in focus:
        for j in focus:
            if len(tickets) >= 8:
                break
            add(main, i, j)
    for i in [1, main, *followers]:
        for j in [main, 1, *followers]:
            if len(tickets) >= 10:
                break
            add(alternate, i, j)
    tickets = tickets[:10]
    if len(tickets) != 10 or len(set(tickets)) != 10 or any(t[0] == '1' for t in tickets):
        raise RuntimeError('invalid_tickets')
    return {'version': 'fukuoka_fly_tickets_v1.4_reference',
            'mainHead': main, 'alternateHead': alternate,
            'Main6': tickets[:6], 'Zure2': tickets[6:8], 'Ana2': tickets[8:10],
            'tickets': tickets, 'stage': 'live' if len(exhibition) == 6 else 'morning',
            'oddsUsed': False, 'resultsUsed': False}
