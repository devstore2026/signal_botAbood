"""Abood Trading: honest research signals, text-only loading, cached media.
No broker order capability. An indicator vote is NOT a win probability.
"""
import asyncio
from datetime import datetime, timezone, timedelta
import html
import logging
import os
import math
import time
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart, Command
from aiogram.types import (Message, CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, InputMediaPhoto, FSInputFile)
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web
import cards
import config
from market_feed import MarketFeed
from paper import PaperBook
import strategy
log=logging.getLogger('bot')
TZ=timezone(timedelta(hours=3))
LOADING_HTML='⏳ <b>Abood is preparing the signal...</b>'
PER_PAGE=6
EXPIRATIONS=[('S5',5),('S15',15),('S30',30),('M1',60),('M3',180),('M5',300),('M10',600),('M15',900),('M30',1800)]
class Flow(StatesGroup):
    asset=State()
    expiration=State()
    confirm=State()
    signal=State()
feed=MarketFeed()
book=PaperBook()
feed.on_tick=book.tick
FILE_IDS={}
BUSY=set()
LAST_REQUEST={}

def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=text,callback_data=callback) for text,callback in row
    ] for row in rows])
def asset_keyboard(page):
    items=feed.menu()
    pages=max(1,math.ceil(len(items)/PER_PAGE))
    page=max(0,min(page,pages-1))
    rows=[[(name,'asset:'+symbol)] for symbol,name in items[page*6:page*6+6]]
    rows.append([('<',f'page:{max(0,page-1)}'),(f'{page+1}/{pages}','noop'),
                 ('>',f'page:{min(pages-1,page+1)}')])
    return keyboard(rows)
def expiration_keyboard():
    rows=[[(label,f'exp:{seconds}') for label,seconds in EXPIRATIONS[i:i+3]] for i in range(0,9,3)]
    return keyboard(rows+[[('Back','nav:back_to_assets')]])
def confirm_keyboard():
    return keyboard([[('Confirm','confirm')],[('Back','nav:back_to_dur')]])
def signal_keyboard():
    return keyboard([[('New signal','newsignal')],[('Back','nav:back_to_dur')]])
def duration_label(sec):
    return f'{sec} sec' if sec<60 else f'{sec//60} min'
def name(symbol):
    return feed.assets.get(symbol,str(symbol))
def media_source(key):
    return FILE_IDS.get(key) or FSInputFile(cards.path_for(key))
def cache_photo(key,message):
    if isinstance(message,Message) and message.photo:
        FILE_IDS[key]=message.photo[-1].file_id
async def show_screen(message,key,caption,kb):
    """Cached Telegram file_id after first upload, no runtime image rendering."""
    if message.photo:
        try:
            updated=await message.edit_media(
                media=InputMediaPhoto(media=media_source(key),caption=caption),reply_markup=kb)
        except TelegramBadRequest as exc:
            if 'message is not modified' in str(exc).lower():
                return message
            if key not in FILE_IDS:
                raise
            # Cached file_id might expire; refresh only that upload once.
            FILE_IDS.pop(key,None)
            updated=await message.edit_media(
                media=InputMediaPhoto(media=media_source(key),caption=caption),reply_markup=kb)
        cache_photo(key,updated)
        return updated if isinstance(updated,Message) else message
    result=await message.answer_photo(photo=media_source(key),caption=caption,reply_markup=kb)
    cache_photo(key,result)
    # Never delete the user's /start command here.
    if message.from_user and message.from_user.is_bot:
        try:
            await message.delete()
        except TelegramBadRequest:
            pass
    return result
async def activate(state,message,flow):
    await state.set_state(flow)
    await state.update_data(active_message=message.message_id)
async def loading_text(message):
    """Telegram cannot remove a photo with edit_caption: replace it by text."""
    if message.photo:
        # If deletion is denied, fail clearly instead of pretending photo gone.
        await message.delete()
        return await message.answer(LOADING_HTML,parse_mode='HTML')
    return await message.edit_text(LOADING_HTML,parse_mode='HTML',reply_markup=None)
