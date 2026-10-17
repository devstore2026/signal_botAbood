"""Forward-only paper measurements on delivered signals, not broker fills.
No historical fitting and no claim of execution, profit, or calibrated accuracy.
Stores JSONL asynchronously and rejects missing/late expiry observations.
"""
import asyncio
from collections import defaultdict
import json
import logging
import math
from pathlib import Path
import time
import uuid
import config
from strategy import PROFILE
log=logging.getLogger('paper')

def wilson(wins,n):
    if n==0:
        return (0.0,1.0)
    z=1.96
    p=wins/n
    denominator=1+z*z/n
    center=(p+z*z/(2*n))/denominator
    half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/denominator
    return max(0,center-half),min(1,center+half)

class PaperBook:
    def __init__(self,path=None):
        self.path=Path(path or config.DATA_DIR/'paper_results.jsonl')
        self.pending={}
        self.counts=defaultdict(lambda:{'win':0,'loss':0,'draw':0,'invalid':0})
        self.queue=asyncio.Queue(maxsize=10000)
        self.last={}
        self.ids=set()
        self.storage_ok=True
    async def load(self):
        def read():
            if not self.path.exists():
                return []
            rows=[]
            with self.path.open(encoding='utf-8') as f:
                for line in f:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
            return rows
        entries={}
        for row in await asyncio.to_thread(read):
            if not isinstance(row,dict) or row.get('profile')!=PROFILE:
                continue
            if row.get('kind')=='entry':
                entries[row.get('id')]=row
            elif row.get('kind')=='result':
                self._count(row)
        # Never silently drop trials interrupted by a process restart.
        for rid,row in entries.items():
            if rid not in self.ids:
                result={**row,'kind':'result','outcome':'invalid',
                        'reason':'Process restarted before result was recorded',
                        'exit_ts':None,'exit_price':None}
                self._count(result)
                self.journal(result)
    def _count(self,row):
        rid=row.get('id')
        outcome=row.get('outcome')
        if rid in self.ids or outcome not in ('win','loss','draw','invalid'):
            return
        self.ids.add(rid)
        self.counts[(row['symbol'],int(row['duration']))][outcome]+=1
    def journal(self,row):
        try:
            self.queue.put_nowait(row)
        except asyncio.QueueFull:
            self.storage_ok=False
            log.error('Paper journal queue full; persistence incomplete')
    def record(self,symbol,duration,direction,tick,now=None):
        now=time.time() if now is None else now
        key=(symbol,int(duration))
        # Independent non-overlapping samples per asset/expiry configuration.
        if key in self.pending:
            return None
        ts,price=tick
        if not 0<=now-ts<=config.MAX_TICK_AGE or direction not in ('UP','DOWN'):
            return None
        row={'kind':'entry','id':uuid.uuid4().hex,'profile':PROFILE,
             'symbol':symbol,'duration':duration,'direction':direction,
             'entry_ts':now,'source_ts':ts,'entry_price':price,
             'expiry_ts':now+duration}
        self.pending[key]=row
        self.last[symbol]=(ts,price)
        self.journal(dict(row))
        return row['id']
    def finish(self,key,outcome,exit_ts=None,exit_price=None):
        row=self.pending.pop(key)
        row={**row,'kind':'result','outcome':outcome,
             'exit_ts':exit_ts,'exit_price':exit_price}
        self._count(row)
        self.journal(row)
    def tick(self,symbol,ts,price):
        prev=self.last.get(symbol)
        self.last[symbol]=(ts,price)
        for key,row in list(self.pending.items()):
            if key[0]!=symbol or ts<row['expiry_ts']:
                continue
            gap=config.PAPER_MAX_GAP
            if (ts-row['expiry_ts']>gap or prev is None or
                    row['expiry_ts']-prev[0]>gap or ts-prev[0]>gap):
                self.finish(key,'invalid',ts,price)
                continue
            delta=price-row['entry_price']
            if abs(delta)<=max(1e-12,abs(price)*1e-10):
                outcome='draw'
            else:
                outcome='win' if (delta>0)==(row['direction']=='UP') else 'loss'
            self.finish(key,outcome,ts,price)
    def summary(self,symbol,duration):
        c=self.counts[(symbol,int(duration))]
        n=c['win']+c['loss']+c['draw']
        lo,hi=wilson(c['win'],n)
        if n<config.MIN_STATS_SAMPLE:
            return f'Not established (paper n={n}/{config.MIN_STATS_SAMPLE})'
        return (f'{100*c["win"]/n:.1f}% observed PAPER win rate; n={n}; '
                f'95% interval {100*lo:.1f}-{100*hi:.1f}%')
    def text(self):
        lines=['Forward PAPER results — not broker execution or profit',
               'Draws included in denominator; invalid observations excluded.']
        for (sym,dur),c in sorted(self.counts.items()):
            lines.append(f'{sym} / {dur}s: W={c["win"]} L={c["loss"]} '
                         f'D={c["draw"]} invalid={c["invalid"]}\n'+self.summary(sym,dur))
        if not self.counts:
            lines.append('No completed live-data paper observations yet.')
        lines.append(f'Pending: {len(self.pending)} | journal OK: {self.storage_ok}')
        return '\n'.join(lines)
    async def writer(self):
        def append(row):
            self.path.parent.mkdir(parents=True,exist_ok=True)
            with self.path.open('a',encoding='utf-8') as f:
                f.write(json.dumps(row,separators=(',',':'))+'\n')
        while True:
            row=await self.queue.get()
            try:
                await asyncio.to_thread(append,row)
            except OSError:
                self.storage_ok=False
                log.error('Paper journal cannot be written; current stats may be volatile')
            finally:
                self.queue.task_done()
    async def monitor(self):
        while True:
            now=time.time()
            for key,row in list(self.pending.items()):
                if now>row['expiry_ts']+config.PAPER_MAX_GAP+1:
                    self.finish(key,'invalid')
            await asyncio.sleep(0.5)
