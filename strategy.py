"""Experimental time-based M1 context + micro momentum, not a win predictor.
No random signals and no automatic alternation to fabricate variety.
"""
from dataclasses import dataclass
import math
import statistics
import time
import config
PROFILE='continuous_m1_micro_v4'

@dataclass(frozen=True)
class Decision:
    direction: str | None
    votes: int=0
    rsi: float=50.0
    reason: str=''
    source_ts: float=0.0
    minute_speed: float=0.0
    minute_volatility: float=0.0
    micro_speed: float=0.0
    mode: str='live_m1'
    data_age: float | None=None

def ema(values,period):
    value=values[0]
    alpha=2/(period+1)
    for x in values[1:]:
        value+=alpha*(x-value)
    return value

def rsi(values,period=7):
    changes=[b-a for a,b in zip(values,values[1:])][-period:]
    gains=sum(max(x,0) for x in changes)
    losses=sum(max(-x,0) for x in changes)
    return (100 if gains else 50) if losses==0 else 100-100/(1+gains/losses)

def slope(ticks):
    if len(ticks)<2:
        return 0.0
    # Actual seconds, not tick indices: quote arrival frequency can vary.
    origin=ticks[0][0]
    times=[t-origin for t,_ in ticks]
    prices=[p for _,p in ticks]
    tm,pm=statistics.fmean(times),statistics.fmean(prices)
    denom=sum((t-tm)**2 for t in times)
    return sum((t-tm)*(p-pm) for t,p in zip(times,prices))/denom if denom else 0.0