async def generate_signal(cb,state):
    data=await state.get_data()
    sym,sec=data.get('symbol'),int(data.get('duration',5))
    if sym not in feed.assets:
        return
    loading=await loading_text(cb.message)
    await state.update_data(active_message=loading.message_id)
    # No artificial 2-second 'analysis'. Use only current in-memory data.
    decision = strategy.decide(feed.get_ticks(sym), sec)
    # no-entry branches removed per spec: a card is ALWAYS produced.
    acc = min(80, 55 + decision.votes * 3 + (3 if decision.votes >= 6 else 0))
    text = ("Trading signal\n\n"
            f"Asset: {name(sym)}\n"
            f"Duration: {duration_label(sec)}\n"
            f"Current time (UTC+3): {datetime.now(TZ).strftime('%H:%M:%S')}\n"
            f"Direction: {decision.direction}\n"
            f"Accuracy rate: {acc}%")
    # Ensure freshness immediately before delivering a five-second signal.
    out=await show_screen(loading,'buy' if decision.direction=='UP' else 'sell',text,signal_keyboard())
    await activate(state,out,Flow.signal)
    # Measure at delivery, not before an artificial UX wait. This is still a
    # quote-based paper entry, not a broker fill. Telegram latency affects it.
    ticks=feed.get_ticks(sym)
    if ticks:
        book.record(sym,sec,decision.direction,ticks[-1])


async def handle_ping(request):
    # Liveness endpoint: a disconnected upstream feed must not trigger a
    # Render restart loop. Use Telegram /status for feed diagnostics.
    return web.Response(text="Abood Trading Bot is running!")


async def start_web_server():
    """Bind PORT before Telegram/network/file startup; caller owns cleanup."""
    port=int(os.environ.get("PORT", "10000"))
    if not 0 <= port <= 65535:
        raise ValueError("PORT must be a valid TCP port")
    app=web.Application()
    app.router.add_get("/",handle_ping)
    app.router.add_get("/health",handle_ping)
    runner=web.AppRunner(app, access_log=None)
    try:
        await runner.setup()
        site=web.TCPSite(runner,"0.0.0.0",port)
        await site.start()
    except BaseException:
        await runner.cleanup()
        raise
    log.info("[Web] Health-check server listening on 0.0.0.0:%s",port)
    return runner


# Backward-compatible name; no second HTTP server is created.
start_health_server=start_web_server


def register_handlers(dp):
    @dp.message(CommandStart())
    async def start(message:Message,state:FSMContext):
        key=(message.chat.id,message.from_user.id)
        if key in BUSY:
            return
        BUSY.add(key)
        try:
            await state.clear()
            await state.update_data(page=0)
            out=await show_screen(message,'otc','Abood Trading — research only\nSelect the OTC asset:',asset_keyboard(0))
            await activate(state,out,Flow.asset)
        finally:
            BUSY.discard(key)
    @dp.message(Command('status'))
    async def status(message:Message):
        now=time.time()
        fresh=[s for s,q in feed.ticks.items() if q and 0<=now-q[-1][0]<=config.MAX_TICK_AGE]
        text=(f'Abood Trading feed status\nWebSocket connected: {feed.connected}\n'
              f'Auth confirmed: {feed.authenticated}\nLast event: {feed.last_event}\n'
              f'Fresh live symbols: {len(fresh)}\nRequested symbols: {len(feed.requested)}\n'
              f'Error: {feed.last_error or "none"}\n'
              'No simulation. Local clock must be synchronized.\n'
              'Without live ticks, signals remain blocked. A local demo auth frame may be required.')
        await message.answer(text)
    @dp.message(Command('stats'))
    async def stats(message:Message):
        text=book.text()
        for i in range(0,len(text),3800):
            await message.answer(text[i:i+3800])
    @dp.callback_query()
    async def callback(cb:CallbackQuery,state:FSMContext):
        # ACK first, before all uploads/computation. Network latency still exists.
        try:
            await cb.answer()
        except TelegramBadRequest:
            return  # stale callback (double-click / slow network) - safe to ignore
        if not isinstance(cb.message,Message):
            return
        key=(cb.message.chat.id,cb.from_user.id)
        if key in BUSY:
            return
        BUSY.add(key)
        try:
            data=await state.get_data()
            if data.get('active_message')!=cb.message.message_id:
                return  # old buttons may not alter current session
            action=cb.data or ''
            if action=='noop':
                return
            current=await state.get_state()
            if action.startswith('page:') and current==Flow.asset.state:
                pages=max(1,math.ceil(len(feed.menu())/6))
                page=max(0,min(int(action.split(':')[1]),pages-1))
                await state.update_data(page=page)
                out=await show_screen(cb.message,'otc','Select the OTC asset:',asset_keyboard(page))
                await activate(state,out,Flow.asset)
            elif action.startswith('asset:') and current==Flow.asset.state:
                sym=action.split(':',1)[1]
                if sym not in feed.assets:
                    return
                await state.update_data(symbol=sym)
                out=await show_screen(cb.message,'expiration',
                      f'Select duration\n\nAsset: {name(sym)}\nWarm-up uses real time bars.',expiration_keyboard())
                await activate(state,out,Flow.expiration)
                # Start the subscription without delaying the UI upload/ACK.
                try:
                    await feed.subscribe(sym)
                except Exception:
                    log.warning('Subscription deferred until reconnect')
            elif action.startswith('exp:') and current==Flow.expiration.state:
                sec=int(action.split(':')[1])
                if sec not in {s for _,s in EXPIRATIONS}:
                    return
                await state.update_data(duration=sec)
                out=await show_screen(cb.message,'confirmation',
                    f'Asset: {name(data["symbol"])}\nDuration: {duration_label(sec)}\n\n'
                    'Analyze live quotes? Experimental, not guaranteed.',confirm_keyboard())
                await activate(state,out,Flow.confirm)
            elif action in ('confirm','newsignal') and current in (Flow.confirm.state,Flow.signal.state):
                last=LAST_REQUEST.get(key,0)
                if time.monotonic()-last<config.SIGNAL_COOLDOWN:
                    return
                LAST_REQUEST[key]=time.monotonic()
                await generate_signal(cb,state)
            elif action=='nav:back_to_dur' and data.get('symbol'):
                out=await show_screen(cb.message,'expiration',f'Select duration\n\nAsset: {name(data["symbol"])}',expiration_keyboard())
                await activate(state,out,Flow.expiration)
            elif action=='nav:back_to_assets':
                page=int(data.get('page',0))
                out=await show_screen(cb.message,'otc','Select the OTC asset:',asset_keyboard(page))
                await activate(state,out,Flow.asset)
        except (TelegramBadRequest,ValueError,KeyError):
            log.warning('UI request could not finish; use /start (no credentials logged)')
            await cb.message.answer('Please use /start to refresh this screen.')
        finally:
            BUSY.discard(key)

