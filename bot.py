# main.py
import os
import asyncio
import tempfile
import aiofiles
import random
import json
import re
from urllib.parse import urljoin, urlparse, parse_qs
from aiohttp import ClientSession, ClientTimeout
from bs4 import BeautifulSoup
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from ddgs import DDGS

# ---------------- CONFIG ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
USER_AGENT_HEADER = {'User-Agent': USER_AGENT}
MIN_PDF_SIZE_BYTES = 50 * 1024
TEMP_LINKS_KEY = "current_search_links"

TRUSTED_DOMAINS = [
    "ketabpedia.com",
    "scribd.com",
    "sahm-book.com",
    "8ghrb.com",
    "mktbtypdf.com",
    "foulabook.com",
    "archive.org",
    "kotobati.com",
    "masaha.org"
]

PDF_LIKE_REGEX = re.compile(r'(https?:\/\/[^\s"\']+\.pdf(\?[^"\']*)?)', re.IGNORECASE)
DRIVE_REGEX = re.compile(r'(https?:\/\/(?:drive\.google\.com|docs\.google\.com)[^\s"\']*)', re.IGNORECASE)
DROPBOX_REGEX = re.compile(r'(https?:\/\/(?:www\.)?dropbox\.com[^\s"\']*)', re.IGNORECASE)
ARCHIVE_REGEX = re.compile(r'(https?:\/\/(?:archive\.org|ia801)[^\s"\']*)', re.IGNORECASE)

# ------------- Utility helpers -------------
async def fetch_text(session: ClientSession, url: str, referer: str = None, timeout=15):
    headers = USER_AGENT_HEADER.copy()
    if referer:
        headers['Referer'] = referer
    try:
        async with session.get(url, headers=headers, allow_redirects=True, timeout=ClientTimeout(total=timeout)) as resp:
            text = await resp.text(errors='ignore')
            return resp.status, resp.headers, text, str(resp.url)
    except Exception as e:
        return None, None, None, None

async def head_check(session: ClientSession, url: str, referer: str = None, timeout=10):
    headers = USER_AGENT_HEADER.copy()
    if referer:
        headers['Referer'] = referer
    try:
        async with session.head(url, headers=headers, allow_redirects=True, timeout=ClientTimeout(total=timeout)) as head_resp:
            content_type = head_resp.headers.get('Content-Type', '').lower()
            content_length = int(head_resp.headers.get('Content-Length', 0) or 0)
            return head_resp.status, content_type, content_length
    except Exception:
        # fallback to GET small-range
        try:
            async with session.get(url, headers=headers, allow_redirects=True, timeout=ClientTimeout(total=timeout)) as resp:
                content_type = resp.headers.get('Content-Type', '').lower()
                content_length = int(resp.headers.get('Content-Length', 0) or 0)
                return resp.status, content_type, content_length
        except Exception:
            return None, None, None

def absolute_url(base: str, link: str):
    try:
        return urljoin(base, link)
    except:
        return link

def extract_urls_from_js(js_text: str, base: str):
    results = set()
    # common patterns window.location='URL' or location.href="URL" or window.open("URL")
    patterns = [
        r'window\.location(?:\.href)?\s*=\s*[\'"]([^\'"]+)[\'"]',
        r'location\.href\s*=\s*[\'"]([^\'"]+)[\'"]',
        r'window\.open\s*\(\s*[\'"]([^\'"]+)[\'"]',
        r'["\'](https?:\/\/[^"\']+\.pdf[^"\']*)["\']'
    ]
    for p in patterns:
        for m in re.finditer(p, js_text, re.IGNORECASE):
            results.add(absolute_url(base, m.group(1)))
    # also raw pdf urls inside JS
    for m in PDF_LIKE_REGEX.finditer(js_text):
        results.add(m.group(1))
    return list(results)

