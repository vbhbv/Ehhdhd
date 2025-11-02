import os
import requests
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# ========= إعدادات البوت =========
BOT_TOKEN = "7176379503:AAFdo257wapb4wJntAk_axaoGBuFdQP617w"
GOOGLE_API_KEY = "AIzaSyCll0HI8NCDut4I4xBBabQ9bRX2SPFTbDk"
SEARCH_ENGINE_ID = "b210b5e71b2aa4918"
# =================================

# دالة البحث في جوجل عن ملفات PDF من موقع مكتبة النور أو كتوباتي
def search_books(query):
    try:
        q = f"site:ktobati.com OR site:alnoor.se filetype:pdf {query}"
        url = f"https://www.googleapis.com/customsearch/v1?q={q}&key={GOOGLE_API_KEY}&cx={SEARCH_ENGINE_ID}"
        response = requests.get(url)
        results = response.json()

        if "items" not in results:
            return None

        links = []
        for item in results["items"]:
            link = item.get("link", "")
            if link.endswith(".pdf"):
                links.append(link)
        return links if links else None
    except Exception as e:
        print("Search error:", e)
        return None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 مرحباً بك! أرسل اسم الكتاب أو المؤلف للبحث عن ملف PDF.\nمثلاً:\n/search ابن سينا")


async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) == 0:
        await update.message.reply_text("❗استخدم الأمر بالشكل التالي:\n/search اسم الكتاب أو المؤلف")
        return

    query = " ".join(context.args)
    await update.message.reply_text(f"🔍 جاري البحث عن: {query}")

    links = search_books(query)
    if not links:
        await update.message.reply_text("❌ لم أجد نتائج. حاول بكلمات مختلفة.")
        return

    for link in links[:2]:  # أرسل أول نتيجتين فقط لتجنب الإزعاج
        try:
            file_name = link.split("/")[-1]
            r = requests.get(link)
            if r.status_code == 200:
                with open(file_name, "wb") as f:
                    f.write(r.content)
                await update.message.reply_document(open(file_name, "rb"), caption=f"📘 {file_name}")
                os.remove(file_name)  # حذف الملف بعد الإرسال
        except Exception as e:
            await update.message.reply_text(f"⚠️ خطأ أثناء تحميل الملف: {e}")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🧠 الأوامر المتاحة:\n/start - بدء الاستخدام\n/search [اسم الكتاب] - البحث عن كتاب PDF")


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("search", search))

    print("✅ البوت يعمل الآن ...")
    app.run_polling()


if __name__ == "__main__":
    main()