def bars(ticks,width,now):
    result={}
    current=int(now//width)
    for t,p in ticks:
        bucket=int(t//width)
        if bucket<current:
            result[bucket]=p
    return sorted(result.items())

def selective_decide(ticks,duration_seconds,now=None):
    now=time.time() if now is None else now
    def no(reason):
        return Decision(None,reason=reason)
    if len(ticks)<10:
        return no('Insufficient live quotes; no random direction')
    try:
        if any(not math.isfinite(t) or not math.isfinite(p) or p<=0 for t,p in ticks):
            return no('Invalid quote data')
        if any(b[0]<=a[0] for a,b in zip(ticks,ticks[1:])):
            return no('Unordered quote timestamps')
    except (TypeError,ValueError):
        return no('Invalid quote data')
    if not 0<=now-ticks[-1][0]<=config.MAX_TICK_AGE:
        return no('Live quotes are stale; check /status')
    # At least 60 seconds of actual observations for M1 context. No fake
    # minute candle made from five ticks. Completed bars only for EMA/RSI.
    if ticks[-1][0]-ticks[0][0]<60:
        return no('Collecting 60 seconds of real M1 context')
    minute=[x for x in ticks if now-60<=x[0]<=now]
    if len(minute)<20 or minute[-1][0]-minute[0][0]<55:
        return no('M1 context is incomplete')
    context=bars(minute,1,now)
    if len(context)<40:
        return no('Not enough observed seconds in M1 context')
    if any(b[0]-a[0]>3 for a,b in zip(context,context[1:])):
        return no('Quote gaps in M1 context')
    closes=[p for _,p in context]
    changes=[b-a for a,b in zip(closes,closes[1:])]
    nonzero=[abs(d) for d in changes if d]
    if not nonzero:
        return no('Flat market')
    typical=statistics.median(nonzero)
    if abs(changes[-1])>5*typical:
        return no('Isolated price spike; no chase entry')
    minute_speed=slope(minute)
    volatility=statistics.pstdev(changes)
    if duration_seconds<=30:
        width=max(3,min(10,int(duration_seconds)))
        micro=[x for x in ticks if now-width<=x[0]<=now]
        prior=[x for x in ticks if now-2*width<=x[0]<now-width]
        if len(micro)<5 or micro[-1][0]-micro[0][0]<width*0.65:
            return no('Not enough fresh micro-window quotes')
        micro_speed=slope(micro)
        prior_speed=slope(prior)
        points=[p for _,p in micro]
        efast,eslow=ema(points,3),ema(points,8)
        deltas=[b-a for a,b in zip(points,points[1:])]
        absolute=sum(abs(d) for d in deltas)
        efficiency=abs(points[-1]-points[0])/absolute if absolute else 0
        if efficiency<0.25:
            return no('Micro-window is too choppy')
        trend=ema(closes,5)-ema(closes,13)
        if trend==0 or micro_speed==0 or trend*micro_speed<=0:
            return no('M1 direction and micro momentum disagree')
        direction='UP' if micro_speed>0 else 'DOWN'
        sign=1 if direction=='UP' else -1
        # A collapse/reversal of local speed cannot be repaired by counting
        # more correlated indicators. Require stability instead.
        acceleration=micro_speed-prior_speed
        if sign*acceleration< -2*abs(micro_speed):
            return no('Micro momentum is decelerating sharply')
        rv=rsi(closes)
        flow=sum(1 if d>0 else -1 if d<0 else 0 for d in deltas)
        lo,hi=min(closes),max(closes)
        position=(points[-1]-lo)/(hi-lo) if hi>lo else 0.5
        checks=[sign*minute_speed>0,
                sign*micro_speed>0,
                sign*(efast-eslow)>0,
                sign*flow>0,
                position>0.55 if sign==1 else position<0.45,
                45<rv<85 if sign==1 else 15<rv<55]
    else:
        # Slower expiries use proportionate real time windows, not the same
        # handful of ticks as a five-second scalp.
        required=max(120,2*int(duration_seconds))
        if ticks[-1][0]-ticks[0][0]<required:
            return no(f'Need {required}s of source history for this expiry')
        width=max(5,int(duration_seconds)//12)
        slow=bars([x for x in ticks if now-required<=x[0]<=now],width,now)
        if len(slow)<24 or any(b[0]-a[0]>1 for a,b in zip(slow,slow[1:])):
            return no('Incomplete slow-timeframe bars')
        values=[p for _,p in slow]
        trend=ema(values,5)-ema(values,13)
        if not trend:
            return no('No slow-timeframe trend')
        direction='UP' if trend>0 else 'DOWN'
        sign=1 if direction=='UP' else -1
        rv=rsi(values)
        micro_speed=slope([(float(t*width),p) for t,p in slow[-6:]])
        recent=[b-a for a,b in zip(values[-7:],values[-6:])]
        checks=[sign*trend>0,sign*micro_speed>0,
                sign*(ema(values,13)-ema(values,21))>0,
                sign*minute_speed>0,
                sign*sum(recent)>0,
                45<rv<85 if sign==1 else 15<rv<55]
    votes=sum(checks)
    if votes<config.MIN_CONFIRMATIONS:
        return no(f'Indicators conflict ({votes}/6); no confirmed setup')
    return Decision(direction,votes,rv,'M1 context + time-based momentum',
                    ticks[-1][0],minute_speed,volatility,micro_speed)


def decide(ticks,duration_seconds,now=None,last_direction=None):
    """Always return a card direction, with explicit data quality.

    Full M1 confluence when valid; otherwise an uncalibrated short-window
    heuristic. With no data, return an explicitly DEMO-only placeholder,
    never fabricate market prices or claim the placeholder is an analysis.
    """
    now=time.time() if now is None else now
    valid={}
    for row in ticks or []:
        try:
            t,p=float(row[0]),float(row[1])
        except (TypeError,ValueError,IndexError,KeyError):
            continue
        if math.isfinite(t) and math.isfinite(p) and p>0 and t<=now:
            valid[t]=p
    clean=sorted(valid.items())
    default=last_direction if last_direction in ('UP','DOWN') else 'UP'
    if not clean:
        return Decision(default,reason='DEMO ONLY: no source prices; direction is not market analysis',
                        mode='demo',data_age=None)
    age=now-clean[-1][0]
    if len(clean)<2:
        return Decision(default,reason='One quote cannot determine movement; illustrative direction',
                        source_ts=clean[-1][0],mode='demo',data_age=age)
    if age<=config.MAX_TICK_AGE:
        full=selective_decide(clean,duration_seconds,now)
        if full.direction is not None:
            from dataclasses import replace
            return replace(full,mode='live_m1',data_age=age)
    # Evaluate relative to LAST SOURCE TIME, never stamp old quotes as new.
    end=clean[-1][0]
    long=[x for x in clean if x[0]>=end-max(60,min(int(duration_seconds),1800))]
    short=[x for x in long if x[0]>=end-max(3,min(int(duration_seconds),30))]
    if len(short)<2:
        short=long[-min(10,len(long)):]
    points=[p for _,p in short]
    deltas=[b-a for a,b in zip(points,points[1:])]
    if not any(deltas):
        return Decision(default,reason='Flat quotes: no directional evidence; illustrative direction',
                        source_ts=end,mode='flat',data_age=age)
    fast_slope=slope(short)
    slow_slope=slope(long)
    ef,es=ema(points,3),ema(points,8)
    flow=sum(1 if d>0 else -1 if d<0 else 0 for d in deltas)
    rv=rsi([p for _,p in long])
    # Simple votes are NOT probability estimates. Short-window evidence is
    # weighted more heavily for 5-second cards, context more for long expiry.
    signals=[fast_slope,ef-es,flow,points[-1]-points[0],slow_slope,rv-50]
    weights=[3,2,1,2,1,1] if duration_seconds<=30 else [1,1,1,1,4,2]
    signed=[1 if x>0 else -1 if x<0 else 0 for x in signals]
    score=sum(w*v for w,v in zip(weights,signed))
    if score==0:
        score=1 if next((d for d in reversed(deltas) if d),0)>0 else -1
    direction='UP' if score>0 else 'DOWN'
    side=1 if direction=='UP' else -1
    votes=sum(v==side for v in signed)
    mode='live_short' if age<=config.MAX_TICK_AGE else 'cached'
    changes=[b[1]-a[1] for a,b in zip(long,long[1:])]
    vol=statistics.pstdev(changes) if len(changes)>1 else 0.0
    return Decision(direction,votes,rv,'Short-window EMA + velocity + tick imbalance + RSI',
                    end,slow_slope,vol,fast_slope,mode,age)