async def try_submit_form(session: ClientSession, form, base_url: str, referer: str = None):
    action = form.get('action') or base_url
    method = (form.get('method') or 'get').lower()
    data = {}
    for inp in form.find_all('input'):
        name = inp.get('name')
        if not name:
            continue
        val = inp.get('value', '')
        data[name] = val
    target = absolute_url(base_url, action)
    headers = USER_AGENT_HEADER.copy()
    if referer:
        headers['Referer'] = referer
    try:
        if method == 'post':
            async with session.post(target, headers=headers, data=data, allow_redirects=True, timeout=ClientTimeout(total=15)) as resp:
                text = await resp.text(errors='ignore')
                return resp.status, resp.headers, text, str(resp.url)
        else:
            async with session.get(target, headers=headers, params=data, allow_redirects=True, timeout=ClientTimeout(total=15)) as resp:
                text = await resp.text(errors='ignore')
                return resp.status, resp.headers, text, str(resp.url)
    except Exception:
        return None, None, None, None

# ------------- Core extractor (no browser) -------------
async def extract_pdf_candidate_from_html(html: str, base_url: str):
    """Returns list of candidate URLs found inside page HTML or scripts."""
    candidates = []
    soup = BeautifulSoup(html, "html.parser")

    # 1) direct <a href> to PDF or known hosts
    for a in soup.find_all('a', href=True):
        href = a['href'].strip()
        full = absolute_url(base_url, href)
        low = full.lower()
        if low.endswith('.pdf') or 'drive.google.com' in low or 'dropbox.com' in low or 'archive.org/download' in low or 'archive.org/stream' in low:
            candidates.append(full)
        # sometimes link leads to an intermediate page with 'download' in path
        if 'download' in low and ('/file' in low or low.endswith('/download') or 'dl=' in low):
            candidates.append(full)

    # 2) data-href / data-url attributes or buttons
    for tag in soup.find_all(True, attrs=True):
        for attr in ('data-href', 'data-url', 'data-download', 'data-link'):
            if tag.has_attr(attr):
                candidates.append(absolute_url(base_url, tag[attr]))

    # 3) onclick attributes (looking for location or window.open)
    for tag in soup.find_all(True, onclick=True):
        onclick = tag['onclick']
        for url in extract_urls_from_js(onclick, base_url):
            candidates.append(url)

    # 4) meta refresh
    m = soup.find('meta', attrs={'http-equiv': re.compile(r'refresh', re.I)})
    if m and m.get('content'):
        content = m['content']
        parts = content.split(';')
        if len(parts) == 2 and 'url=' in parts[1].lower():
            url_part = parts[1].split('=', 1)[1].strip(' "\'')
            candidates.append(absolute_url(base_url, url_part))

    # 5) scripts: search inline JS for urls
    for script in soup.find_all('script'):
        if script.string:
            for url in extract_urls_from_js(script.string, base_url):
                candidates.append(url)
            # raw pdf regex
            for m in PDF_LIKE_REGEX.finditer(script.string):
                candidates.append(absolute_url(base_url, m.group(1)))

    # 6) forms (we will try to submit later)
    forms = soup.find_all('form')

    return list(dict.fromkeys([c for c in candidates if c]))

