#!/bin/bash

# 1. تثبيت المتصفحات المطلوبة لـ Playwright
echo "🚀 تثبيت متصفحات Playwright..."
playwright install --with-deps chromium

# 2. تشغيل البوت
echo "🤖 بدء تشغيل بوت Telegram..."
python bot.py
