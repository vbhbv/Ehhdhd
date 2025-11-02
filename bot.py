import os
import asyncio
import tempfile
import aiofiles
import random
import re
from aiohttp import ClientSession
from bs4 import BeautifulSoup
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from urllib.parse import urljoin

# --- إعدادات البوت ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
USER_AGENT_HEADER = {'User-Agent': USER_AGENT}
MIN_PDF_SIZE_BYTES = 50 * 1024
TEMP_LINKS_KEY = "current_search_links"

# --- 50 مكتبة عربية ---
LIBRARY_SITES = [
    "https://ketabpedia.com", "https://sahm-book.com", "https://foulabook.com", "https://mktbtypdf.com",
    "https://kotobati.com", "https://masaha.org", "https://almeshkat.com", "https://noor-book.com",
    "https://almeshkat.net", "https://arab-pdf.com", "https://kitab4u.com", "https://kutub.info",
    "https://library4all.com", "https://al-fikr.com", "https://almaktaba.org", "https://books-world.net",
    "https://al-islah.org", "https://pdf4arab.com", "https://freearabebooks.com", "https://arbookshop.com",
    "https://almeshkatbooks.com", "https://arpdf.net", "https://pdfbooksarab.com", "https://al-maktabah.com",
    "https://arabebooksite.com", "https://kutub-pdf.com", "https://ebook-4arab.com", "https://almeshkat-ebooks.com",
    "https://kutubarabia.net", "https://pdf-ebooksarab.com", "https://alkitabonline.com", "https://arbooks.net",
    "https://freearabicbooks.com", "https://arabicpdfbooks.net", "https://kutubpdf.com", "https://arabicbookarchive.com",
    "https://kutub-ebooks.com", "https://pdfkitab.com", "https://alkitabpdf.com", "https://arabicbooklibrary.com",
    "https://almeshkatpdf.com", "https://kutub-arab.com", "https://pdfarabicbooks.com", "https://ebooks4arab.com",
    "https://kutubonline.net", "https://pdfbooks4arab.com", "https://arabiclibrary.org", "https://kutubfree.com",
    "https://ebooks-arab.com", "https://kitabpdf.net"
]

# --- دالة البحث المباشر في المكتبات ---
async def search_libraries(query: str):
    headers = USER_AGENT_HEADER.copy()
    results = []

    async with ClientSession() as session:
        for site in LIBRARY_SITES:
            try:
                # نبحث في صفحة البحث الخاصة بالموقع
                search_url = f"{site}/search?q={query.replace(' ', '+')}"
                async with session.get(search_url, headers=headers, timeout=15) as resp:
                    if resp.status != 200:
                        continue
                    html = await resp.text()
                    soup = BeautifulSoup(html, "html.parser")

                    # البحث عن روابط PDF أو زر تحميل
                    for a in soup.find_all("a", href=True):
                        href = urljoin(site, a['href'])
                        title = a.get_text(strip=True) or "كتاب بدون عنوان"

                        # قبول أي PDF مباشر أو صفحة تحميل
                        if href.lower().endswith(".pdf") or "download" in href.lower():
                            results.append({
                                "title": title,
                                "link": href,
                                "source": site
                            })
            except Exception:
                continue

    # إزالة الروابط المكررة
    unique_links = {}
    for item in results:
        unique_links[item['link']] = item
    return list(unique_links.values())[:10]  # أفضل 10 روابط

# --- دالة تحميل PDF وإرساله ---
async def download_and_send_pdf(context, chat_id, source, title="book.pdf"):
    tmp_dir = tempfile.gettempdir()
    safe_title = re.sub(r"[\\/*?\"<>|]", "_", title)[:50]
    file_path = os.path.join(tmp_dir, f"{safe_title}.pdf")

    async with ClientSession() as session:
        try:
            async with session.get(source, headers=USER_AGENT_HEADER, timeout=30) as resp:
                if resp.status != 200:
                    await context.bot.send_message(chat_id=chat_id, text=f"⚠️ فشل تحميل الملف: {resp.status}")
                    return
                content = await resp.read()
                if len(content) < MIN_PDF_SIZE_BYTES:
                    await context.bot.send_message(chat_id=chat_id, text="⚠️ حجم الملف صغير جدًا.")
                    return
                async with aiofiles.open(file_path, "wb") as f:
                    await f.write(content)
        except Exception as e:
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ خطأ أثناء تحميل الملف: {e}")
            return

    # إرسال الملف وحذفه بعد الإرسال
    try:
        with open(file_path, "rb") as f:
            await context.bot.send_document(chat_id=chat_id, document=f)
        await context.bot.send_message(chat_id=chat_id, text="✅ تم إرسال الكتاب بنجاح.")
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"⚠️ خطأ أثناء الإرسال: {e}")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

# --- Telegram handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📚 بوت الكتب العربية جاهز! استخدم /search متبوعًا باسم الكتاب أو المؤلف.")

async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("استخدم: /search اسم الكتاب أو المؤلف")
        return
    msg = await update.message.reply_text(f"🔍 أبحث عن '{query}' في المكتبات العربية...")
    try:
        results = await search_libraries(query)
        if not results:
            await msg.edit_text("❌ لم أجد نتائج في المكتبات العربية.")
            return

        buttons = []
        text_lines = []
        context.user_data[TEMP_LINKS_KEY] = [item["link"] for item in results]
        for i, item in enumerate(results):
            title = item["title"][:100]
            text_lines.append(f"{i+1}. {title} ({item['source']})")
            buttons.append([InlineKeyboardButton(f"📥 تحميل {i+1}", callback_data=f"dl|{i}")])
        await msg.edit_text("\n".join(text_lines), reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        await msg.edit_text(f"⚠️ خطأ أثناء البحث: {e}")

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("dl|"):
        index = int(data.split("|")[1])
        link = context.user_data[TEMP_LINKS_KEY][index]
        await query.edit_message_text("⏳ تحميل الكتاب...")
        await download_and_send_pdf(context, query.message.chat_id, link, title=f"book_{index+1}.pdf")

def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN مفقود في المتغيرات البيئية.")
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler))
    print("البوت بدأ العمل.")
    app.run_polling()

if __name__ == "__main__":
    main()
