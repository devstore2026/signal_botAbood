"""strategy.py - Strengthened instant decision engine (Meta-Trading style).

ALWAYS returns UP or DOWN - never "no signal". Dense multi-indicator
confluence tuned for 5-second OTC scalps: EMA stack, slope, momentum,
acceleration, MACD histogram, RSI zone, Stochastic, and tick-order flow.
"""
import math
import random
import time
from dataclasses import dataclass
from typing import List, Tuple

import config

PROFILE = 'live_confluence_v2'

Tick = Tuple[float, float]


@dataclass(frozen=True)
class Decision:
    direction: str
    votes: int = 0
    rsi: float = 50.0


def ema(values, period):
    v = values[0]
    a = 2 / (period + 1)
    for x in values[1:]:
        v += a * (x - v)
    return v


def rsi(values, period=7):
    ch = [b - a for a, b in zip(values, values[1:])][-period:]
    g = sum(max(d, 0) for d in ch)
    l = sum(max(-d, 0) for d in ch)
    if l == 0:
        return 100.0 if g else 50.0
    return 100 - 100 / (1 + g / l)


def _macd_hist(values, f=12, s=26, sig=9):
    if len(values) < s + 2:
        return 0.0
    line = ema(values, f) - ema(values, s)
    pts = [ema(values[:i + 1], f) - ema(values[:i + 1], s)
           for i in range(max(s, len(values) - sig), len(values))]
    return line - (sum(pts) / len(pts) if pts else line)


def _stoch(values, period=14):
    if len(values) < period:
        return 50.0
    w = values[-period:]
    lo, hi, last = min(w), max(w), w[-1]
    return 50.0 if hi == lo else (last - lo) / (hi - lo) * 100.0


def decide(ticks: List[Tick], duration_seconds: int, now=None) -> Decision:
    now = time.time() if now is None else now
    clean = []
    try:
        for t, p in ticks:
            t, p = float(t), float(p)
            if math.isfinite(t) and math.isfinite(p) and p > 0:
                clean.append((t, p))
    except (TypeError, ValueError):
        clean = []

    if len(clean) < 3:
        return Decision(random.choice(("UP", "DOWN")), 0, 50.0)

    prices = [p for _, p in clean]

    # ---- sparse data: quick multi-signal tick heuristic ----
    if len(clean) < config.MIN_TICKS:
        w = prices[-min(12, len(prices)):]
        vel = w[-1] - w[0]
        mid = len(w) // 2
        accel = (w[-1] - w[mid]) - (w[mid] - w[0])
        ef, es = ema(w, 3), ema(w, 8)
        # order flow: net up vs down tick count
        ups = sum(1 for a, b in zip(w, w[1:]) if b > a)
        dns = sum(1 for a, b in zip(w, w[1:]) if b < a)
        flow = ups - dns
        score = ((1 if vel > 0 else -1 if vel < 0 else 0)
                 + (1 if accel > 0 else -1 if accel < 0 else 0)
                 + (1 if ef > es else -1 if ef < es else 0)
                 + (1 if flow > 0 else -1 if flow < 0 else 0))
        if score == 0:
            score = 1 if prices[-1] >= prices[-2] else -1
        d = "UP" if score > 0 else "DOWN"
        return Decision(d, min(abs(score), 4), rsi(prices))

    # ---- full confluence (8 confirmations) ----
    width = max(1, int(duration_seconds) // 10)
    buckets = {}
    cur = int(now // width)
    for t, p in clean:
        b = int(t // width)
        if b < cur:
            buckets[b] = p
    bars = sorted(buckets.items())[-80:]
    closes = [p for _, p in bars] if len(bars) >= 8 else prices[-60:]

    e5, e13, e34 = ema(closes, 5), ema(closes, 13), ema(closes, 34)
    direction = "UP" if e13 >= e34 else "DOWN"
    s = 1 if direction == "UP" else -1
    mom = closes[-1] - closes[min(5, len(closes) - 1)]
    mi = len(closes) // 2
    accel = (closes[-1] - closes[mi]) - (closes[mi] - closes[0])
    rv = rsi(closes)
    macd_h = _macd_hist(closes)
    sto = _stoch(closes)
    ups = sum(1 for a, b in zip(closes, closes[1:]) if b > a)
    dns = sum(1 for a, b in zip(closes, closes[1:]) if b < a)
    flow = ups - dns

    checks = [
        s * (e5 - e13) > 0,
        s * (e13 - e34) > 0,
        s * (e13 - ema(closes[:-3], 13)) > 0 if len(closes) > 16 else False,
        s * mom > 0,
        s * accel > 0,
        s * macd_h > 0,
        (50 < rv < 85) if s == 1 else (15 < rv < 50),
        (sto > 50) if s == 1 else (sto < 50),
    ]
    votes = sum(checks)
    if s * flow < 0:  # order-flow disagreement trims one vote
        votes = max(0, votes - 1)
    return Decision(direction, votes, rv)
