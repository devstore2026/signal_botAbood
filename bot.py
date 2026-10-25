"""Telegram controls + HTTP liveness + M1 worker, one asyncio event loop.
Read-only scanner: no order endpoints, random directions or fake accuracy.
"""
import asyncio
import logging
import os
from aiohttp import web
from aiogram import Bot,Dispatcher
from aiogram.filters import Command,CommandStart
from aiogram.types import Message,CallbackQuery,InlineKeyboardButton,InlineKeyboardMarkup
from aiogram.exceptions import TelegramBadRequest
import config
from feed import Feed
from news import NewsGuard
from scanner import Scanner
log=logging.getLogger('bot')
feed=Feed()
news=NewsGuard()
recipients=set(config.ALERT_CHAT_IDS)
scanner=None

def authorized(user):
    return user is not None and (not config.ALLOWED_USER_IDS or user.id in config.ALLOWED_USER_IDS)

def controls():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='▶️ تشغيل الماسح',callback_data='scan:on'),
        InlineKeyboardButton(text='⏸ إيقاف التنبيهات',callback_data='scan:off')],
        [InlineKeyboardButton(text='📡 الحالة',callback_data='scan:status')]])

def status_text():
    eligible=feed.registry.eligible()
    bars=sum(1 for s in eligible if len(feed.store.bars.get(s,()))>=26)
    metrics=dict(scanner.metrics) if scanner else {}
    return (f'Abood CHESS Scanner\nالوضع: {config.SCAN_MODE}\n'
            f'WebSocket: {feed.connected} | Auth: {feed.authenticated}\n'
            f'آخر حدث: {feed.last_event}\n'
            f'الأصول المفهومة: {len(feed.registry.assets)}\n'
            f'العائد >= {config.MIN_PAYOUT:g}% ومتاح وحديث: {len(eligible)}\n'
            f'مشترك بها: {len(feed.subscribed)} | ذات 26 شمعة على الأقل: {bars}\n'
            f'صفوف بيانات غير مفهومة: {feed.registry.unmapped_rows}\n'
            f'تشخيص: {feed.last_error or "لا يوجد"}\n'
            f'زمن الحساب المحلي ms: {scanner.last_duration_ms if scanner else 0:.2f}\n'
            f'عدادات الفحص: {metrics}\n'
            'درجة التحليل ليست نسبة نجاح. لا تصدر إشارة دون استيفاء الفلاتر.')

def handlers(dp):
    @dp.message(CommandStart())
    async def start(message:Message):
        if not authorized(message.from_user):
            return
        recipients.add(message.chat.id)
        await message.answer(
            '♟️ Abood CHESS Scanner\n'
            'مسح جميع فئات الأصول المتاحة ذات عائد M1 >=85%، وفريم M1 ومدة 60 ثانية فقط.\n'
            'استراتيجية انتقائية: لا نرسل إشارة عند غياب الشروط.\n'
            f'وضع التوقيت: {config.SCAN_MODE}. /status للتشخيص و/stop لإيقاف التنبيهات.\n'
            'الإشارة المبكرة قبل الإغلاق تمهيدية وليست تأكيداً نهائياً.',reply_markup=controls())
    @dp.message(Command('status'))
    async def status(message:Message):
        if authorized(message.from_user):
            await message.answer(status_text()[:4000])
    @dp.message(Command('stop'))
    async def stop(message:Message):
        if authorized(message.from_user):
            recipients.discard(message.chat.id)
            await message.answer('تم إيقاف التنبيهات لهذه المحادثة. /start لإعادة الاشتراك.')
    @dp.callback_query()
    async def callback(cb:CallbackQuery):
        if not authorized(cb.from_user):
            return
        try:
            await cb.answer()
        except TelegramBadRequest:
            return
        if not isinstance(cb.message,Message):
            return
        if cb.data=='scan:on':
            recipients.add(cb.message.chat.id)
            await cb.message.answer('تم تشغيل التنبيهات. الفحص تلقائي ولا يصدر إشارات إجبارية.')
        elif cb.data=='scan:off':
            recipients.discard(cb.message.chat.id)
            await cb.message.answer('تم إيقاف التنبيهات.')
        elif cb.data=='scan:status':
            await cb.message.answer(status_text()[:4000])

async def handle_ping(request):
    return web.Response(text='Abood CHESS scanner is running!')

async def start_web_server():
    port=int(os.environ.get('PORT','10000'))
    app=web.Application()
    app.router.add_get('/',handle_ping)
    app.router.add_get('/health',handle_ping)
    runner=web.AppRunner(app,access_log=None)
    try:
        await runner.setup()
        await web.TCPSite(runner,'0.0.0.0',port).start()
    except BaseException:
        await runner.cleanup()
        raise
    log.info('[Web] listening on 0.0.0.0:%d',port)
    return runner

async def main():
    global scanner
    runner=await start_web_server()
    client=None
    tasks=[]
    try:
        if not config.BOT_TOKEN:
            raise RuntimeError('Set BOT_TOKEN in Environment')
        client=Bot(token=config.BOT_TOKEN)
        dp=Dispatcher()
        handlers(dp)
        scanner=Scanner(feed,news,client,recipients)
        tasks=[asyncio.create_task(feed.run(),name='feed'),
               asyncio.create_task(news.run(),name='news'),
               asyncio.create_task(scanner.run(),name='scanner'),
               asyncio.create_task(dp.start_polling(client,close_bot_session=False),name='polling')]
        done,_=await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            await task
            if task.get_name()!='polling':
                raise RuntimeError('Background worker stopped unexpectedly')
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        try:
            if client is not None:
                await client.session.close()
        finally:
            await runner.cleanup()
if __name__=='__main__':
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s %(message)s')
    asyncio.run(main())
