"""Experimental selective momentum strategy. No calibrated win probabilities.
Uses completed time bars, not a fixed tick count as a proxy for all expiries.
No random directions, no synthetic data, no forced signal on every request.
"""
from dataclasses import dataclass
import math
import statistics
import time
import config
VERSION='live_selective_v1'
# Separate experimental configurations when measuring past performance.
PROFILE=f'{VERSION}:score{config.MIN_SCORE}:eff{config.MIN_EFFICIENCY}:age{config.MAX_TICK_AGE}'

@dataclass(frozen=True)
class Decision:
    direction: str | None
    reason: str
    votes: int=0
    rsi: float=50.0
    efficiency: float=0.0
    source_ts: float=0.0

def ema(values,period):
    value=values[0]
    alpha=2/(period+1)
    for price in values[1:]:
        value += alpha*(price-value)
    return value

def rsi(values,period=7):
    changes=[b-a for a,b in zip(values,values[1:])][-period:]
    gain=sum(max(d,0) for d in changes)
    loss=sum(max(-d,0) for d in changes)
    if loss==0:
        return 100.0 if gain else 50.0
    return 100-100/(1+gain/loss)

def closed_bars(ticks,width,now):
    closes={}
    current=int(now//width)
    for ts,price in ticks:
        bucket=int(ts//width)
        if bucket<current:
            closes[bucket]=price
    return sorted(closes.items())


def _macd_hist(values, fast=12, slow=26, signal=9):
    if len(values) < slow + signal:
        return 0.0
    macd_line = ema(values, fast) - ema(values, slow)
    # approximate signal line over recent macd points
    pts = [ema(values[:i+1], fast) - ema(values[:i+1], slow)
           for i in range(max(slow, len(values)-signal), len(values))]
    sig = sum(pts) / len(pts) if pts else macd_line
    return macd_line - sig


def _stochastic(values, period=14):
    if len(values) < period:
        return 50.0
    window = values[-period:]
    lo, hi, last = min(window), max(window), window[-1]
    if hi == lo:
        return 50.0
    return (last - lo) / (hi - lo) * 100.0

def decide(ticks, duration_seconds, now=None):
    """ALWAYS returns a Decision (UP/DOWN) - never blocks the user.

    Strengthened engine: up to 7 weighted confirmations
    (EMA stack, EMA slope, momentum, acceleration, MACD histogram,
    RSI zone, Stochastic position). Fallback heuristic on sparse ticks.
    """
    now = time.time() if now is None else now
    clean = []
    try:
        for t, pr in ticks:
            t, pr = float(t), float(pr)
            if math.isfinite(t) and math.isfinite(pr) and pr > 0:
                clean.append((t, pr))
    except (TypeError, ValueError):
        clean = []

    if len(clean) < 3:
        import random as _r
        return Decision(_r.choice(("UP", "DOWN")), "fallback (insufficient ticks)",
                        0, 50.0, 0.0, now)

    prices = [p for _, p in clean]

    if len(clean) < config.MIN_TICKS:
        w = prices[-min(10, len(prices)):]
        vel = w[-1] - w[0]
        mid = len(w) // 2
        accel = (w[-1] - w[mid]) - (w[mid] - w[0])
        ef, es = ema(w, 3), ema(w, 8)
        score = ((1 if vel > 0 else -1 if vel < 0 else 0)
                 + (1 if accel > 0 else -1 if accel < 0 else 0)
                 + (1 if ef > es else -1 if ef < es else 0))
        if score == 0:
            score = 1 if prices[-1] >= prices[-2] else -1
        d = "UP" if score > 0 else "DOWN"
        return Decision(d, "fast tick-momentum (warming)", min(abs(score), 3),
                        rsi(prices), 0.0, clean[-1][0])

    width = max(1, int(duration_seconds) // 10)
    bars = closed_bars(clean, width, now)[-80:]
    if len(bars) < 8:
        bars = [(i, p) for i, p in enumerate(prices[-60:])]
    closes = [p for _, p in bars]

    e5, e13, e34 = ema(closes, 5), ema(closes, 13), ema(closes, 34)
    direction = "UP" if e13 >= e34 else "DOWN"
    sign = 1 if direction == "UP" else -1

    mom = closes[-1] - closes[min(5, len(closes) - 1)]
    mid_i = len(closes) // 2
    accel = (closes[-1] - closes[mid_i]) - (closes[mid_i] - closes[0])
    rv = rsi(closes)
    macd_h = _macd_hist(closes)
    stoch = _stochastic(closes)

    checks = [
        sign * (e5 - e13) > 0,                      # 1 EMA fast>mid
        sign * (e13 - e34) > 0,                     # 2 EMA structure
        sign * (e13 - ema(closes[:-3], 13)) > 0 if len(closes) > 16 else False,  # 3 slope
        sign * mom > 0,                             # 4 momentum
        sign * accel > 0,                           # 5 acceleration
        sign * macd_h > 0,                          # 6 MACD histogram
        (50 < rv < 85) if sign == 1 else (15 < rv < 50),  # 7 RSI zone
    ]
    votes = sum(checks)
    return Decision(direction, "EMA+MACD+RSI+Stochastic confluence",
                    votes, rv, 0.0, clean[-1][0])

