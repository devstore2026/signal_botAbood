"""Explicit parameters for the M1/60s chess scanner."""
from pathlib import Path
import json
import os
from dotenv import load_dotenv
ROOT=Path(__file__).resolve().parent
load_dotenv(ROOT/'.env',override=False)
BOT_TOKEN=os.getenv('BOT_TOKEN','').strip()
PO_WS_URL=os.getenv('PO_WS_URL','wss://demo-api-eu.po.market/socket.io/?EIO=4&transport=websocket').strip()
PO_AUTH_FRAME=os.getenv('PO_AUTH_FRAME','').strip()
def number(key,default,lo,hi,integer=False):
    x=float(os.getenv(key,str(default)))
    if not lo<=x<=hi or (integer and int(x)!=x):
        raise ValueError(f'Invalid {key}; expected {lo}..{hi}')
    return int(x) if integer else x
def boolean(key,default=False):
    return os.getenv(key,str(default)).strip().lower() in ('true','1','yes')
def ids(key):
    return {int(v.strip()) for v in os.getenv(key,'').split(',') if v.strip()}
ALERT_CHAT_IDS=ids('ALERT_CHAT_IDS')
ALLOWED_USER_IDS=ids('ALLOWED_USER_IDS')
SCAN_MODE=os.getenv('SCAN_MODE','PRE_CLOSE').upper()
if SCAN_MODE not in ('PRE_CLOSE','CLOSED'):
    raise ValueError('SCAN_MODE must be PRE_CLOSE or CLOSED')
SCAN_SECOND=number('SCAN_SECOND',57,57,58,True)
SEND_DEADLINE_SECOND=number('SEND_DEADLINE_SECOND',59,58,59)
if SEND_DEADLINE_SECOND<=SCAN_SECOND:
    raise ValueError('Deadline must follow scan second')
MIN_PAYOUT=number('MIN_PAYOUT',85,85,100)
MIN_CONFIDENCE=number('MIN_CONFIDENCE',80,80,100)
MAX_SIGNALS_PER_MINUTE=number('MAX_SIGNALS_PER_MINUTE',1,1,50,True)
PAYOUT_TTL=number('PAYOUT_TTL',120,5,600)
MAX_TICK_AGE=number('MAX_TICK_AGE',2,0.1,5)
MAX_TICK_GAP=number('MAX_TICK_GAP',5,0.1,10)
MAX_OPEN_DELAY=number('MAX_OPEN_DELAY',2,0.1,5)
MAX_CLOSE_DELAY=number('MAX_CLOSE_DELAY',2,0.1,5)
MIN_CANDLE_TICKS=number('MIN_CANDLE_TICKS',10,3,10000,True)
CANDLE_HISTORY=number('CANDLE_HISTORY',180,40,1440,True)
GENERIC_PAYOUT_IS_M1=boolean('GENERIC_PAYOUT_IS_M1')
ASSET_ARRAY_MAP=json.loads(os.getenv('ASSET_ARRAY_MAP','{}'))
if not isinstance(ASSET_ARRAY_MAP,dict):
    raise ValueError('ASSET_ARRAY_MAP must be an object')
for key,value in ASSET_ARRAY_MAP.items():
    if key not in ('symbol','name','payout','active') or not isinstance(value,int) or value<0:
        raise ValueError('Invalid ASSET_ARRAY_MAP')
SUBSCRIBE_EVENT=os.getenv('SUBSCRIBE_EVENT','subscribeSymbol')
SUBSCRIBE_AS_OBJECT=boolean('SUBSCRIBE_AS_OBJECT')
SUBSCRIBE_PERIOD=number('SUBSCRIBE_PERIOD',60,60,60,True)
ASSET_REFRESH_EVENT=os.getenv('ASSET_REFRESH_EVENT','getAssets')
ASSET_REFRESH_SECONDS=number('ASSET_REFRESH_SECONDS',30,5,120)
NEWS_FILE=os.getenv('NEWS_FILE','').strip()
NEWS_MAX_AGE=number('NEWS_MAX_AGE',300,10,3600)
NEAR_LEVEL_FRACTION=number('NEAR_LEVEL_FRACTION',0.20,0.05,0.30)
MAX_BB_EXPANSION=number('MAX_BB_EXPANSION',1.15,1.0,1.5)
MAX_EMA_SLOPE_FRACTION=number('MAX_EMA_SLOPE_FRACTION',0.08,0.01,0.20)