# ------------- High-level: follow redirects/wait/poll -------------
async def get_pdf_link_from_page_no_browser(start_url: str, max_wait_seconds=30):
    """
    Try to extract a workable PDF URL without using a real browser.
    Returns: (pdf_url or None, referer_used or None)
    """
    async with ClientSession() as session:
        referer = start_url
        # If start_url already looks like a pdf or known host, return it
        low = start_url.lower()
        if low.endswith('.pdf') or 'drive.google.com' in low or 'dropbox.com' in low or 'archive.org' in low:
            return start_url, start_url

        # initial fetch
        try:
            status, headers, text, final_url = await fetch_text(session, start_url, referer=start_url, timeout=15)
        except Exception:
            return None, None

        if not text:
            return None, None

        # Try to extract candidates from this HTML
        candidates = await extract_pdf_candidate_from_html(text, final_url)

        # Also try to submit forms found (some sites require form submit to get redirect)
        soup = BeautifulSoup(text, "html.parser")
        for form in soup.find_all('form'):
            # Heuristic: if form contains 'download' or 'getfile' or 'submit' in attributes/names
            form_text = str(form).lower()
            if 'download' in form_text or 'get' in form_text or 'submit' in form_text or 'file' in form_text:
                st, hd, tx, fu = await try_submit_form(session, form, final_url, referer=start_url)
                if tx:
                    more = await extract_pdf_candidate_from_html(tx, fu or final_url)
                    candidates.extend(more)
                    # update final_url for further checks
                    final_url = fu or final_url

        # regex search across entire page (catch hidden links in JS)
        for m in PDF_LIKE_REGEX.finditer(text):
            candidates.append(absolute_url(final_url, m.group(1)))
        for m in DRIVE_REGEX.finditer(text):
            candidates.append(absolute_url(final_url, m.group(1)))
        for m in DROPBOX_REGEX.finditer(text):
            candidates.append(absolute_url(final_url, m.group(1)))
        for m in ARCHIVE_REGEX.finditer(text):
            candidates.append(absolute_url(final_url, m.group(1)))

        # Deduplicate preserving order
        seen = set()
        candidates = [x for x in candidates if x and not (x in seen or seen.add(x))]

        # If found direct candidate, validate them
        for candidate in candidates:
            st, ctype, clen = await head_check(session, candidate, referer=final_url)
            if ctype and ('pdf' in ctype or 'octet-stream' in ctype) and (clen is None or clen >= MIN_PDF_SIZE_BYTES):
                return candidate, final_url

        # If none found yet, do polling / wait attempts to cover 20s countdown scenarios
        # We'll poll the page every 5 seconds up to max_wait_seconds
        wait_interval = 5
        attempts = max(1, max_wait_seconds // wait_interval)
        for i in range(attempts):
            await asyncio.sleep(wait_interval)
            try:
                status, headers, text, final_url = await fetch_text(session, start_url, referer=referer, timeout=15)
            except Exception:
                continue
            if not text:
                continue

            # re-scan page for candidates
            candidates = await extract_pdf_candidate_from_html(text, final_url)
            # check scripts and regex again
            for m in PDF_LIKE_REGEX.finditer(text):
                candidates.append(absolute_url(final_url, m.group(1)))
            # dedupe
            seen = set()
            candidates = [x for x in candidates if x and not (x in seen or seen.add(x))]

            for candidate in candidates:
                st, ctype, clen = await head_check(session, candidate, referer=final_url)
                if ctype and ('pdf' in ctype or 'octet-stream' in ctype) and (clen is None or clen >= MIN_PDF_SIZE_BYTES):
                    return candidate, final_url

        # last resort: follow redirects from initial URL by doing a GET and inspecting history
        try:
            async with session.get(start_url, headers=USER_AGENT_HEADER, allow_redirects=True, timeout=ClientTimeout(total=15)) as resp:
                # check final url
                final = str(resp.url)
                if final and final.lower().endswith('.pdf'):
                    st, ctype, clen = await head_check(session, final, referer=start_url)
                    if ctype and ('pdf' in ctype or 'octet-stream' in ctype):
                        return final, start_url
        except Exception:
            pass

        return None, None

# ------------- Download and send -------------
async def download_and_send_pdf(context, chat_id, source, title="book.pdf", referer_link=None):
    # source may be a URL or a local path (we only use URLs in this no-browser version)
    pdf_url = source
    download_headers = USER_AGENT_HEADER.copy()
    if referer_link:
        download_headers['Referer'] = referer_link

    async with ClientSession() as session:
        # HEAD check with fallback
        try:
            st, ctype, clen = await head_check(session, pdf_url, referer=referer_link)
            if not ctype or ('pdf' not in ctype and 'octet-stream' not in ctype):
                await context.bot.send_message(chat_id=chat_id, text=f"⚠️ فشل: الرابط لا يبدو PDF ({ctype}).")
                return
            if clen is not None and clen < MIN_PDF_SIZE_BYTES:
                await context.bot.send_message(chat_id=chat_id, text="⚠️ فشل: الملف صغير جدًا.")
                return
        except Exception as e:
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ خطأ أثناء التحقق من الملف: {e}")
            return

        tmp_dir = tempfile.gettempdir()
        safe_title = (title or "book").replace("/", "_")[:60]
        file_path = os.path.join(tmp_dir, f"{safe_title}.pdf")

        try:
            async with session.get(pdf_url, headers=download_headers, allow_redirects=True, timeout=ClientTimeout(total=120)) as resp:
                if resp.status != 200:
                    await context.bot.send_message(chat_id=chat_id, text=f"⚠️ فشل تحميل الملف (status={resp.status}).")
                    return
                content = await resp.read()
                async with aiofiles.open(file_path, "wb") as f:
                    await f.write(content)
        except Exception as e:
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ خطأ أثناء تنزيل الملف: {e}")
            return

    # إرسال الملف
    try:
        with open(file_path, "rb") as f:
            await context.bot.send_document(chat_id=chat_id, document=f)
        await context.bot.send_message(chat_id=chat_id, text="✅ تم إرسال الكتاب.")
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"⚠️ خطأ أثناء إرسال الملف: {e}")
    finally:
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except:
            pass

