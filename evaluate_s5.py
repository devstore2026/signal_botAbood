"""Forward chronological replay. No fitting, no live account access.
CSV header: symbol,timestamp,price (epoch seconds in UTC).
Run: python evaluate_s5.py ticks.csv --latency 0.3 --max-gap 1
Evaluate a chronological held-out recording; NEVER shuffle ticks.
"""
import argparse
import bisect
import csv
import json
from collections import defaultdict
import math
import strategy
import strategy_legacy
from paper import wilson


def read_ticks(path):
    by_symbol = defaultdict(list)
    with open(path, encoding='utf-8-sig', newline='') as f:
        for line, row in enumerate(csv.DictReader(f), 2):
            try:
                sym = row['symbol'].strip()
                ts, price = float(row['timestamp']), float(row['price'])
                if not sym or not math.isfinite(ts) or not math.isfinite(price) or price <= 0:
                    raise ValueError('invalid value')
                if by_symbol[sym] and ts <= by_symbol[sym][-1][0]:
                    raise ValueError('timestamps must increase per symbol')
                by_symbol[sym].append((ts, price))
            except (KeyError, ValueError) as exc:
                raise ValueError(f'CSV line {line}: {exc}') from exc
    if not by_symbol:
        raise ValueError('Empty recording')
    return by_symbol


def replay(points, latency, max_gap, cooldown):
    counts = {key: dict(win=0, loss=0, draw=0, invalid=0, skipped=0)
              for key in ('original', 'new_continuous', 'new_filtered')}
    times = [t for t, _ in points]
    first = times[0]
    next_request = first + max(25, strategy.config.S5_MIN_SPAN)
    candidates = 0
    for i, (request_ts, _) in enumerate(points):
        if request_ts < next_request:
            continue
        # Same non-overlapping request schedule for ALL variants, even if filtered.
        next_request = request_ts + latency + 5 + cooldown
        candidates += 1
        past = points[max(0, bisect.bisect_left(times, request_ts-120)):i+1]
        new = strategy.decide(past, 5, now=request_ts)
        old = strategy_legacy.decide(past, 5, now=request_ts)
        arms = {'original': (old, len(past) >= strategy.config.MIN_TICKS),
                'new_continuous': (new, not new.reason.startswith('DATA:')),
                'new_filtered': (new, new.eligible)}
        # First quote at/after simulated delivery/execution, never before it.
        entry_target = request_ts + latency
        en = bisect.bisect_left(times, entry_target)
        target = entry_target + 5
        ex = bisect.bisect_left(times, target)
        valid = (en < len(points) and ex < len(points) and ex > en and
                 times[en]-entry_target <= max_gap and times[ex]-target <= max_gap and
                 all(b-a <= max_gap for a,b in zip(times[en:ex], times[en+1:ex+1])))
        for key, (decision, accepted) in arms.items():
            c = counts[key]
            if not accepted:
                c['skipped'] += 1
                continue
            if not valid:
                c['invalid'] += 1
                continue
            delta = points[ex][1]-points[en][1]
            if abs(delta) <= max(1e-12, abs(points[en][1])*1e-10):
                outcome = 'draw'
            else:
                outcome = 'win' if (delta > 0) == (decision.direction == 'UP') else 'loss'
            c[outcome] += 1
    for c in counts.values():
        n = c['win'] + c['loss'] + c['draw']
        lo, hi = wilson(c['win'], n)
        c.update(n=n, win_rate=c['win']/n if n else None,
                 wilson95=[lo, hi], request_coverage=(candidates-c['skipped'])/candidates if candidates else 0)
    return {'candidates': candidates, 'variants': counts}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('csv')
    p.add_argument('--latency', type=float, default=0.3)
    p.add_argument('--max-gap', type=float, default=1)
    p.add_argument('--cooldown', type=float, default=5)
    a = p.parse_args()
    if (not all(math.isfinite(x) for x in (a.latency,a.max_gap,a.cooldown)) or
            a.latency < 0 or a.max_gap <= 0 or a.cooldown < 0):
        p.error('latency/cooldown must be nonnegative and max-gap positive')
    data = read_ticks(a.csv)
    report = {'profile': strategy.PROFILE, 'latency_seconds': a.latency,
              'max_quote_gap': a.max_gap, 'note': 'QUOTE replay, not broker fills; no guarantee of future accuracy',
              'symbols': {s: replay(q, a.latency, a.max_gap, a.cooldown) for s,q in data.items()}}
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
