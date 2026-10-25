"""Deduplicate Telegram callback requests, NOT repeated price snapshots.
Two distinct clicks may legitimately use the same latest quotes. Unknown
network delivery results are never automatically replayed for the same click.
"""
import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
import time

class SignalStore:
    def __init__(self,path):
        self.path=Path(path)
    def connect(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        c=sqlite3.connect(self.path,timeout=5)
        c.execute('PRAGMA busy_timeout=5000')
        return c
    async def init(self):
        def work():
            c=self.connect()
            try:
                c.execute('PRAGMA journal_mode=WAL')
                with c:
                    c.execute('''CREATE TABLE IF NOT EXISTS card_requests (
                    id TEXT PRIMARY KEY, symbol TEXT, duration INTEGER,
                    status TEXT, created_at REAL, message_id INTEGER, payload TEXT)''')
                    c.execute('DELETE FROM card_requests WHERE created_at < ?',
                              (time.time()-30*86400,))
            finally:
                c.close()
        await asyncio.to_thread(work)
    async def reserve_request(self,callback_id,symbol,duration,payload):
        rid=hashlib.sha256(str(callback_id).encode()).hexdigest()[:32]
        def work():
            c=self.connect()
            try:
                with c:
                    cur=c.execute('INSERT OR IGNORE INTO card_requests VALUES (?,?,?,?,?,?,?)',
                                  (rid,symbol,duration,'pending',time.time(),None,json.dumps(payload)))
                    return rid if cur.rowcount else None
            finally:
                c.close()
        return await asyncio.to_thread(work)
    async def mark(self,rid,status,message_id=None):
        if status not in ('sent','uncertain'):
            raise ValueError('invalid delivery status')
        def work():
            c=self.connect()
            try:
                with c:
                    c.execute('UPDATE card_requests SET status=?,message_id=? WHERE id=?',
                              (status,message_id,rid))
            finally:
                c.close()
        await asyncio.to_thread(work)
