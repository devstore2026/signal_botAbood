"""Optional trusted calendar snapshot. Missing/stale coverage = UNKNOWN.
This is an integration boundary, NOT an invented live news provider.
"""
import asyncio
import json
from pathlib import Path
import time
import config

class NewsGuard:
    def __init__(self):
        self.snapshot=None
    def state(self,symbol,entry,expiry,now=None):
        now=time.time() if now is None else now
        data=self.snapshot
        if not isinstance(data,dict) or not data.get('source'):
            return 'unknown'
        try:
            if not 0<=now-float(data['updated_at'])<=config.NEWS_MAX_AGE:
                return 'unknown'
            if float(data['covered_from'])>entry or float(data['covered_until'])<expiry:
                return 'unknown'
            symbols=data.get('covered_symbols',[])
            if symbol not in symbols and '*' not in symbols:
                return 'unknown'
            for item in data.get('blackouts',[]):
                if symbol in item['symbols'] or '*' in item['symbols']:
                    if float(item['start'])<=expiry and float(item['end'])>=entry:
                        return 'blocked'
            return 'clear'
        except (KeyError,TypeError,ValueError):
            return 'unknown'
    async def run(self):
        while True:
            if config.NEWS_FILE:
                try:
                    text=await asyncio.to_thread(Path(config.NEWS_FILE).read_text,encoding='utf-8')
                    self.snapshot=json.loads(text)
                except (OSError,ValueError):
                    self.snapshot=None
            await asyncio.sleep(5)
