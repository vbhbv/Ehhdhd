!/usr/bin/env python3
import os
import asyncio
import tempfile
import aiofiles
from aiohttp import ClientSession, ClientTimeout
from bs4 import BeautifulSoup
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
import html
import time

# ---- CONFIG: use environment variables (set these on Railway/Replit) ----
BOT_TOKEN = os.getenv("BOT_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
CSE_ID = os.getenv("CSE_ID")  # Search Engine ID (CX)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required")
if not GOOGLE_API_KEY:
    raise RuntimeError("GOOGLE_API_KEY environment variable is required")
if not CSE_ID:
    raise RuntimeError("CSE_ID environment variable is required")

# ---- basic rate-limit (in-memory) to prevent abuse ----
USER_REQUESTS = {}  # user_id -> (last_request_ts, count)
RATE_LIMIT_SECONDS = 2  # minimal seconds between search requests per user

# ---- utilities ----
async def google_search(query: str, num: int = 5):
    """Query Google Custom Search API and return list of items (title, link, snippet)."""
    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": GOOGLE_API_KEY,
        "cx": CSE_ID,
        "q": query,
        "num": str(num),
    }
    timeout = ClientTimeout(total=15)
    async with ClientSession(timeout=timeout) as session:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Google API error {resp.status}: {text[:200]}")
            data = await resp.json()
    items = []
    for it in data.get("items", []):
        title = html.unescape(it.get("title", ""))
        link = it.get("link", "")
        snippet = html.unescape(it.get("snippet", ""))
        items.append({"title": title, "link": link, "snippet": snippet})
    return items

async def fetch_page_html(url: str, session: ClientSession, timeout_s: int = 20):
    """Return HTML text of a page (or None if failed)."""
    try:
        async with session.get(url, timeout=ClientTimeout(total=timeout_s), allow_redirects=True) as resp:
            if resp.status != 200:
                return None
            return await resp.text()
    except Exception:
        return None

def find_pdf_link_in_html(html_text: str, base_url: str = ""):
    """Parse HTML and look for PDF links in <a> and <iframe> tags."""
    soup = BeautifulSoup(html_text, "html.parser")
    # search anchor tags
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if ".pdf" in href.lower():
            # handle relative urls simply by returning href (aiohttp will handle some)
            return href
    # search iframe src
    for ifr in soup.find_all("iframe", src=True):
        src = ifr["src"].strip()
        if ".pdf" in src.lower():
            return src
    return None

async def resolve_pdf_link(candidate_link: str):
    """Given a link from Google results, try to get a direct PDF link.
       Returns absolute link or None.
    """
    # If direct pdf link already:
    if candidate_link.lower().endswith(".pdf") or ".pdf" in candidate_link.lower():
        return candidate_link

    timeout = ClientTimeout(total=15)
    async with ClientSession(timeout=timeout) as session:
        html_text = await fetch_page_html(candidate_link, session)
        if not html_text:
            return None
        pdf_link = find_pdf_link_in_html(html_text, base_url=candidate_link)
        if pdf_link:
            # If link is relative, try to join with candidate base
            if pdf_link.startswith("//"):
                # protocol-relative
                if candidate_link.startswith("https:"):
                    pdf_link = "https:" + pdf_link
                else:
                    pdf_link = "http:" + pdf_link
            elif pdf_link.startswith("/"):
                # relative path: build from base
                from urllib.parse import urljoin
                pdf_link = urljoin(candidate_link, pdf_link)
            return pdf_link
    return None

async def download_and_send_pdf(context, chat_id, pdf_url, filename="book.pdf"):
    """Download file temporarily and send it; then delete the temp file."""
    tmp_dir = tempfile.gettempdir()
    # sanitize filename
    safe_name = filename.replace("/", "_")[:120]
    if not safe_name.lower().endswith(".pdf"):
        safe_name += ".pdf"
    file_path = os.path.join(tmp_dir, safe_name)
    timeout = ClientTimeout(total=120)
    try:
        async with ClientSession(timeout=timeout) as session:
            async with session.get(pdf_url) as resp:
                if resp.status != 200:
                    await context.bot.send_message(chat_id=chat_id, text=f"⚠️ خطأ في تحميل الملف ({resp.status}).")
                    return False
                # stream to file
                f = await aiofiles.open(file_path, "wb")
                await f.write(await resp.read())
                await f.close()
        # send document
        await context.bot.send_document(chat_id=chat_id, document=open(file_path, "rb"))
        return True
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"⚠️ خطأ أثناء التحميل أو الإرسال: {e}")
        return False
    finally:
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass

# ---- Telegram handlers ----
async def start(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أهلًا! أرسل: /search <اسم الكتاب أو المؤلف>\n"
        "مثال: /search ابن تيمية\n\n"
        "البوت يبحث عبر Google (محرك البحث المخصّص) في مواقع الكتب العربية."
    )

async def help_cmd(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("استخدم: /search اسم الكتاب أو المؤلف")

async def search_cmd(update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    now = time.time()
    last_ts, _ = USER_REQUESTS.get(user_id, (0, 0))
    if now - last_ts < RATE_LIMIT_SECONDS:
        await update.message.reply_text("⏳ انتظر قليلاً قبل طلب بحث آخر.")
        return
    USER_REQUESTS[user_id] = (now, 1)

    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("استخدم: /search اسم الكتاب أو المؤلف")
        return

    msg = await update.message.reply_text("🔎 أبحث... يرجى الانتظار.")
    try:
        items = await google_search(query, num=6)
    except Exception as e:
        await msg.edit_text(f"⚠️ خطأ في البحث عبر Google: {e}")
        return

    if not items:
        await msg.edit_text("❌ لم أجد نتائج عبر محرك البحث.")
        return

    # build result list
    buttons = []
    lines = []
    for i, it in enumerate(items[:6], start=1):
        t = it.get("title") or "بدون عنوان"
        link = it.get("link") or ""
        lines.append(f"{i}. {t}")
        buttons.append([InlineKeyboardButton(f"📥 تنزيل {i}", callback_data=f"dl|{link}")])

    await msg.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))

async def callback_handler(update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if not data.startswith("dl|"):
        return
    link = data.split("|", 1)[1]
    await query.edit_message_text("⏳ أبحث عن ملف PDF في المصدر...")

    # 1) if link seems direct pdf
    pdf_candidate = None
    if ".pdf" in link.lower():
        pdf_candidate = link

    # 2) try resolve if not direct
    if not pdf_candidate:
        try:
            pdf_candidate = await resolve_pdf_link(link)
        except Exception:
            pdf_candidate = None

    if not pdf_candidate:
        await context.bot.send_message(chat_id=query.message.chat_id, text=f"❌ لم أجد رابط PDF مباشر في هذا المصدر:\n{link}")
        return

    # 3) attempt download & send
    await context.bot.send_message(chat_id=query.message.chat_id, text="🔽 جاري تحميل الملف وإرساله...")
    filename = link.split("/")[-1] or "book.pdf"
    success = await download_and_send_pdf(context, query.message.chat_id, pdf_candidate, filename=filename)
    if success:
        await context.bot.send_message(chat_id=query.message.chat_id, text="✅ تم الإرسال (الملف محذوف محليًا بعد الإرسال).")
    else:
        await context.bot.send_message(chat_id=query.message.chat_id, text="❌ تعذّر إرسال الملف.")

async def unknown_msg(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أمر غير معروف. استخدم /search <اسم الكتاب أو المؤلف>")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), unknown_msg))

    print("Bot started...")
    app.run_polling()

if __name__ == "__main__":
    main()
