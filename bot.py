import os
import asyncio
import tempfile
import aiofiles
import random 
from aiohttp import ClientSession
from bs4 import BeautifulSoup
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes 
from urllib.parse import urljoin 
from ddgs import DDGS

# --- إعدادات البوت ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
USER_AGENT_HEADER = {'User-Agent': USER_AGENT}
MIN_PDF_SIZE_BYTES = 50 * 1024 
TEMP_LINKS_KEY = "current_search_links" 

# --- 50 مكتبة عربية ---
TRUSTED_DOMAINS = [
    "ketabpedia.com", "sahm-book.com", "foulabook.com", "mktbtypdf.com", "kotobati.com",
    "masaha.org", "almeshkat.com", "noor-book.com", "almeshkat.net", "arab-pdf.com",
    "kitab4u.com", "kutub.info", "library4all.com", "al-fikr.com", "almaktaba.org",
    "books-world.net", "al-islah.org", "pdf4arab.com", "freearabebooks.com", "arbookshop.com",
    "almeshkatbooks.com", "arpdf.net", "pdfbooksarab.com", "al-maktabah.com", "arabebooksite.com",
    "kutub-pdf.com", "ebook-4arab.com", "almeshkat-ebooks.com", "kutubarabia.net", "pdf-ebooksarab.com",
    "alkitabonline.com", "arbooks.net", "freearabicbooks.com", "arabicpdfbooks.net", "kutubpdf.com",
    "arabicbookarchive.com", "kutub-ebooks.com", "pdfkitab.com", "alkitabpdf.com", "arabicbooklibrary.com",
    "almeshkatpdf.com", "kutub-arab.com", "pdfarabicbooks.com", "ebooks4arab.com", "kutubonline.net",
    "pdfbooks4arab.com", "arabiclibrary.org", "kutubfree.com", "ebooks-arab.com", "kitabpdf.net"
]

# --- دالة البحث المحدثة ---
async def search_duckduckgo(query: str):
    sites_query = " OR ".join([f"site:{d}" for d in TRUSTED_DOMAINS])
    full_query = f"{query} filetype:pdf OR {sites_query}"
    results = []

    try:
        with DDGS(timeout=5) as ddgs:
            search_results = ddgs.text(full_query, max_results=30)  # زيادة عدد النتائج
            for r in search_results:
                link = r.get("href")
                title = r.get("title")
                if link and title:
                    # السماح بروابط PDF مباشرة أو من المكتبات العربية
                    if link.lower().endswith(".pdf") or any(d in link for d in TRUSTED_DOMAINS):
                        results.append({"title": title.strip(), "link": link})
    except Exception as e:
        print(f"DDGS search failed: {e}")
        return []

    # إزالة الروابط المكررة
    unique_links = {}
    for item in results:
        unique_links[item['link']] = item

    return list(unique_links.values())[:10]  # إرجاع أفضل 10 نتائج

# --- دالة تحميل وإرسال PDF ---
async def download_and_send_pdf(context, chat_id, source, title="book.pdf", is_local_path=False, referer_link=None):
    if is_local_path:
        file_path = source 
    else:
        pdf_url = source
        download_headers = USER_AGENT_HEADER.copy()
        if referer_link:
            download_headers['Referer'] = referer_link

        async with ClientSession() as session:
            try:
                async with session.head(pdf_url, headers=download_headers, allow_redirects=True, timeout=10) as head_resp: 
                    content_type = head_resp.headers.get('Content-Type', '').lower()
                    content_length = int(head_resp.headers.get('Content-Length', 0))
                    if 'application/pdf' not in content_type and 'octet-stream' not in content_type:
                        await context.bot.send_message(chat_id=chat_id, text=f"⚠️ الرابط المستخلص لا يشير إلى PDF ({content_type})")
                        return
                    if content_length < MIN_PDF_SIZE_BYTES:
                        await context.bot.send_message(chat_id=chat_id, text="⚠️ حجم الملف صغير جدًا.")
                        return
            except Exception as e:
                await context.bot.send_message(chat_id=chat_id, text=f"⚠️ فشل التحقق من الملف: {e}")
                return

            tmp_dir = tempfile.gettempdir()
            safe_title = title.replace("/", "_")[:40]
            file_path = os.path.join(tmp_dir, f"{safe_title}.pdf")

            async with session.get(pdf_url, headers=download_headers) as resp: 
                if resp.status != 200:
                    await context.bot.send_message(chat_id=chat_id, text=f"⚠️ فشل تحميل الملف. رمز الخطأ: {resp.status}")
                    return
                content = await resp.read()
                async with aiofiles.open(file_path, "wb") as f:
                    await f.write(content)

    # إرسال الملف وحذفه
    try:
        with open(file_path, "rb") as f:
            await context.bot.send_document(chat_id=chat_id, document=f)
        await context.bot.send_message(chat_id=chat_id, text="✅ تم إرسال الكتاب بنجاح.")
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"⚠️ خطأ أثناء الإرسال: {e}")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

# --- دوال Telegram الأساسية ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 بوت الكتب العربية جاهز!\n"
        "أرسل /search متبوعًا باسم الكتاب أو المؤلف."
    )

async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("استخدم: /search اسم الكتاب أو المؤلف")
        return

    msg = await update.message.reply_text(f"🔍 أبحث عن **{query}** في المكتبات العربية...")
    try:
        results = await search_duckduckgo(query)
        if not results:
            await msg.edit_text("❌ لم أجد نتائج في المكتبات العربية.")
            return

        buttons = []
        text_lines = []
        context.user_data[TEMP_LINKS_KEY] = [item.get("link") for item in results]
        for i, item in enumerate(results, start=0):
            title = item.get("title")[:120]
            text_lines.append(f"{i+1}. {title}")
            buttons.append([InlineKeyboardButton(f"📥 تحميل {i+1}", callback_data=f"dl|{i}")])
        reply = "\n".join(text_lines)
        await msg.edit_text(reply, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        await msg.edit_text(f"⚠️ خطأ أثناء البحث: {e}")

# --- Callback لتحميل الملف ---
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("dl|"):
        index = int(data.split("|")[1])
        link = context.user_data[TEMP_LINKS_KEY][index]
        await query.edit_message_text("⏳ تحميل الكتاب من المكتبة العربية...")
        await download_and_send_pdf(context, query.message.chat_id, link, title=f"book_{index+1}.pdf")

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
