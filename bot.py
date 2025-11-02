# main.py
import os
import asyncio
import tempfile
import aiofiles
import random
import json
import re
import subprocess
from urllib.parse import urljoin, urlparse
from aiohttp import ClientSession, ClientTimeout
from bs4 import BeautifulSoup
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from ddgs import DDGS

# ------------ Optional Playwright import (may fail in Railway) ------------
USE_BROWSER = os.getenv("USE_BROWSER", "false").lower() in ("1", "true", "yes")
_playwright_available = False
_playwright_import_error = None
if USE_BROWSER:
    try:
        from playwright.async_api import async_playwright, Page
        _playwright_available = True
    except Exception as e:
        _playwright_import_error = e
        _playwright_available = False

# ---------------- CONFIG ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
USER_AGENT_HEADER = {'User-Agent': USER_AGENT}
MIN_PDF_SIZE_BYTES = int(os.getenv("MIN_PDF_SIZE_BYTES", 50 * 1024))
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

# ---------------- Helpers ----------------
def absolute_url(base: str, link: str):
    try:
        return urljoin(base, link)
    except:
        return link

async def fetch_text(session: ClientSession, url: str, referer: str = None, timeout=15):
    headers = USER_AGENT_HEADER.copy()
    if referer:
        headers['Referer'] = referer
    try:
        async with session.get(url, headers=headers, allow_redirects=True, timeout=ClientTimeout(total=timeout)) as resp:
            text = await resp.text(errors='ignore')
            return resp.status, resp.headers, text, str(resp.url)
    except Exception:
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

# Extract urls from inline JS or onclick
def extract_urls_from_js(js_text: str, base: str):
    results = set()
    patterns = [
        r'window\.location(?:\.href)?\s*=\s*[\'"]([^\'"]+)[\'"]',
        r'location\.href\s*=\s*[\'"]([^\'"]+)[\'"]',
        r'window\.open\s*\(\s*[\'"]([^\'"]+)[\'"]',
        r'["\'](https?:\/\/[^"\']+\.pdf[^"\']*)["\']'
    ]
    for p in patterns:
        for m in re.finditer(p, js_text, re.IGNORECASE):
            results.add(absolute_url(base, m.group(1)))
    for m in PDF_LIKE_REGEX.finditer(js_text):
        results.add(m.group(1))
    return list(results)

# parse html for candidates
async def extract_pdf_candidate_from_html(html: str, base_url: str):
    candidates = []
    soup = BeautifulSoup(html, "html.parser")

    for a in soup.find_all('a', href=True):
        href = a['href'].strip()
        full = absolute_url(base_url, href)
        low = full.lower()
        if low.endswith('.pdf') or 'drive.google.com' in low or 'dropbox.com' in low or 'archive.org/download' in low or 'archive.org/stream' in low:
            candidates.append(full)
        if 'download' in low and ('/file' in low or low.endswith('/download') or 'dl=' in low):
            candidates.append(full)

    for tag in soup.find_all(True, attrs=True):
        for attr in ('data-href', 'data-url', 'data-download', 'data-link'):
            if tag.has_attr(attr):
                candidates.append(absolute_url(base_url, tag[attr]))

    for tag in soup.find_all(True, onclick=True):
        onclick = tag['onclick']
        for url in extract_urls_from_js(onclick, base_url):
            candidates.append(url)

    m = soup.find('meta', attrs={'http-equiv': re.compile(r'refresh', re.I)})
    if m and m.get('content'):
        content = m['content']
        parts = content.split(';')
        if len(parts) == 2 and 'url=' in parts[1].lower():
            url_part = parts[1].split('=', 1)[1].strip(' "\'')
            candidates.append(absolute_url(base_url, url_part))

    for script in soup.find_all('script'):
        script_text = script.string or ''
        for url in extract_urls_from_js(script_text, base_url):
            candidates.append(url)
        for mm in PDF_LIKE_REGEX.finditer(script_text):
            candidates.append(absolute_url(base_url, mm.group(1)))

    return list(dict.fromkeys([c for c in candidates if c]))

# form submit helper
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

