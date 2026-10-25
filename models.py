"""Asset metadata and real-tick minute candles. No fixed asset whitelist."""
from collections import deque
from dataclasses import dataclass
import math
import re
import time
import config

@dataclass(frozen=True)
class Asset:
    symbol:str
    name:str
    payout:float
    active:bool
    updated_at:float
    category:str='unknown'

class Registry:
    def __init__(self):
        self.assets={}
        self.last_update=0.0
        self.unmapped_rows=0
    @staticmethod
    def alias(value):
        return re.sub(r'[^a-z0-9]','',str(value).lower())
    def resolve(self,symbol):
        if symbol in self.assets:
            return symbol
        matches=[s for s,a in self.assets.items() if self.alias(symbol) in (self.alias(s),self.alias(a.name))]
        return matches[0] if len(matches)==1 else None
    @staticmethod
    def payout_for_m1(row):
        if 'payout60' in row:
            return row['payout60']
        values=row.get('payouts')
        if isinstance(values,dict):
            return values.get('60',values.get(60))
        if str(row.get('expiry',row.get('expiration',''))) in ('60','60.0'):
            return row.get('payout',row.get('profit'))
        if config.GENERIC_PAYOUT_IS_M1:
            return row.get('payout',row.get('profit'))
        return None
    def update(self,payload,now=None):
        now=time.time() if now is None else now
        def visit(node):
            if isinstance(node,dict):
                symbol=node.get('symbol',node.get('asset'))
                if isinstance(symbol,str) and symbol.strip():
                    symbol=symbol.strip()  # preserve raw server symbol, including non-FX assets
                    value=self.payout_for_m1(node)
                    active=node.get('active',node.get('isActive',node.get('available')))
                    # Unknown availability or payout is NOT treated as eligible.
                    try:
                        payout=float(value)
                        if not math.isfinite(payout) or not 0<=payout<=100:
                            raise ValueError
                        if active not in (True,False,0,1):
                            raise ValueError
                    except (ValueError,TypeError):
                        self.unmapped_rows+=1
                        # Invalidate prior metadata if the newest row cannot be understood.
                        self.assets.pop(symbol,None)
                        return
                    self.assets[symbol]=Asset(symbol,str(node.get('name',symbol)),payout,
                                              bool(active),now,str(node.get('type','unknown')))
                    self.last_update=now
                    return
                for value in node.values():
                    if isinstance(value,(dict,list)):
                        visit(value)
            elif isinstance(node,list):
                mapping=config.ASSET_ARRAY_MAP
                if mapping and all(v<len(node) for v in mapping.values()) and 'symbol' in mapping:
                    candidate={k:node[i] for k,i in mapping.items()}
                    if isinstance(candidate.get('symbol'),str) and not any(isinstance(v,(dict,list)) for v in candidate.values()):
                        if 'payout' in candidate and config.GENERIC_PAYOUT_IS_M1:
                            candidate['payout60']=candidate.pop('payout')
                        visit(candidate)
                        return
                # An unconfigured positional row is never guessed from numeric fields.
                if node and not isinstance(node[0],(dict,list)):
                    self.unmapped_rows+=1
                    return
                for value in node:
                    visit(value)
        visit(payload)
    def eligible(self,now=None):
        now=time.time() if now is None else now
        return {s:a for s,a in self.assets.items() if a.active and a.payout>=config.MIN_PAYOUT
                and 0<=now-a.updated_at<=config.PAYOUT_TTL}
    def clear(self):
        self.assets.clear()
        self.last_update=0.0

@dataclass
class Candle:
    start:int
    open:float
    high:float
    low:float
    close:float
    first_ts:float
    last_ts:float
    ticks:int=1
    max_gap:float=0.0
    @property
    def end(self):
        return self.start+60
    @property
    def body(self):
        return abs(self.close-self.open)
    @property
    def span(self):
        return self.high-self.low
    @property
    def color(self):
        return 'G' if self.close>self.open else ('R' if self.close<self.open else 'D')
    def valid(self,asof,provisional=False):
        # Require coverage of the candle opening and the sampling endpoint.
        end=min(asof,self.end) if provisional else self.end
        return (self.ticks>=config.MIN_CANDLE_TICKS and self.span>0 and
                self.first_ts-self.start<=config.MAX_OPEN_DELAY and
                0<=end-self.last_ts<=config.MAX_CLOSE_DELAY and
                self.max_gap<=config.MAX_TICK_GAP)

class CandleStore:
    def __init__(self):
        self.bars={}
    def ingest(self,symbol,ts,price,now=None):
        now=time.time() if now is None else now
        try:
            ts,price=float(ts),float(price)
        except (TypeError,ValueError):
            return False
        if ts>1e12:
            ts/=1000
        if not math.isfinite(ts) or not math.isfinite(price) or price<=0 or ts>now+1 or now-ts>5:
            return False
        q=self.bars.setdefault(symbol,deque(maxlen=config.CANDLE_HISTORY))
        bucket=int(ts//60)*60
        if q and (bucket<q[-1].start or ts<q[-1].last_ts):
            return False
        if not q or bucket>q[-1].start:
            q.append(Candle(bucket,price,price,price,price,ts,ts))
        else:
            bar=q[-1]
            if ts==bar.last_ts and price==bar.close:
                return False
            bar.max_gap=max(bar.max_gap,ts-bar.last_ts)
            bar.high=max(bar.high,price)
            bar.low=min(bar.low,price)
            bar.close=price
            bar.last_ts=ts
            bar.ticks+=1
        return True
    def window(self,symbol,now,mode=None):
        mode=mode or config.SCAN_MODE
        q=list(self.bars.get(symbol,()))
        if mode=='CLOSED':
            q=[b for b in q if b.end<=now]
        if len(q)<26:
            return [],'need_26_real_M1_candles'
        q=q[-26:]
        if any(b.start-a.start!=60 for a,b in zip(q,q[1:])):
            return [],'missing_candles'
        for i,b in enumerate(q):
            if not b.valid(now,provisional=(mode=='PRE_CLOSE' and i==len(q)-1)):
                return [],'incomplete_tick_coverage'
        if mode=='PRE_CLOSE' and q[-1].start!=int(now//60)*60:
            return [],'current_candle_missing'
        if mode=='CLOSED' and q[-1].end!=int(now//60)*60:
            return [],'closed_candle_stale'
        return q,''
    def clear(self):
        self.bars.clear()
