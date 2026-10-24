"""Atomic one-use signal ledger. Never deletes the quote history needed by EMA.
Source timestamp watermarks prevent replay; pending/uncertain deliveries are
not retried automatically because Telegram delivery is not transactional.
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
        conn=sqlite3.connect(self.path,timeout=5)
        conn.execute('PRAGMA busy_timeout=5000')
        return conn
    async def init(self):
        def work():
            with self.connect() as c:
                c.execute('PRAGMA journal_mode=WAL')
                c.executescript('''
                CREATE TABLE IF NOT EXISTS watermark (
                    profile TEXT, symbol TEXT, duration INTEGER, source_ts REAL,
                    PRIMARY KEY(profile,symbol,duration));
                CREATE TABLE IF NOT EXISTS signals (
                    id TEXT PRIMARY KEY, profile TEXT, symbol TEXT, duration INTEGER,
                    source_ts REAL, direction TEXT, status TEXT,
                    created_at REAL, delivered_at REAL, message_id INTEGER,
                    payload TEXT);
                ''')
                # Watermarks are retained even when old audit rows are pruned.
                c.execute('DELETE FROM signals WHERE created_at < ?', (time.time()-30*86400,))
        await asyncio.to_thread(work)
    async def reserve(self,profile,symbol,duration,source_ts,direction,payload):
        identity=f'{profile}|{symbol}|{duration}|{source_ts:.6f}'
        signal_id=hashlib.sha256(identity.encode()).hexdigest()[:24]
        def work():
            with self.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                row=c.execute('SELECT source_ts FROM watermark WHERE profile=? AND symbol=? AND duration=?',
                              (profile,symbol,duration)).fetchone()
                if row and source_ts<=row[0]:
                    return None
                c.execute('INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                          (signal_id,profile,symbol,duration,source_ts,direction,'pending',
                           time.time(),None,None,json.dumps(payload,separators=(',',':'))))
                c.execute('INSERT INTO watermark VALUES (?,?,?,?) ON CONFLICT(profile,symbol,duration) '
                          'DO UPDATE SET source_ts=excluded.source_ts',
                          (profile,symbol,duration,source_ts))
            return signal_id
        return await asyncio.to_thread(work)
    async def mark(self,signal_id,status,message_id=None):
        if status not in ('sent','uncertain'):
            raise ValueError('invalid delivery status')
        def work():
            with self.connect() as c:
                c.execute('UPDATE signals SET status=?,delivered_at=?,message_id=? WHERE id=?',
                          (status,time.time() if status=='sent' else None,message_id,signal_id))
        await asyncio.to_thread(work)
