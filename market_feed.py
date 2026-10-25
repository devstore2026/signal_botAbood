"""Live quotes only. No random fallback, price fabrication, or trade orders.
The transport adapter is unofficial; supported event shapes are fixture-tested,
not certified against the user's account. /status diagnoses missing live ticks.
"""
import asyncio
import inspect
import json
import logging
import math
import time
from collections import deque
import websockets
import config
from assets import OTC_ASSETS
from protocol import Decoder
log=logging.getLogger('feed')

def norm(symbol):
    """Map any display/raw form to the clean platform symbol (e.g. EURUSD_otc)."""
    x = str(symbol).strip()
    x = x.replace('(OTC)', '_otc').replace('OTC', '_otc')
    x = x.replace('/', '').replace(' ', '').replace('-', '')
    if not x.lower().endswith('_otc'):
        x += '_otc'
    return x

class MarketFeed:
    def __init__(self):
        self.assets={symbol:name.replace(' OTC',' (OTC)') for name,symbol in OTC_ASSETS}
        self.ticks={}
        self.last_received={}
        self.requested=set()
        self.connected=False
        self.authenticated=False
        self.last_error=''
        self.last_event='none'
        self.decoder=Decoder()
        self.ws=None
        self._send_lock=asyncio.Lock()
        self.on_tick=None
    def get_ticks(self,symbol):
        return list(self.ticks.get(symbol,()))
    def menu(self):
        return sorted(self.assets.items(),key=lambda x:x[1])
    async def send(self,event,payload):
        async with self._send_lock:
            if self.ws is not None:
                await self.ws.send('42'+json.dumps([event,payload],separators=(',',':')))
    async def subscribe(self,symbol):
        if symbol not in self.assets:
            return
        if symbol in self.requested:
            return
        self.requested.add(symbol)
        if self.connected:
            await self._subscribe(symbol)
    async def _subscribe(self,symbol):
        # A known community convention; may need a captured sanitized fixture
        # for the user's region. Do not emit trading events or guessed prices.
        await self.send('subscribeSymbol',symbol)
        await self.send('changeSymbol',{'asset':symbol,'period':5})
    def _ingest(self,symbol,ts,price,now=None):
        now=time.time() if now is None else now
        symbol=norm(symbol)
        if '_otc' not in symbol.lower():
            return False
        try:
            ts,price=float(ts),float(price)
        except (TypeError,ValueError):
            return False
        if ts>1e12:
            ts/=1000
        if not math.isfinite(ts) or not math.isfinite(price) or price<=0:
            return False
        if ts > now + 5:
            ts = now  # clamp future-drifted server stamps
        q=self.ticks.setdefault(symbol,deque(maxlen=config.TICK_BUFFER_SIZE))
        if q and ts<=q[-1][0]:
            return False
        q.append((ts,price))
        self.last_received[symbol]=now
        if symbol not in self.assets:
            self.assets[symbol]=symbol.replace('_otc',' (OTC)')
        if self.on_tick:
            self.on_tick(symbol,ts,price)
        return True
    def _stream(self, node):
        """Extract price ticks from any updateStream shape into the deque."""
        if isinstance(node, list):
            if (len(node) >= 3 and isinstance(node[0], str)
                    and isinstance(node[1], (int, float))
                    and isinstance(node[2], (int, float))):
                self._ingest(node[0], node[1], node[2])
                return
            if (len(node) >= 2 and isinstance(node[0], str)
                    and isinstance(node[1], (int, float))):
                self._ingest(node[0], time.time(), node[1])
                return
            for item in node:
                self._stream(item)
        elif isinstance(node, dict):
            symbol = node.get('asset') or node.get('symbol') or node.get('active')
            price = None
            for pk in ('price', 'tick', 'value', 'rate', 'close'):
                if isinstance(node.get(pk), (int, float)):
                    price = node[pk]
                    break
            ts = node.get('timestamp', node.get('time', time.time()))
            if symbol is not None and price is not None:
                self._ingest(symbol, ts, price)
            else:
                for v in node.values():
                    if isinstance(v, (dict, list)):
                        self._stream(v)

    def _assets(self,node):
        if isinstance(node,dict):
            symbol=node.get('symbol',node.get('asset'))
            if isinstance(symbol,str) and symbol.lower().endswith('_otc'):
                self.assets[symbol]=str(node.get('name',symbol)).replace(' OTC',' (OTC)')
            for value in node.values():
                if isinstance(value,(dict,list)):
                    self._assets(value)
        elif isinstance(node,list):
            # Registry is best effort, never claimed to be the full market.
            symbol=next((s for s in node if isinstance(s,str) and s.lower().endswith('_otc')),None)
            if symbol:
                self.assets.setdefault(symbol,symbol.replace('_otc',' (OTC)'))
            for value in node:
                if isinstance(value,(dict,list)):
                    self._assets(value)
    def event(self,event):
        if not isinstance(event,list) or len(event)<2 or not isinstance(event[0],str):
            return
        name,payload=event[0],event[1]
        self.last_event=name[:50]
        if name=='successauth':
            self.authenticated=True
        elif name in ('updateStream','stream'):
            self._stream(payload)
        elif name=='updateAssets':
            self._assets(payload)
        elif name.lower() in ('error','failauth','errorauth'):
            self.last_error='Server rejected auth/request; check local session (not logged)'
    async def session(self):
        kwargs = {'ping_interval': None, 'max_size': 2**22, 'close_timeout': 5,
                  'origin': 'https://pocketoption.com', 'open_timeout': 15}
        key = 'additional_headers' if 'additional_headers' in inspect.signature(websockets.connect).parameters else 'extra_headers'
        kwargs[key] = {'User-Agent': 'Mozilla/5.0'}
        async with websockets.connect(config.PO_WS_URL, **kwargs) as ws:
            self.ws = ws
            self.decoder.reset()
            greeting = await asyncio.wait_for(ws.recv(), 10)
            if isinstance(greeting, bytes):
                greeting = greeting.decode('utf-8', errors='ignore')
            if not isinstance(greeting, str) or not greeting.startswith('0'):
                raise ConnectionError('Unexpected Engine.IO greeting')
            await ws.send('40')
            while True:
                msg = await asyncio.wait_for(ws.recv(), 10)
                if isinstance(msg, bytes):
                    continue
                if msg == '2':
                    await ws.send('3')
                elif msg.startswith('40'):
                    break
            self.connected = True
            if config.PO_AUTH_FRAME:
                try:
                    arr = json.loads(config.PO_AUTH_FRAME[2:])
                except ValueError:
                    raise ValueError('PO_AUTH_FRAME is not valid JSON - fix .env')
                if not isinstance(arr, list) or len(arr) < 2 or arr[0] != 'auth' or not isinstance(arr[1], dict):
                    raise ValueError('Invalid PO_AUTH_FRAME in .env')
                if arr[1].get('isDemo') not in (1, True):
                    raise ValueError('Signal-only build requires demo feed auth (isDemo=1)')
                await ws.send(config.PO_AUTH_FRAME)
            else:
                # Verified by live probe 2026-10-17: server sends NOTHING without auth.
                log.warning('PO_AUTH_FRAME EMPTY. Server stays silent without auth; '
                            'signals stay blocked. Paste your DEMO auth frame in .env.')
            for sym in tuple(self.requested):
                await self._subscribe(sym)
            self.last_error = ''
            silent_since = time.monotonic()
            warned = False
            async for frame in ws:
                if isinstance(frame, bytes):
                    events = self.decoder.feed(frame)
                elif frame == '2':
                    await ws.send('3')
                    continue
                elif frame == '41':
                    raise ConnectionError('Namespace disconnected')
                elif isinstance(frame, str):
                    events = self.decoder.feed(frame)
                else:
                    continue
                for event in events:
                    silent_since = time.monotonic()
                    before = self.authenticated
                    self.event(event)
                    if self.authenticated and not before:
                        for sym in tuple(self.requested):
                            await self._subscribe(sym)
                if (not warned and not config.PO_AUTH_FRAME
                        and time.monotonic() - silent_since > 12):
                    warned = True
                    log.warning('Feed connected but SILENT: auth frame required; signals blocked.')

    async def run(self):
        """Permanent reconnect loop; upstream errors never kill Telegram/HTTP."""
        if not config.PO_AUTH_FRAME:
            log.warning('PO_AUTH_FRAME is empty; a demo session may be required for live prices')
        while True:
            try:
                await self.session()
                self.last_error='Feed closed; retry in 3 seconds'
                log.warning('%s',self.last_error)
            except asyncio.CancelledError:
                raise  # let Render/aiogram perform a graceful shutdown
            except Exception as exc:
                # Do not log raw exception text: auth/session data may appear.
                self.last_error=f'Feed disconnected ({type(exc).__name__}); retry in 3 seconds'
                log.warning('%s',self.last_error)
            finally:
                self.connected=False
                self.authenticated=False
                self.ws=None
                self.decoder.reset()
                self.ticks.clear()
                self.last_received.clear()
            await asyncio.sleep(3)
