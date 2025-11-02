import os
import asyncio
import aiohttp
import aiofiles
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ===== إعدادات البوت =====
BOT_TOKEN = "7176379503:AAFdo257wapb4wJntAk_axaoGBuFdQP617w"
GOOGLE_API_KEY = "AIzaSyCll0HI8NCDut4I4xBBabQ9bRX2SPFTbDk"
SEARCH_ENGINE_ID = "b210b5e71b2aa4918"
# =========================

# البحث في Google عبر API
async def google_search(query):
    url = (
        f"https://www.googleapis.com/customsearch/v1"
        f"?q=site:alnoor.se OR site:ktobati.com filetype:pdf {query}"
        f"&key={GOOGLE_API_KEY}&cx={SEARCH_ENGINE_ID}"
    )
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            data = await response.json()
            items = data.get("items", [])
            results = []
            for item in items:
                link = item.get("link", "")
                if link.endswith(".pdf"):
                    results.append(link)
            return results


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 مرحباً! أرسل اسم الكتاب أو المؤلف لأبحث لك عن ملف PDF.\n"
        "مثلاً:\n/search ابن سينا"
    )


async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❗ استخدم الأمر بالشكل التالي:\n/search اسم الكتاب")
        return

    query = " ".join(context.args)
    await update.message.reply_text(f"🔎 جاري البحث عن: {query} ...")

    links = await google_search(query)
    if not links:
        await update.message.reply_text("❌ لم أجد كتب PDF مطابقة، حاول كلمات مختلفة.")
        return

    sent_any = False
    for link in links[:2]:  # إرسال أول نتيجتين فقط
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(link) as r:
                    if r.status == 200:
                        file_name = link.split("/")[-1]
                        async with aiofiles.open(file_name, "wb") as f:
                            await f.write(await r.read())

                        await update.message.reply_document(open(file_name, "rb"), caption=f"📘 {file_name}")
                        os.remove(file_name)
                        sent_any = True
        except Exception as e:
            print(f"⚠️ خطأ أثناء تحميل {link}: {e}")

    if not sent_any:
        await update.message.reply_text("⚠️ لم أتمكن من تحميل أي ملف PDF صالح.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧠 الأوامر المتاحة:\n/start - بدء الاستخدام\n/search [اسم الكتاب] - البحث عن كتاب PDF"
    )


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("search", search))

    print("✅ البوت يعمل الآن ...")
    app.run_polling()


if __name__ == "__main__":
    main()