# ------------- Telegram handlers -------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📚 بوت التحميل الجاهز! استخدم /search <اسم الكتاب> أو أرسل رابط مباشر.")

async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("استخدم: /search اسم الكتاب أو الصلة.")
        return

    msg = await update.message.reply_text(f"🔍 أبحث عن **{query}** ...")
    try:
        # use DDGS
        results = []
        with DDGS(timeout=5) as ddgs:
            q = " OR ".join([f"site:{d}" for d in TRUSTED_DOMAINS])
            full_q = f"{query} filetype:pdf OR {q}"
            for r in ddgs.text(full_q, max_results=10):
                link = r.get('href')
                title = r.get('title') or link
                if link and title:
                    results.append({"title": title.strip(), "link": link})
        unique = {}
        for it in results:
            unique[it['link']] = it
        results = list(unique.values())[:6]
        if not results:
            await msg.edit_text("❌ لم أجد نتائج موثوقة.")
            return

        context.user_data[TEMP_LINKS_KEY] = [it['link'] for it in results]
        buttons = []
        lines = []
        for i, it in enumerate(results):
            lines.append(f"{i+1}. {it['title'][:100]}")
            buttons.append([InlineKeyboardButton(f"📥 تحميل {i+1}", callback_data=f"dl|{i}")])
        await msg.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        await msg.edit_text(f"⚠️ خطأ أثناء البحث: {e}")

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if not data.startswith("dl|"):
        return
    try:
        idx = int(data.split("|", 1)[1])
        link = context.user_data[TEMP_LINKS_KEY][idx]
    except Exception:
        await query.message.reply_text("⚠️ خطأ: الرابط غير صالح.")
        return

    await query.edit_message_text("⏳ أحاول استخراج رابط التحميل (بدون متصفح)... يرجى الانتظار.")
    pdf_link, referer = await get_pdf_link_from_page_no_browser(link, max_wait_seconds=30)
    if pdf_link:
        await download_and_send_pdf(context, query.message.chat_id, pdf_link, title="book", referer_link=referer)
    else:
        await context.bot.send_message(chat_id=query.message.chat_id, text=f"📄 لم أتمكن من استخلاص رابط PDF من المصدر: {link}\n\nملاحظة: إن كان الموقع يعتمد على تنفيذ جافاسكربت معقد أو CAPTCHA فذلك يتطلب متصفحًا حقيقياً.")

# ------------- Main -------------
def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN مفقود في المتغيرات البيئية.")
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler))
    print("✅ البوت يعمل (بدون Playwright).")
    app.run_polling()

if __name__ == "__main__":
    main()
