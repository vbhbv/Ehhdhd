import os
import asyncio
import aiofiles
import tempfile
import re
from aiohttp import ClientSession
from bs4 import BeautifulSoup
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from urllib.parse import urljoin, urlparse

# --- إعدادات البوت ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
HEADERS = {'User-Agent': USER_AGENT}
TEMP_LINKS_KEY = "current_search_links"
MIN_PDF_SIZE_BYTES = 50 * 1024  # 50KB

# --- قائمة مكتبات عربية حقيقية فقط ---
LIBRARY_SITES = [
    "https://ketabpedia.com",
    "https://foulabook.com",
    "https://sahm-book.com",
    "https://mktbtypdf.com",
    "https://kotobati.com",
    "https://masaha.org",
    "https://almeshkat.com",
    "https://noor-book.com",
    "https://kitab4u.com",
    "https://kutub.info",
    "https://library4all.com",
    "https://al-fikr.com",
    "https://pdf4arab.com",
    "https://freearabebooks.com",
    "https://arbookshop.com",
    "https://alkitabonline.com",
    "https://pdfkitab.com",
    "https://ebooks4arab.com",
    "https://arabicbooklibrary.com",
    "https://kitabpdf.net"
]

# --- دالة بحث مبتكرة في المكتبات العربية ---
async def search_libraries(query: str):
    results = []
    async with ClientSession(headers=HEADERS) as session:
        for site in LIBRARY_SITES:
            try:
                # بعض المواقع لديها صفحة بحث محددة
                search_url = f"{site}/search?q={query.replace(' ', '+')}"
                async with session.get(search_url, timeout=15) as resp:
                    if resp.status != 200:
                        continue
                    html = await resp.text()
                    soup = BeautifulSoup(html, "html.parser")
                    # البحث عن روابط PDF مباشرة أو زر تحميل
                    for a in soup.find_all("a", href=True):
                        href = urljoin(site, a['href'])
                        title = a.get_text(strip=True) or "كتاب بدون عنوان"
                        if href.lower().endswith(".pdf") or "download" in href.lower():
                            results.append({"title": title, "link": href, "source": site})
            except Exception:
                continue
    # إزالة الروابط المكررة
    unique_links = {}
    for item in results:
        unique_links[item['link']] = item
    return list(unique_links.values())[:10]  # أفضل 10 روابط

# --- دالة تحميل ذكية وفعالة ---
async def download_pdf(url: str, filename: str):
    tmp_dir = tempfile.gettempdir()
    safe_title = re.sub(r"[\\/*?\"<>|]", "_", filename)[:50]
    file_path = os.path.join(tmp_dir, f"{safe_title}.pdf")
    async with ClientSession(headers=HEADERS) as session:
        try:
            async with session.get(url, timeout=30) as resp:
                if resp.status != 200:
                    return None, f"فشل تحميل الملف: رمز {resp.status}"
                content = await resp.read()
                if len(content) < MIN_PDF_SIZE_BYTES:
                    return None, "حجم الملف صغير جداً."
                async with aiofiles.open(file_path, "wb") as f:
                    await f.write(content)
            return file_path, None
        except Exception as e:
            return None, str(e)

# --- دالة إرسال ذكية ---
async def send_pdf(context, chat_id, file_path):
    try:
        with open(file_path, "rb") as f:
            await context.bot.send_document(chat_id=chat_id, document=f)
        await context.bot.send_message(chat_id=chat_id, text="✅ تم إرسال الكتاب بنجاح.")
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"⚠️ خطأ أثناء الإرسال: {e}")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

# --- Handlers ---
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
            text_lines.append(f"{i+1}. {title} ({urlparse(item['source']).netloc})")
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
        file_path, error = await download_pdf(link, f"book_{index+1}.pdf")
        if file_path:
            await send_pdf(context, query.message.chat_id, file_path)
        else:
            await context.bot.send_message(chat_id=query.message.chat_id, text=f"⚠️ فشل التحميل: {error}")

# --- Main ---
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
