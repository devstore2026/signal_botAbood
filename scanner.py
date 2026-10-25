"""Deadline-aware M1 scanner: local math only in the :57/:58 hot path.
WebSocket is kept warm separately. No historical HTTP fetch at scan time.
"""
import asyncio
from collections import Counter
from datetime import datetime,timezone,timedelta
import logging
import time
from aiogram.exceptions import TelegramForbiddenError,TelegramRetryAfter,TelegramAPIError
import config
from chess import detect_chess
log=logging.getLogger('scanner')
TZ=timezone(timedelta(hours=3))

def plan(now,mode=None):
    mode=mode or config.SCAN_MODE
    minute=int(now//60)*60
    if mode=='PRE_CLOSE':
        trigger=minute+config.SCAN_SECOND
        deadline=minute+config.SEND_DEADLINE_SECOND
        if now>=deadline:
            trigger+=60
            deadline+=60
        return max(now,trigger),int(trigger//60)*60+60,deadline
    trigger=minute+0.20
    if now>trigger:
        trigger+=60
    entry=int(trigger//60)*60
    return trigger,entry,entry+2.0

def alert_text(signal,asset,entry):
    direction='🟢 CALL / UP' if signal.direction=='UP' else '🔴 PUT / DOWN'
    when=datetime.fromtimestamp(entry,TZ).strftime('%Y-%m-%d %H:%M:%S')
    text=(f'♟️ إشارة استراتيجية الشطرنج | CHESS SIGNAL\n'
          f'━━━━━━━━━━━━━━━━━━━━\n'
          f'📊 الزوج: {asset.name} (نسبة الربح: {asset.payout:g}%)\n'
          f'⏱ فريم التحليل: 1 دقيقة (M1)\n'
          f'🎯 مدة الصفقة: 60 ثانية (شمعة واحدة)\n'
          f'📈 الاتجاه المطلوب: {direction}\n'
          f'⏰ وقت الدخول: {when} (UTC+3) عند الثانية 00\n'
          f'━━━━━━━━━━━━━━━━━━━━\n'
          f'🔍 قوة التحليل والتأكيد: {signal.confidence:g}%\n'
          f'💡 حالة السوق: تذبذب عرضي متناسق (Flat Range)\n'
          f'⚠️ تنبيه: لا تدخل الصفقة إذا افتتحت الشمعة بفجوة سعرية (GAP).')
    if signal.provisional:
        text+='\n🕯️ إشارة تمهيدية قبل الإغلاق: C1 قد تتغير في الثواني الأخيرة.'
    else:
        text+='\n🕯️ تأكيد شموع مغلقة؛ إذا وصل التنبيه بعد وقت الدخول المحدد فتجاهله.'
    text+='\nℹ️ النسبة درجة تحقق للشروط وليست احتمال ربح مضموناً.'
    if signal.points['verified_news']==0:
        text+='\n📰 تغطية الأخبار غير مؤكدة؛ نقاط الأخبار = 0/10.'
    return text

class Scanner:
    def __init__(self,feed,news,bot,recipients):
        self.feed=feed
        self.news=news
        self.bot=bot
        self.recipients=recipients
        self.seen={}
        self.metrics=Counter()
        self.last_scan=None
        self.last_duration_ms=0.0
    def candidates(self,now,entry):
        results=[]
        eligible=self.feed.registry.eligible(now)
        for symbol,asset in eligible.items():
            self.metrics['examined']+=1
            last=self.feed.last_tick.get(symbol)
            if last is None or not 0<=now-last<=config.MAX_TICK_AGE:
                self.metrics['stale_ticks']+=1
                continue
            bars,reason=self.feed.store.window(symbol,now)
            if not bars:
                self.metrics[reason]+=1
                continue
            state=self.news.state(symbol,entry,entry+60,now)
            result=detect_chess(symbol,bars,news_state=state,
                                provisional=config.SCAN_MODE=='PRE_CLOSE')
            if result.signal:
                results.append((result.signal,asset))
                self.metrics['qualified']+=1
            else:
                self.metrics[result.reason]+=1
        return sorted(results,key=lambda pair:(-pair[0].confidence,-pair[1].payout,pair[0].symbol))
    async def deliver(self,signal,asset,entry,deadline):
        # Revalidate payout, availability and source age after local scoring.
        now=time.time()
        current=self.feed.registry.eligible(now).get(asset.symbol)
        if current is None:
            self.metrics['payout_changed']+=1
            return
        last=self.feed.last_tick.get(asset.symbol,0)
        if now-last>config.MAX_TICK_AGE or last>now+1:
            self.metrics['source_stale_before_send']+=1
            return
        if now>=deadline:
            self.metrics['deadline_missed']+=1
            return
        text=alert_text(signal,current,entry)
        sem=asyncio.Semaphore(8)
        async def send(chat):
            key=(chat,asset.symbol,entry)
            if key in self.seen:
                return
            async with sem:
                remaining=deadline-time.time()
                if remaining<=0:
                    self.metrics['deadline_missed']+=1
                    return
                self.seen[key]=entry  # timeouts may have delivered; don't retry duplicates
                try:
                    await asyncio.wait_for(self.bot.send_message(
                        chat,text,request_timeout=remaining),timeout=remaining)
                    self.metrics['delivered']+=1
                    if time.time()>=entry:
                        self.metrics['delivered_after_entry']+=1
                except TelegramForbiddenError:
                    self.recipients.discard(chat)
                except (asyncio.TimeoutError,TelegramRetryAfter,TelegramAPIError):
                    # Never put a timed trading alert in an after-deadline retry queue.
                    self.metrics['send_failed_or_late']+=1
        await asyncio.gather(*(send(chat) for chat in tuple(self.recipients)))
    async def cycle(self,entry,deadline):
        started=time.perf_counter()
        now=time.time()
        self.last_scan=now
        if not self.feed.connected or not self.feed.authenticated:
            self.metrics['feed_unready']+=1
            return
        if now>=deadline:
            self.metrics['deadline_missed']+=1
            return
        candidates=self.candidates(now,entry)
        self.last_duration_ms=(time.perf_counter()-started)*1000
        # Every eligible asset was evaluated, but alerts are capped to avoid
        # Telegram floods. All qualifying alerts can be enabled via config.
        for signal,asset in candidates[:config.MAX_SIGNALS_PER_MINUTE]:
            await self.deliver(signal,asset,entry,deadline)
        log.info('Scan local_math_ms=%.2f eligible=%d qualified=%d',self.last_duration_ms,
                 len(self.feed.registry.eligible()),len(candidates))
        self.seen={k:v for k,v in self.seen.items() if v>time.time()-7200}
    async def run(self):
        while True:
            trigger,entry,deadline=plan(time.time())
            await asyncio.sleep(max(0,trigger-time.time()))
            # Large clock jumps or busy event loops must skip, not send late.
            await self.cycle(entry,deadline)
            next_boundary=entry+0.05 if config.SCAN_MODE=='PRE_CLOSE' else entry+60.05
            await asyncio.sleep(max(0,next_boundary-time.time()))
