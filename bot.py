import os
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from mining_engine import run_mining_task # استيراد دالة التشغيل من ملفنا

# 1. جلب التوكن من متغيرات البيئة
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

if not BOT_TOKEN:
    print("❌ خطأ حرج: لم يتم العثور على توكن البوت في متغيرات البيئة (TELEGRAM_BOT_TOKEN).")
    exit()

# -----------------------------------------------------
#                   دوال البوت (Handlers)
# -----------------------------------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الرد على أمر /start."""
    await update.message.reply_text("أهلاً بك! أنا بوت استخلاص الكتب. أرسل لي رابط الصفحة لأبدأ البحث عن زر التحميل.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الرسائل الواردة."""
    user_input = update.message.text
    chat_id = update.effective_chat.id

    # تحقق بسيط من أن الرسالة تبدو كرابط
    if user_input.startswith(('http://', 'https://')):
        await context.bot.send_message(chat_id=chat_id, text=f"🔍 تم استلام الرابط: {user_input}\nبدء تحليل الصفحة باستخدام نموذج الذكاء الاصطناعي...")
        
        # 🚨 تنفيذ مهمة الاستخلاص غير المتزامنة (Async)
        try:
            # نستخدم asyncio.create_task لتشغيل المهمة دون إيقاف البوت
            asyncio.create_task(run_mining_task_and_respond(chat_id, user_input, context))
            
        except Exception as e:
            await context.bot.send_message(chat_id=chat_id, text=f"❌ حدث خطأ داخلي أثناء التشغيل: {e}")
            
    else:
        await update.message.reply_text("الرجاء إرسال رابط URL صالح للصفحة التي تحتوي على زر التحميل.")

async def run_mining_task_and_respond(chat_id, url, context: ContextTypes.DEFAULT_TYPE):
    """دالة مساعدة لتشغيل مهمة الاستخلاص والرد على المستخدم."""
    
    # يمكنك استخدام run_mining_task مباشرة إذا كانت نتيجتها تحتوي على رابط الملف
    # (لاحظ: run_mining_task الحالية تطبع فقط، يجب أن تعيد النتيجة النهائية)

    # 🚨 افتراض: سنقوم فقط بتشغيل run_mining_task التي تطبع النتيجة حالياً
    try:
        await run_mining_task(url)
        # 💡 يجب تعديل run_mining_task في mining_engine.py لترجع النتيجة بدلاً من طباعتها
        # لغرض العرض، سنرسل رسالة إكمال:
        await context.bot.send_message(chat_id=chat_id, text="✅ انتهى التحليل. تحقق من سجلات التطبيق (Logs) للحصول على النتيجة.")

    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ فشلت عملية الاستخلاص: {e}")


# -----------------------------------------------------
#                   تشغيل البوت (Main)
# -----------------------------------------------------

def main():
    """نقطة الدخول لتشغيل تطبيق Telegram."""
    
    # بناء تطبيق البوت
    application = Application.builder().token(BOT_TOKEN).build()

    # إضافة المعالجات (Handlers)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 البوت يعمل الآن...")
    # بدء البوت
    application.run_polling(poll_interval=3)

if __name__ == '__main__':
    main()
