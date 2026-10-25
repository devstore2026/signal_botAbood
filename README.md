# Abood Trading — Render 24/7

## التشغيل على مدار الساعة (مجاناً)
الخطة المجانية تُسكِت الخدمة بعد 15 دقيقة بلا حركة. الحل المدمج: **self-ping**.

1. ارفع هذا المجلد إلى GitHub.
2. Render → New → Blueprint → اختر المستودع (يقرأ render.yaml).
3. أدخل متغيرات البيئة في لوحة Render:
   - `BOT_TOKEN` = توكن البوت
   - `PO_AUTH_FRAME` = إطار auth التجريبي (إن لزم)
   - **`SELF_URL` = رابط خدمتك** (مثل `https://abood-trading-bot.onrender.com`)
     — بدونه لا يعمل الـ keep-alive وتسكن الخدمة.
4. Deploy.

الكود يبعث طلب GET إلى `/health` كل 4 دقائق لنفسه، فيبقى المنفذ نشطاً ولا يرسل Render إشارة الإيقاف.

> ملاحظة: Render قد تغيّر سياسة الخطة المجانية؛ الـ self-ping حل شائع لكنه ليس ضماناً رسمياً.

## بنية المشروع (نظيفة للاستضافة)
```
bot.py          # البوت + خادم الصحة + keep-alive
market_feed.py  # بث التكات الحية + إعادة اتصال
strategy.py     # محرك الإشارة (8 توافقات)
assets.py       # أزواج عملات OTC فقط (33 زوجاً)
cards.py        # تحميل البانرات
protocol.py     # فك حزم Socket.IO
paper.py        # (اختياري) قياس النتائج الورقي
config.py       # الإعدادات
assets/         # البانرات
requirements.txt
render.yaml
```
