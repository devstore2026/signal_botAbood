"""Read-only Pocket Option transport adapter: all asset categories, M1 ticks.
No trade commands and no fabricated payout, news, candles or random prices.
Unofficial event schemas must be verified against the user's server.
"""
import asyncio
import inspect
import json
import logging
import math
import time
import websockets
import config
from models import Registry,CandleStore
from protocol import Decoder
log=logging.getLogger('feed')

class Feed:
    def __init__(self):
        self.registry=Registry()
        self.store=CandleStore()
        self.decoder=Decoder()
        self.connected=False
        self.authenticated=False
        self.last_event='none'
        self.last_error=''
        self.ws=None
        self.subscribed=set()
        self.last_tick={}
        self._send_lock=asyncio.Lock()
    async def send(self,event,payload):
        async with self._send_lock:
            if self.ws is None:
                return
            await self.ws.send('42'+json.dumps([event,payload],separators=(',',':')))
    def stream(self,node):
        if isinstance(node,list):
            if len(node)>=3 and isinstance(node[0],str) and isinstance(node[1],(int,float)) and isinstance(node[2],(int,float)):
                self.tick(node[0],node[1],node[2])
                return
            for value in node:
                self.stream(value)
        elif isinstance(node,dict):
            symbol=node.get('symbol',node.get('asset'))
            ts=node.get('timestamp',node.get('time'))
            price=node.get('price',node.get('value'))
            if symbol is not None and ts is not None and price is not None:
                self.tick(symbol,ts,price)
            else:
                for value in node.values():
                    if isinstance(value,(dict,list)):
                        self.stream(value)
    def tick(self,symbol,ts,price):
        symbol=self.registry.resolve(symbol)
        if symbol is None or symbol not in self.registry.eligible():
            return False
        if self.store.ingest(symbol,ts,price):
            ts=float(ts)
            if ts>1e12:
                ts/=1000
            self.last_tick[symbol]=ts
            return True
        return False
    def event(self,event):
        if not isinstance(event,list) or len(event)<2:
            return
        name,payload=event[:2]
        self.last_event=str(name)[:60]
        if name=='successauth':
            self.authenticated=True
        elif name=='updateAssets':
            self.registry.update(payload)
        elif name in ('updateStream','stream'):
            self.stream(payload)
        elif name in ('failauth','errorauth'):
            self.authenticated=False
            self.last_error='Authentication rejected; update local demo auth frame'
            raise ConnectionError('Authentication rejected')
    async def reconcile(self):
        """Subscribe all currently eligible assets; yield between frame sends."""
        last_refresh=0.0
        while True:
            now=time.time()
            if self.connected and self.authenticated:
                if now-last_refresh>=config.ASSET_REFRESH_SECONDS:
                    if config.ASSET_REFRESH_EVENT:
                        await self.send(config.ASSET_REFRESH_EVENT,{})
                    last_refresh=now
                eligible=self.registry.eligible(now)
                # No unsubscribe event is guessed. Ineligible ticks/scan windows
                # are ignored; clear their candles and resubscribe if needed.
                removed=self.subscribed-set(eligible)
                for sym in removed:
                    self.store.bars.pop(sym,None)
                    self.last_tick.pop(sym,None)
                self.subscribed.difference_update(removed)
                for symbol in sorted(set(eligible)-self.subscribed):
                    payload={'asset':symbol} if config.SUBSCRIBE_AS_OBJECT else symbol
                    await self.send(config.SUBSCRIBE_EVENT,payload)
                    await self.send('changeSymbol',{'asset':symbol,'period':60})
                    self.subscribed.add(symbol)
                    await asyncio.sleep(0.03)
            await asyncio.sleep(1)
    def auth_frame(self):
        raw=config.PO_AUTH_FRAME
        if not raw.startswith('42'):
            raise ValueError('PO_AUTH_FRAME must be the complete 42 auth frame')
        arr=json.loads(raw[2:])
        if not isinstance(arr,list) or len(arr)!=2 or arr[0]!='auth' or not isinstance(arr[1],dict):
            raise ValueError('Invalid PO_AUTH_FRAME')
        if arr[1].get('isDemo') not in (1,True):
            raise ValueError('Use demo authentication for this research scanner')
        return raw
    async def session(self):
        kwargs={'ping_interval':None,'max_size':2**23,'open_timeout':15,
                'close_timeout':5,'origin':'https://pocketoption.com'}
        key='additional_headers' if 'additional_headers' in inspect.signature(websockets.connect).parameters else 'extra_headers'
        kwargs[key]={'User-Agent':'Mozilla/5.0'}
        async with websockets.connect(config.PO_WS_URL,**kwargs) as ws:
            self.ws=ws
            greeting=await asyncio.wait_for(ws.recv(),10)
            if isinstance(greeting,bytes):
                greeting=greeting.decode()
            if not greeting.startswith('0'):
                raise ConnectionError('Invalid Engine.IO handshake')
            await ws.send('40')
            while True:
                frame=await asyncio.wait_for(ws.recv(),10)
                if frame=='2':
                    await ws.send('3')
                elif isinstance(frame,str) and frame.startswith('40'):
                    break
            self.connected=True
            await ws.send(self.auth_frame())
            async def receiver():
                while True:
                    frame=await asyncio.wait_for(ws.recv(),70)
                    if frame=='2':
                        await ws.send('3')
                    elif frame=='41':
                        raise ConnectionError('Namespace disconnected')
                    else:
                        for event in self.decoder.feed(frame):
                            self.event(event)
            tasks=[asyncio.create_task(receiver()),asyncio.create_task(self.reconcile())]
            try:
                done,_=await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    await task
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks,return_exceptions=True)
    async def run(self):
        while True:
            try:
                self.last_error=''
                await self.session()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Do not expose auth frames or raw payloads in logs.
                self.last_error=f'Feed reconnecting: {type(exc).__name__}; check demo auth/schema'
                log.warning('%s',self.last_error)
            finally:
                self.connected=False
                self.authenticated=False
                self.ws=None
                self.subscribed.clear()
                self.last_tick.clear()
                self.decoder.reset()
                self.registry.clear()
                self.store.clear()
            await asyncio.sleep(3)
