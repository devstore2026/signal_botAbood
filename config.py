"""Validated local configuration. Importing does not start any network call."""
from pathlib import Path
import os
from dotenv import load_dotenv
ROOT=Path(__file__).resolve().parent
# Render Environment takes precedence over optional local .env values.
load_dotenv(ROOT/'.env', override=False)
BOT_TOKEN=os.getenv('BOT_TOKEN','').strip()
PO_WS_URL=os.getenv('PO_WS_URL','wss://demo-api-eu.po.market/socket.io/?EIO=4&transport=websocket').strip()
PO_AUTH_FRAME=os.getenv('PO_AUTH_FRAME','').strip()
def number(key, default, lo, hi, integer=False):
    v=float(os.getenv(key,str(default)))
    if not lo <= v <= hi or (integer and int(v) != v):
        raise ValueError(f'{key} must be between {lo} and {hi}')
    return int(v) if integer else v
TICK_BUFFER_SIZE=number('TICK_BUFFER_SIZE',20000,100,100000,True)
MIN_TICKS=number('MIN_TICKS',80,40,TICK_BUFFER_SIZE,True)
MAX_TICK_AGE=number('MAX_TICK_AGE',2,0.2,10)
MIN_SCORE=number('MIN_SCORE',4,3,5,True)
MIN_EFFICIENCY=number('MIN_EFFICIENCY',0.30,0.05,0.95)
MIN_STATS_SAMPLE=number('MIN_STATS_SAMPLE',50,30,100000,True)
PAPER_MAX_GAP=number('PAPER_MAX_GAP',2,0.1,10)
SIGNAL_COOLDOWN=number('SIGNAL_COOLDOWN',5,1,60)
# No artificial wait is used; kept in .env for migration clarity only.
ANALYSIS_MIN = number('ANALYSIS_MIN', 1.0, 0.0, 10.0)
ANALYSIS_MAX = number('ANALYSIS_MAX', 3.0, 0.5, 10.0)
DATA_DIR=ROOT/'data'

# Experimental S5 model: engineering defaults, not statistically optimized.
S5_WINDOW_SECONDS=number('S5_WINDOW_SECONDS',20,8,120)
S5_MIN_SPAN=number('S5_MIN_SPAN',5,2,30)
S5_MIN_TICKS=number('S5_MIN_TICKS',30,8,1000,True)
S5_MIN_QUALITY=number('S5_MIN_QUALITY',65,0,100)
# False preserves continuous requests on valid data. True skips weak setups.
S5_STRICT_FILTER=os.getenv('S5_STRICT_FILTER','false').strip().lower() in ('true','1','yes')