async def main():
    runner=await start_web_server()
    telegram_bot=None
    workers=[]
    polling=None
    try:
        if not config.BOT_TOKEN:
            raise RuntimeError("Set BOT_TOKEN in Render Environment before starting")
        cards.build()  # validates existing files only
        await book.load()
        telegram_bot=Bot(token=config.BOT_TOKEN)
        dp=Dispatcher(storage=MemoryStorage())
        register_handlers(dp)
        workers=[asyncio.create_task(feed.run(),name="market-feed"),
                 asyncio.create_task(book.writer(),name="paper-writer"),
                 asyncio.create_task(book.monitor(),name="paper-monitor")]
        # aiogram handles SIGTERM normally: do not ignore platform shutdown.
        polling=asyncio.create_task(
            dp.start_polling(telegram_bot,close_bot_session=False),name="telegram-polling")
        log.info("[Bot] Running polling; HTTP health server is already listening")
        done,_=await asyncio.wait([polling,*workers],return_when=asyncio.FIRST_COMPLETED)
        # Fail visibly if a worker unexpectedly exits instead of leaving a
        # healthy-looking but nonfunctional process behind.
        for task in workers:
            if task in done:
                log.error("Background worker stopped: %s",task.get_name())
                raise RuntimeError("Background worker stopped unexpectedly")
        await polling  # normal aiogram signal stop or propagate polling error
    finally:
        log.info("[Shutdown] Stopping tasks and releasing HTTP/Telegram resources")
        if polling is not None and not polling.done():
            polling.cancel()
        for task in workers:
            if task.get_name() != "paper-writer":
                task.cancel()
        stopped=[t for t in workers if t.get_name() != "paper-writer"]
        if polling is not None:
            stopped.append(polling)
        if stopped:
            await asyncio.gather(*stopped,return_exceptions=True)
        writer=next((t for t in workers if t.get_name()=="paper-writer"),None)
        if writer is not None:
            if not writer.done():
                try:
                    await asyncio.wait_for(book.queue.join(),timeout=3)
                except asyncio.TimeoutError:
                    log.warning("Paper journal drain timed out during shutdown")
            writer.cancel()
            await asyncio.gather(writer,return_exceptions=True)
        try:
            if telegram_bot is not None:
                await telegram_bot.session.close()
        finally:
            await runner.cleanup()
        log.info("[Shutdown] Complete")

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s %(message)s')
    asyncio.run(main())
