"""Pure chess-pattern detection with hard filters and a transparent score.
Score 80 means 80/100 rule quality, NEVER an 80% probability of a winning trade.
"""
from dataclasses import dataclass
import statistics
import config

def ema_series(values,period=20):
    alpha=2/(period+1)
    out=[values[0]]
    for x in values[1:]:
        out.append(out[-1]+alpha*(x-out[-1]))
    return out

def rsi(values,period=7):
    ds=[b-a for a,b in zip(values,values[1:])][-period:]
    gains=sum(max(x,0) for x in ds)
    losses=sum(max(-x,0) for x in ds)
    return (100.0 if gains else 50.0) if losses==0 else 100-100/(1+gains/losses)

def bands(values):
    v=values[-20:]
    mean=statistics.mean(v)
    sd=statistics.pstdev(v)
    return mean-2*sd,mean+2*sd,4*sd

@dataclass(frozen=True)
class ChessSignal:
    symbol:str
    direction:str
    confidence:float
    points:dict
    support:float
    resistance:float
    wick_ratio:float
    ema_slope_ratio:float
    bb_expansion:float
    c1_start:int
    provisional:bool

@dataclass(frozen=True)
class Result:
    signal:ChessSignal | None
    reason:str

def detect_chess(symbol,candles,*,news_state='unknown',provisional=False):
    """candles ordered oldest->newest, >=26, C3,C2,C1 = last three.
    Timing/coverage/payout are checked by the worker before calling this pure
    mathematical detector. news_state must come from a verified calendar.
    """
    def reject(reason):
        return Result(None,reason)
    if len(candles)<26:
        return reject('insufficient_history')
    if any(b.start-a.start!=60 for a,b in zip(candles,candles[1:])):
        return reject('non_contiguous_history')
    c1=candles[-1]
    colors=''.join(c.color for c in candles[-3:])
    if colors not in ('RGR','GRG'):
        return reject('no_three_candle_alternation')
    direction='UP' if colors=='RGR' else 'DOWN'
    prior=candles[-6:-1]  # five preceding candles, NOT including C1
    mean_body=statistics.mean(c.body for c in prior)
    if mean_body<=0 or c1.body>1.7*mean_body:
        return reject('body_expansion')
    if c1.span<=0:
        return reject('zero_range')
    upper=c1.high-max(c1.open,c1.close)
    lower=min(c1.open,c1.close)-c1.low
    wick=(lower if direction=='UP' else upper)/c1.span
    if wick<0.15:
        return reject('wick_rejection_below_15pct')
    # Flat EMA20 normalized to ATR-like mean range over recent closed candles.
    closes=[c.close for c in candles]
    mean_range=statistics.mean(c.span for c in candles[-20:])
    if mean_range<=0:
        return reject('flat_zero_prices')
    em=ema_series(closes,20)
    slope=abs(em[-1]-em[-4])/3/mean_range
    if slope>config.MAX_EMA_SLOPE_FRACTION:
        return reject('ema20_not_flat')
    lo,hi,width=bands(closes)
    previous_widths=[bands(closes[:-k])[2] for k in range(1,6)]
    baseline=statistics.mean(previous_widths)
    if baseline<=0 or width<=0:
        return reject('bollinger_zero_width')
    expansion=width/baseline
    if expansion>config.MAX_BB_EXPANSION:
        return reject('bollinger_expansion')
    history=candles[-21:-1]
    support=min(c.low for c in history)
    resistance=max(c.high for c in history)
    market_range=resistance-support
    if market_range<=0:
        return reject('zero_support_resistance_range')
    if c1.low<support-0.1*mean_range or c1.high>resistance+0.1*mean_range:
        return reject('range_breakout')
    # C1 must close near the correct structural edge, not merely somewhere
    # inside the Bollinger envelope. Band contact provides an alternative edge.
    level=support if direction=='UP' else resistance
    band_edge=lo if direction=='UP' else hi
    structural_distance=abs(c1.close-level)
    band_distance=abs(c1.close-band_edge)
    tolerance=config.NEAR_LEVEL_FRACTION*market_range
    distance=min(structural_distance,band_distance)
    if distance>tolerance:
        return reject('not_near_support_or_resistance')
    rv=rsi(closes)
    window=candles[-14:]
    low=min(c.low for c in window)
    high=max(c.high for c in window)
    stochastic=50 if high==low else 100*(c1.close-low)/(high-low)
    # In a range, avoid extreme persistent oscillator states characteristic
    # of an emerging trend. This is a rule, not a reliability guarantee.
    if not (20<=rv<=80 and 10<=stochastic<=90):
        return reject('oscillators_outside_range')
    if news_state=='blocked':
        return reject('news_blackout')
    rejection_points=25*min(1.0,wick/0.30)
    touch_points=20*(1-0.25*min(1.0,distance/tolerance))
    oscillation_points=(7.5*max(0,1-abs(rv-50)/50)+
                        7.5*max(0,1-abs(stochastic-50)/50))
    # Quiet realized volatility is necessary but cannot prove NO NEWS.
    news_points=10.0 if news_state=='clear' else 0.0
    points={'pattern':30.0,'rejection':rejection_points,'touch':touch_points,
            'oscillators':oscillation_points,'verified_news':news_points}
    score=round(sum(points.values()),2)
    if score<config.MIN_CONFIDENCE:
        return reject('score_below_threshold')
    signal=ChessSignal(symbol,direction,score,points,support,resistance,wick,
                       slope,expansion,c1.start,provisional)
    return Result(signal,'qualified')