# ---------------- No-browser extractor (polling/wait simulation) ----------------
async def get_pdf_link_from_page_no_browser(start_url: str, max_wait_seconds=30):
    async with ClientSession() as session:
        referer = start_url
        low = start_url.lower()
        if low.endswith('.pdf') or 'drive.google.com' in low or 'dropbox.com' in low or 'archive.org' in low:
            return start_url, start_url

        status, headers, text, final_url = await fetch_text(session, start_url, referer=start_url, timeout=15)
        if not text:
            return None, None

        candidates = await extract_pdf_candidate_from_html(text, final_url)

        soup = BeautifulSoup(text, "html.parser")
        for form in soup.find_all('form'):
            form_text = str(form).lower()
            if 'download' in form_text or 'get' in form_text or 'submit' in form_text or 'file' in form_text:
                st, hd, tx, fu = await try_submit_form(session, form, final_url, referer=start_url)
                if tx:
                    more = await extract_pdf_candidate_from_html(tx, fu or final_url)
                    candidates.extend(more)
                    final_url = fu or final_url

        for m in PDF_LIKE_REGEX.finditer(text):
            candidates.append(absolute_url(final_url, m.group(1)))
        for m in DRIVE_REGEX.finditer(text):
            candidates.append(absolute_url(final_url, m.group(1)))
        for m in DROPBOX_REGEX.finditer(text):
            candidates.append(absolute_url(final_url, m.group(1)))
        for m in ARCHIVE_REGEX.finditer(text):
            candidates.append(absolute_url(final_url, m.group(1)))

        seen = set()
        candidates = [x for x in candidates if x and not (x in seen or seen.add(x))]

        for candidate in candidates:
            st, ctype, clen = await head_check(session, candidate, referer=final_url)
            if ctype and ('pdf' in ctype or 'octet-stream' in ctype) and (clen is None or clen >= MIN_PDF_SIZE_BYTES):
                return candidate, final_url

        wait_interval = 5
        attempts = max(1, max_wait_seconds // wait_interval)
        for i in range(attempts):
            await asyncio.sleep(wait_interval)
            status, headers, text, final_url = await fetch_text(session, start_url, referer=referer, timeout=15)
            if not text:
                continue
            candidates = await extract_pdf_candidate_from_html(text, final_url)
            for m in PDF_LIKE_REGEX.finditer(text):
                candidates.append(absolute_url(final_url, m.group(1)))
            seen = set()
            candidates = [x for x in candidates if x and not (x in seen or seen.add(x))]
            for candidate in candidates:
                st, ctype, clen = await head_check(session, candidate, referer=final_url)
                if ctype and ('pdf' in ctype or 'octet-stream' in ctype) and (clen is None or clen >= MIN_PDF_SIZE_BYTES):
                    return candidate, final_url

        try:
            async with ClientSession().get(start_url, headers=USER_AGENT_HEADER, allow_redirects=True, timeout=ClientTimeout(total=15)) as resp:
                final = str(resp.url)
                if final and final.lower().endswith('.pdf'):
                    st, ctype, clen = await head_check(session, final, referer=start_url)
                    if ctype and ('pdf' in ctype or 'octet-stream' in ctype):
                        return final, start_url
        except Exception:
            pass

        return None, None

# ---------------- Optional Playwright-based extractor ----------------
async def get_pdf_link_from_page_with_browser(link: str):
    """Use Playwright if available. Caller must ensure _playwright_available True."""
    try:
        # try to ensure browsers are installed if requested (best-effort)
        try:
            # attempt a one-time install (may fail on Railway)
            subprocess.run(["playwright", "install", "chromium"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
            context = await browser.new_context(user_agent=USER_AGENT, viewport={'width': 1280,'height':720})
            page = await context.new_page()
            try:
                await page.goto(link, wait_until="networkidle", timeout=30000)
            except Exception:
                pass

            # try to click common download selectors (best-effort)
            download_selector_css = 'a[href*="pdf"], a.book-dl-btn, a.btn-download, button:has-text("تحميل"), a:has-text("Download"), a:has-text("ابدأ التحميل")'
            try:
                # wait a bit then try a click
                await asyncio.sleep(1)
                await page.locator(download_selector_css).click(timeout=6000, force=True)
            except Exception:
                pass

            # wait network traffic for pdf
            try:
                resp = await page.wait_for_response(lambda r: 'application/pdf' in r.headers.get('content-type','') or r.url.lower().endswith('.pdf'), timeout=12000)
                pdf_url = resp.url
            except Exception:
                pdf_url = None

            # last tries: search links in frames or html
            if not pdf_url:
                content = await page.content()
                soup = BeautifulSoup(content, "html.parser")
                for a in soup.find_all('a', href=True):
                    href = absolute_url(link, a['href'])
                    if href.lower().endswith('.pdf'):
                        pdf_url = href
                        break
                # frames
                if not pdf_url:
                    for frame in page.frames:
                        try:
                            for a_el in await frame.query_selector_all("a[href]"):
                                href = await a_el.get_attribute("href")
                                if href and href.lower().endswith('.pdf'):
                                    pdf_url = absolute_url(link, href)
                                    break
                        except Exception:
                            continue

            await context.storage_state(path="browser_state.json")
            await browser.close()
            return pdf_url, link
    except Exception as e:
        # any browser error -> return None to let fallback work
        try:
            await browser.close()
        except:
            pass
        return None, None

# ---------------- Unified getter (tries browser then fallback) ----------------
async def get_pdf_link(link: str, prefer_browser: bool = False):
    # prefer_browser means try browser first if available
    if prefer_browser and _playwright_available:
        pdf_link, referer = await get_pdf_link_from_page_with_browser(link)
        if pdf_link:
            return pdf_link, referer
    # otherwise try no-browser method
    pdf_link, referer = await get_pdf_link_from_page_no_browser(link, max_wait_seconds=30)
    if pdf_link:
        return pdf_link, referer
    # if playwright is available and not tried yet, try it as last resort
    if _playwright_available and not prefer_browser:
        pdf_link, referer = await get_pdf_link_from_page_with_browser(link)
        return pdf_link, referer
    return None, None

# ---------------- Download/send helper ----------------
async def download_and_send_pdf(context, chat_id, source, title="book.pdf", referer_link=None):
    pdf_url = source
    download_headers = USER_AGENT_HEADER.copy()
    if referer_link:
        download_headers['Referer'] = referer_link

    async with ClientSession() as session:
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

# ---------------- Telegram handlers ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📚 بوت التحميل جاهز. استخدم /search <اسم الكتاب> أو أرسل رابط مباشر.")

async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("استخدم: /search اسم الكتاب أو رابط.")
        return
    msg = await update.message.reply_text(f"🔍 أبحث عن **{query}** ...")
    try:
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

    await query.edit_message_text("⏳ محاولة استخراج رابط التحميل... (هذا قد يستغرق بعض الثواني)")
    # try browser if user requested and playwright available
    prefer_browser = USE_BROWSER and _playwright_available
    if USE_BROWSER and not _playwright_available:
        # warn the user in the logs
        print("⚠️ USE_BROWSER=true لكن Playwright غير متوفر:", _playwright_import_error)

    pdf_link, referer = await get_pdf_link(link, prefer_browser=prefer_browser)
    if pdf_link:
        await download_and_send_pdf(context, query.message.chat_id, pdf_link, title="book", referer_link=referer)
    else:
        text = f"📄 لم أتمكن من استخلاص رابط PDF من المصدر: {link}\n\n"
        if USE_BROWSER and not _playwright_available:
            text += "ملاحظة: طلبت وضع المتصفح (USE_BROWSER=true) لكن Playwright غير مُثبت في البيئة. لتفعيل المتصفح على الخادم تحتاج إلى نشر عبر Docker وإضافة متطلبات Playwright."
        else:
            text += "ملاحظة: إن كان الموقع يعتمد على تنفيذ جافاسكربت معقّد أو CAPTCHA فذلك يتطلب متصفحًا حقيقياً."
        await context.bot.send_message(chat_id=query.message.chat_id, text=text)

# ---------------- Main ----------------
def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN مفقود في المتغيرات البيئية.")
    print("Starting bot. USE_BROWSER =", USE_BROWSER, "playwright_available =", _playwright_available)
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.run_polling()

if __name__ == "__main__":
    main()
