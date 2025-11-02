import os
import asyncio
import httpx # يجب إضافة 'httpx' إلى requirements.txt
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from mining_engine import run_mining_task 
import re 
import io # لإدارة الملفات في الذاكرة

# -----------------------------------------------------
#                   إعدادات البوت والتوكن
# -----------------------------------------------------

BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

if not BOT_TOKEN:
    print("❌ خطأ حرج: لم يتم العثور على توكن البوت في متغيرات البيئة.")
    exit()

# -----------------------------------------------------
#                   دوال البوت (Handlers)
# -----------------------------------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أهلاً بك! أرسل لي رابط الصفحة لأبدأ البحث عن زر التحميل وتنزيل الكتاب لك مباشرة.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text
    chat_id = update.effective_chat.id

    if user_input.startswith(('http://', 'https://')):
        await context.bot.send_message(chat_id=chat_id, text=f"🔍 تم استلام الرابط: {user_input}\nبدء تحليل الصفحة وتحديد الملف...")
        
        # تنفيذ مهمة الاستخلاص في مهمة منفصلة
        asyncio.create_task(run_mining_task_and_respond(chat_id, user_input, context))
            
    else:
        await update.message.reply_text("الرجاء إرسال رابط URL صالح للصفحة.")

async def run_mining_task_and_respond(chat_id, url, context: ContextTypes.DEFAULT_TYPE):
    """دالة مساعدة لتشغيل مهمة الاستخلاص وتحميل الملف وإرساله."""
    
    await context.bot.send_chat_action(chat_id, 'typing')
    
    try:
        # 1. استدعاء دالة الاستخلاص التي ترجع الرابط
        result = await run_mining_task(url)
        
        if not result or not result.get('final_download_link'):
            await context.bot.send_message(chat_id=chat_id, text="❌ فشل العثور على رابط تحميل موثوق بعد التحليل بالذكاء الاصطناعي.")
            return

        download_url = result['final_download_link']
        
        # 2. تحميل الملف باستخدام httpx (بشكل غير متزامن)
        await context.bot.send_message(chat_id=chat_id, text=f"✅ تم تحديد الرابط. بدء تحميل الملف...")
        await context.bot.send_chat_action(chat_id, 'upload_document')
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # يمكن إضافة User-Agent لتقليل احتمالية الحظر
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
            
            async with client.stream("GET", download_url, headers=headers) as response:
                response.raise_for_status() # رفع خطأ إذا كان رمز الحالة 4xx/5xx

                # فحص حجم الملف (اختياري لكن يوصى به)
                content_length = int(response.headers.get('Content-Length', 0))
                if content_length > 50 * 1024 * 1024:
                    await context.bot.send_message(chat_id=chat_id, text="⚠️ الملف كبير جدًا (> 50MB) لإرساله عبر البوت. تم إرسال الرابط بدلاً من ذلك.")
                    await context.bot.send_message(chat_id=chat_id, text=f"الرابط المباشر: {download_url}")
                    return
                
                # قراءة المحتوى إلى الذاكرة
                file_content = await response.read()

        # 3. استخراج اسم الملف وإرساله
        filename = re.search(r'[^/]+\.(pdf|epub|zip)', download_url.lower())
        file_name_to_send = filename.group(0) if filename else 'downloaded_file.pdf'
        
        await context.bot.send_document(
            chat_id=chat_id,
            document=InputFile(io.BytesIO(file_content), filename=file_name_to_send),
            caption="🌟 تم تنزيل الملف لك بواسطة البوت!"
        )

    except httpx.HTTPStatusError as e:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ خطأ HTTP أثناء تحميل الملف: {e.response.status_code}")
    except Exception as e:
        print(f"❌ خطأ أثناء تشغيل مهمة التعدين: {e}")
        await context.bot.send_message(chat_id=chat_id, text="❌ حدث خطأ غير متوقع أثناء معالجة الرابط أو تحميل الملف.")


# -----------------------------------------------------
#                   تشغيل البوت (Main)
# -----------------------------------------------------

def main():
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 البوت يعمل الآن...")
    application.run_polling(poll_interval=3)

if __name__ == '__main__':
    main()
