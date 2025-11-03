# اختر صورة Python مناسبة
FROM python:3.11-slim

# تعيين متغير البيئة لمنع buffering
ENV PYTHONUNBUFFERED=1

# إنشاء مجلد العمل
WORKDIR /app

# نسخ ملفات المشروع
COPY . /app

# تثبيت المكتبات المطلوبة
RUN pip install --no-cache-dir python-telegram-bot==20.8 telethon aiofiles

# أمر البدء عند تشغيل الحاوية
CMD ["python", "bot.py"]
