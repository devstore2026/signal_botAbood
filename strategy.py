"""Experimental time-aware S5 confluence. Not a calibrated win probability.
All other expiries delegate to the supplied original strategy unchanged.
No random prices, random directions, or forced alternation in the S5 path.
"""
import math
import statistics
import time
from dataclasses import dataclass
import config
import strategy_legacy as legacy

# Separate measurements whenever strategy thresholds/filter mode change.
import hashlib
_signature = repr((config.S5_WINDOW_SECONDS, config.S5_MIN_SPAN,
                   config.S5_MIN_TICKS, config.S5_MIN_QUALITY,
                   config.S5_STRICT_FILTER, config.MIN_EFFICIENCY,
                   config.MAX_TICK_AGE, config.ANALYSIS_MIN, config.ANALYSIS_MAX))
PROFILE = 's5_timeaware_v3_' + hashlib.sha256(_signature.encode()).hexdigest()[:12]
Tick = tuple
ema = legacy.ema
rsi = legacy.rsi


@dataclass(frozen=True)
class Decision:
    direction: str
    votes: int = 0
    rsi: float = 50.0
    quality: float = 0.0  # engineering agreement score, NEVER a probability
    eligible: bool = False
    reason: str = ''


def _clean(ticks, now, window):
    # Duplicated timestamps: deterministic last value wins. Future data excluded.
    clean = {}
    for row in ticks:
        try:
            ts, price = float(row[0]), float(row[1])
        except (ValueError, TypeError, IndexError, KeyError):
            continue
        if (math.isfinite(ts) and math.isfinite(price) and price > 0 and
                now - window <= ts <= now):
            clean[ts] = price
    return sorted(clean.items())


def _time_ema(ticks, tau):
    # Irregular intervals: the weight depends on elapsed seconds, not tick count.
    result = ticks[0][1]
    for (t0, _), (t1, p1) in zip(ticks, ticks[1:]):
        result += (-math.expm1(-(t1 - t0) / tau)) * (p1 - result)
    return result


def _slope(ticks):
    if len(ticks) < 3:
        return 0.0
    times = [t - ticks[0][0] for t, _ in ticks]
    prices = [p for _, p in ticks]
    tm, pm = statistics.mean(times), statistics.mean(prices)
    denom = sum((t - tm)**2 for t in times)
    return sum((t - tm)*(p - pm) for t, p in zip(times, prices)) / denom if denom else 0.0


def _sign(x):
    return 1 if x > 0 else -1 if x < 0 else 0


def decide(ticks, duration_seconds, now=None):
    if duration_seconds != 5:
        return legacy.decide(ticks, duration_seconds, now=now)
    now = time.time() if now is None else now
    points = _clean(ticks, now, config.S5_WINDOW_SECONDS)
    if (len(points) < config.S5_MIN_TICKS or
            points[-1][0] < now - config.MAX_TICK_AGE or
            points[-1][0] - points[0][0] < config.S5_MIN_SPAN):
        return Decision('UP', reason='DATA: insufficient fresh time-spanning ticks')
    # Equal-time aggregation prevents a burst of ticks from dominating a feature.
    bins = {}
    for ts, price in points:
        bins[int(ts * 4)] = (ts, price)
    points = list(bins.values())
    if len(points) < 8:
        return Decision('UP', reason='DATA: too few independent time buckets')
    end = points[-1][0]
    short = [x for x in points if x[0] >= end - 2]
    medium = [x for x in points if x[0] >= end - 5]
    long = [x for x in points if x[0] >= end - 12]
    if len(short) < 3 or len(medium) < 5:
        return Decision('UP', reason='DATA: gaps in recent tick windows')
    if max(b[0] - a[0] for a, b in zip(medium, medium[1:])) > config.MAX_TICK_AGE:
        return Decision('UP', reason='DATA: recent feed gap exceeds tolerance')

    sf, sm, sl = _slope(short), _slope(medium), _slope(long)
    ef, es = _time_ema(points, 0.8), _time_ema(points, 2.2)
    changes = [b[1] - a[1] for a, b in zip(medium, medium[1:])]
    path = sum(abs(x) for x in changes)
    net = medium[-1][1] - medium[0][1]
    efficiency = abs(net) / path if path else 0.0
    # These are uptick/downtick counts, NOT actual market order flow/volume.
    persistence = sum(_sign(x) for x in changes) / max(1, len(changes))
    old = [x for x in points if end - 4 <= x[0] <= end - 2]
    acceleration = sf - _slope(old)
    rv = rsi([p for _, p in medium], 7)
    scale = max(statistics.mean(abs(x) for x in changes), points[-1][1] * 1e-12)
    # Several horizons and features; correlated votes do NOT create independent evidence.
    components = [
        (2.0, _sign(sf)), (1.5, _sign(sm)), (0.75, _sign(sl)),
        (1.5, _sign(ef - es)), (1.0, _sign(persistence)),
        (0.5, _sign(acceleration)), (1.0, _sign(net)),
        (0.5, 1 if rv > 55 else -1 if rv < 45 else 0),
    ]
    score = sum(w * s for w, s in components)
    side = _sign(score) or _sign(net) or _sign(points[-1][1] - points[-2][1]) or 1
    votes = sum(s == side for _, s in components)
    agreement = abs(score) / sum(w for w, _ in components)
    shock = max((abs(x) for x in changes), default=0) > 6 * scale
    # A spike opposing a saturated trend is a caution, not a forced reversal.
    exhausted = ((side == 1 and rv > 85 and sf < sm) or
                 (side == -1 and rv < 15 and sf > sm))
    quality = 100 * (0.65 * agreement + 0.35 * efficiency)
    if shock:
        quality *= 0.65
    if exhausted:
        quality *= 0.8
    reasons = []
    if efficiency < config.MIN_EFFICIENCY:
        reasons.append('choppy recent path')
    if _sign(sf) != side or _sign(sm) != side:
        reasons.append('short/medium trend disagreement')
    if shock:
        reasons.append('single-tick shock')
    if exhausted:
        reasons.append('decelerating stretched trend')
    if quality < config.S5_MIN_QUALITY:
        reasons.append('confluence below experimental threshold')
    return Decision('UP' if side == 1 else 'DOWN', votes, rv, round(quality, 2),
                    not reasons, '; '.join(reasons) or 'time-aware confluence passed')
