# main.py - Advanced extractor: focused search domains + JS endpoint probing + optional Playwright
import os
import re
import asyncio
import tempfile
import aiofiles
import json
import base64
import random
from urllib.parse import urljoin, urlparse
from aiohttp import ClientSession, ClientTimeout
from bs4 import BeautifulSoup
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from ddgs import DDGS

# ---------------- CONFIG ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
USE_BROWSER = os.getenv("USE_BROWSER", "false").lower() in ("1", "true", "yes")
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
USER_AGENT_HEADER = {'User-Agent': USER_AGENT}
MIN_PDF_SIZE_BYTES = int(os.getenv("MIN_PDF_SIZE_BYTES", 50 * 1024))
TEMP_LINKS_KEY = "current_search_links"

# ---------------- TRUSTED DOMAINS (50) ----------------
TRUSTED_DOMAINS = [
"archive.org","openlibrary.org","gutenberg.org","hathitrust.org","doabooks.org",
"arxiv.org","core.ac.uk","semanticscholar.org","researchgate.net","academia.edu",
"ncbi.nlm.nih.gov","europeana.eu","gallica.bnf.fr","dlib.org","openedition.org",
"jstor.org","kotobati.com","foulabook.com","ketabpedia.com","mktbtypdf.com",
"scribd.com","noor-book.com","masaha.org","kotobpdf.com","sahm-book.com",
"8ghrb.com","islamhouse.com","bvm.digital","biblioteca.org.ar","nla.gov.au",
"worldcat.org","s3.amazonaws.com","dropbox.com","drive.google.com","dl.dropboxusercontent.com",
"projectmuse.org","oapen.org","oapen-uk.org","oare.edu","oercommons.org",
"b-ok.org","libgen.rs","libgen.is","archive.is","web.archive.org","openresearchlibrary.org",
"bookfi.net","catalog.hathitrust.org","hdl.handle.net","research-Repository"  # placeholders for institutional repos
]

# Regex helpers
PDF_LIKE_REGEX = re.compile(r'(https?:\/\/[^\s"\']+\.pdf(\?[^"\']*)?)', re.IGNORECASE)
DRIVE_REGEX = re.compile(r'(https?:\/\/(?:drive\.google\.com|docs\.google\.com)[^\s"\']*)', re.IGNORECASE)
DROPBOX_REGEX = re.compile(r'(https?:\/\/(?:www\.)?dropbox\.com[^\s"\']*)', re.IGNORECASE)
ARCHIVE_REGEX = re.compile(r'(https?:\/\/(?:archive\.org|web\.archive\.org)[^\s"\']*)', re.IGNORECASE)

# Optional Playwright import (best-effort)
_playwright_available = False
_playwright_err = None
if USE_BROWSER:
    try:
        from playwright.async_api import async_playwright, Page
        _playwright_available = True
    except Exception as e:
        _playwright_err = e
        _playwright_available = False

# ---------------- Helper functions ----------------
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
            final = str(resp.url)
            print(f"[fetch_text] {url} -> {resp.status} final={final}")
            return resp.status, resp.headers, text, final
    except Exception as e:
        print(f"[fetch_text] ERROR {url}: {e}")
        return None, None, None, None

async def head_check(session: ClientSession, url: str, referer: str = None, timeout=10):
    headers = USER_AGENT_HEADER.copy()
    if referer:
        headers['Referer'] = referer
    try:
        async with session.head(url, headers=headers, allow_redirects=True, timeout=ClientTimeout(total=timeout)) as head_resp:
            ctype = head_resp.headers.get('Content-Type', '').lower()
            clen = int(head_resp.headers.get('Content-Length', 0) or 0)
            print(f"[head_check] HEAD {url} -> {head_resp.status} {ctype} {clen}")
            return head_resp.status, ctype, clen
    except Exception as e:
        print(f"[head_check] HEAD failed {url}: {e}, trying GET fallback")
        try:
            async with session.get(url, headers=headers, allow_redirects=True, timeout=ClientTimeout(total=timeout)) as resp:
                ctype = resp.headers.get('Content-Type', '').lower()
                clen = int(resp.headers.get('Content-Length', 0) or 0)
                print(f"[head_check] GET fallback {url} -> {resp.status} {ctype} {clen}")
                return resp.status, ctype, clen
        except Exception as e2:
            print(f"[head_check] GET fallback failed {url}: {e2}")
            return None, None, None

# JS parsing helpers
def extract_urls_from_js(js_text: str, base: str):
    res = set()
    if not js_text:
        return []
    patterns = [
        r'window\.location(?:\.href)?\s*=\s*[\'"]([^\'"]+)[\'"]',
        r'location\.href\s*=\s*[\'"]([^\'"]+)[\'"]',
        r'window\.open\s*\(\s*[\'"]([^\'"]+)[\'"]',
        r'setTimeout\s*\(\s*function\s*\(\)\s*\{\s*window\.location\s*=\s*[\'"]([^\'"]+)[\'"]',
        r'fetch\(\s*[\'"]([^\'"]+)[\'"]',
        r'["\'](https?:\/\/[^"\']+\.pdf[^"\']*)["\']'
    ]
    for p in patterns:
        for m in re.finditer(p, js_text, re.IGNORECASE):
            try:
                res.add(absolute_url(base, m.group(1)))
            except:
                pass
    for m in PDF_LIKE_REGEX.finditer(js_text):
        res.add(m.group(1))
    # base64 detection (simple)
    for m in re.finditer(r'atob\([\'"]([^\'"]+)[\'"]\)', js_text, re.IGNORECASE):
        try:
            dec = base64.b64decode(m.group(1)).decode(errors='ignore')
            for mm in PDF_LIKE_REGEX.finditer(dec):
                res.add(absolute_url(base, mm.group(1)))
        except Exception:
            pass
    return list(res)

async def try_call_js_endpoint(session: ClientSession, endpoint: str, base_url: str, referer: str = None):
    full = absolute_url(base_url, endpoint)
    headers = USER_AGENT_HEADER.copy()
    if referer:
        headers['Referer'] = referer
    print(f"[try_call_js_endpoint] trying {full}")
    try:
        async with session.get(full, headers=headers, allow_redirects=True, timeout=ClientTimeout(total=12)) as resp:
            text = await resp.text(errors='ignore')
            final = str(resp.url)
            for m in PDF_LIKE_REGEX.finditer(text):
                return absolute_url(final, m.group(1))
            try:
                j = await resp.json(content_type=None)
                # scan JSON for urls
                def scan(v):
                    if isinstance(v, str):
                        if v.lower().endswith('.pdf') or 'drive.google' in v or 'dropbox' in v:
                            return v
                        if 'http' in v and '.pdf' in v:
                            uu = re.search(r'https?://[^\s"\']+\.pdf', v)
                            if uu:
                                return uu.group(0)
                    if isinstance(v, dict):
                        for val in v.values():
                            r = scan(val)
                            if r:
                                return r
                    if isinstance(v, list):
                        for item in v:
                            r = scan(item)
                            if r:
                                return r
                    return None
                found = scan(j)
                if found:
                    return absolute_url(final, found)
            except Exception:
                pass
    except Exception as e:
        print(f"[try_call_js_endpoint] GET failed {full}: {e}")
    # try POST minimal
    try:
        async with session.post(full, headers=headers, data={}, allow_redirects=True, timeout=ClientTimeout(total=12)) as resp:
            t = await resp.text(errors='ignore')
            for m in PDF_LIKE_REGEX.finditer(t):
                return absolute_url(str(resp.url), m.group(1))
    except Exception as e:
        print(f"[try_call_js_endpoint] POST failed {full}: {e}")
    return None

# ---------------- HTML extractor ----------------
async def extract_pdf_candidate_from_html(html: str, base_url: str):
    soup = BeautifulSoup(html, "html.parser")
    candidates = []

    # links and download patterns
    for a in soup.find_all('a', href=True):
        href = a['href'].strip()
        full = absolute_url(base_url, href)
        low = full.lower()
        text = (a.get_text() or '').strip().lower()
        if low.endswith('.pdf') or 'drive.google.com' in low or 'dropbox.com' in low or 'archive.org/download' in low:
            candidates.append(full)
        if '/book/downloading/' in low or '/downloading/' in low or '/download/' in low or 'dl=' in low:
            candidates.append(full)
        if any(k in text for k in ('تحميل', 'download', 'download book', 'تحميل الكتاب')):
            candidates.append(full)

    # data-* attributes
    for tag in soup.find_all(True, attrs=True):
        for attr in ('data-href','data-url','data-download','data-link','data-file'):
            if tag.has_attr(attr):
                candidates.append(absolute_url(base_url, tag[attr]))

    # onclick handlers
    for tag in soup.find_all(True, onclick=True):
        for url in extract_urls_from_js(tag['onclick'], base_url):
            candidates.append(url)

    # inline scripts
    for script in soup.find_all('script'):
        s = script.string or ''
        for url in extract_urls_from_js(s, base_url):
            candidates.append(url)

    # forms (we'll try simple submission logic later)
    seen = set()
    unique = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            unique.append(c)
    return unique

# ---------------- No-browser extraction (with JS endpoint probing + polling) ----------------
async def get_pdf_link_no_browser(start_url: str, max_wait_seconds=35):
    async with ClientSession() as session:
        referer = start_url
        low = start_url.lower()
        if low.endswith('.pdf') or 'archive.org/download' in low or 'drive.google.com' in low or 'dropbox.com' in low:
            return start_url, start_url

        status, headers, text, final_url = await fetch_text(session, start_url, referer=start_url, timeout=15)
        if not text:
            return None, None

        candidates = await extract_pdf_candidate_from_html(text, final_url)
        print(f"[no_browser] initial candidates: {candidates}")

        # scan for fetch/XHR endpoints in scripts
        endpoints = set()
        for m in re.finditer(r'fetch\(\s*[\'"]([^\'"]+)[\'"]', text, re.IGNORECASE):
            endpoints.add(m.group(1))
        for m in re.finditer(r'xhr\.open\(\s*[\'"](?:GET|POST)[\'"]\s*,\s*[\'"]([^\'"]+)[\'"]', text, re.IGNORECASE):
            endpoints.add(m.group(1))

        # call endpoints
        for ep in endpoints:
            found = await try_call_js_endpoint(session, ep, final_url, referer=referer)
            if found:
                print(f"[no_browser] endpoint returned pdf: {found}")
                return found, final_url

        # try HEAD on initial candidates
        for c in candidates:
            st, ctype, clen = await head_check(session, c, referer=final_url)
            if ctype and ('pdf' in ctype or 'octet-stream' in ctype) and (clen is None or clen >= MIN_PDF_SIZE_BYTES):
                return c, final_url

        # try simple form submits (heuristic)
        soup = BeautifulSoup(text, "html.parser")
        for form in soup.find_all('form'):
            form_text = str(form).lower()
            if any(k in form_text for k in ('download','get','file','submit')):
                # prepare basic data
                data = {}
                for inp in form.find_all('input'):
                    if inp.get('name'):
                        data[inp.get('name')] = inp.get('value','')
                action = form.get('action') or final_url
                method = (form.get('method') or 'get').lower()
                target = absolute_url(final_url, action)
                try:
                    if method == 'post':
                        async with session.post(target, data=data, headers=USER_AGENT_HEADER, allow_redirects=True, timeout=ClientTimeout(total=15)) as resp:
                            tx = await resp.text(errors='ignore')
                            for m in PDF_LIKE_REGEX.finditer(tx):
                                return absolute_url(str(resp.url), m.group(1)), final_url
                    else:
                        async with session.get(target, params=data, headers=USER_AGENT_HEADER, allow_redirects=True, timeout=ClientTimeout(total=15)) as resp:
                            tx = await resp.text(errors='ignore')
                            for m in PDF_LIKE_REGEX.finditer(tx):
                                return absolute_url(str(resp.url), m.group(1)), final_url
                except Exception as e:
                    print(f"[no_browser] form submit failed: {e}")

        # polling loop to simulate waiting/countdown or later load
        wait_interval = 5
        attempts = max(1, max_wait_seconds // wait_interval)
        for i in range(attempts):
            await asyncio.sleep(wait_interval + random.uniform(0,2))
            print(f"[no_browser] polling attempt {i+1}/{attempts}")
            status, headers, text, final_url = await fetch_text(session, start_url, referer=referer, timeout=15)
            if not text:
                continue
            candidates = await extract_pdf_candidate_from_html(text, final_url)
            for c in candidates:
                st, ctype, clen = await head_check(session, c, referer=final_url)
                if ctype and ('pdf' in ctype or 'octet-stream' in ctype) and (clen is None or clen >= MIN_PDF_SIZE_BYTES):
                    return c, final_url
            # try endpoints again
            for m in re.finditer(r'fetch\(\s*[\'"]([^\'"]+)[\'"]', text, re.IGNORECASE):
                found = await try_call_js_endpoint(session, m.group(1), final_url, referer=referer)
                if found:
                    return found, final_url

        # redirect history fallback
        try:
            async with session.get(start_url, headers=USER_AGENT_HEADER, allow_redirects=True, timeout=ClientTimeout(total=15)) as resp:
                final = str(resp.url)
                if final and final.lower().endswith('.pdf'):
                    st, ctype, clen = await head_check(session, final, referer=start_url)
                    if ctype and ('pdf' in ctype or 'octet-stream' in ctype):
                        return final, start_url
        except Exception:
            pass

        return None, None

# ---------------- Playwright extractor (if available) ----------------
async def get_pdf_link_with_browser(link: str, timeout_ms: int = 45000):
    try:
        # best-effort install attempt (no-op if already installed)
        try:
            import subprocess
            subprocess.run(["playwright", "install", "chromium"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=['--no-sandbox','--disable-dev-shm-usage'])
            context = await browser.new_context(user_agent=USER_AGENT, viewport={'width':1280,'height':720})
            # anti detection scripts
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
                Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3] });
                window.navigator.chrome = { runtime: {} };
            """)
            page = await context.new_page()
            network_urls = set()

            def on_response(resp):
                try:
                    if resp.status in (200,206) or resp.status >=300:
                        network_urls.add(resp.url)
                except:
                    pass

            page.on("response", on_response)

            try:
                await page.goto(link, wait_until="networkidle", timeout=timeout_ms)
            except Exception as e:
                print(f"[playwright] goto failed: {e}")

            # attempt clicks for common selectors
            selectors = ['a[href*="pdf"]', 'a.btn-download', 'a[href*="download"]', 'button:has-text("تحميل")', 'button:has-text("download")']
            for sel in selectors:
                try:
                    loc = page.locator(sel)
                    if await loc.count() > 0:
                        await loc.first.click(timeout=5000, force=True)
                        await asyncio.sleep(random.uniform(1.5,3.5))
                except Exception:
                    pass

            # wait for network capture
            await asyncio.sleep(3)
            # inspect network URLs
            for u in network_urls:
                if u.lower().endswith('.pdf') or 'drive.google.com' in u or 'dropbox.com' in u:
                    await browser.close()
                    return u, link

            # fallback: scrape HTML and frames
            content = await page.content()
            soup = BeautifulSoup(content, "html.parser")
            for a in soup.find_all('a', href=True):
                href = absolute_url(link, a['href'])
                if href.lower().endswith('.pdf') or '/book/downloading/' in href.lower():
                    await browser.close()
                    return href, link

            await browser.close()
            return None, None
    except Exception as e:
        print(f"[playwright] critical error: {e}")
        return None, None

# ---------------- Unified getter ----------------
async def get_pdf_link(link: str, prefer_browser: bool = False):
    # prefer_browser tries Playwright first if available
    if prefer_browser and _playwright_available:
        pdf, ref = await get_pdf_link_with_browser(link)
        if pdf:
            return pdf, ref
    # try no-browser smart method
    pdf, ref = await get_pdf_link_no_browser(link, max_wait_seconds=35)
    if pdf:
        return pdf, ref
    # last resort: try browser if available
    if _playwright_available and not prefer_browser:
        pdf, ref = await get_pdf_link_with_browser(link)
        return pdf, ref
    return None, None

# ---------------- download/send ----------------
async def download_and_send_pdf(context, chat_id, source, title="book.pdf", referer_link=None):
    pdf_url = source
    download_headers = USER_AGENT_HEADER.copy()
    if referer_link:
        download_headers['Referer'] = referer_link

    async with ClientSession() as session:
        try:
            st, ctype, clen = await head_check(session, pdf_url, referer=referer_link)
            if not ctype or ('pdf' not in ctype and 'octet-stream' not in ctype):
                await context.bot.send_message(chat_id=chat_id, text=f"⚠️ الفشل: الرابط لا يبدو PDF ({ctype}).")
                return
            if clen is not None and clen < MIN_PDF_SIZE_BYTES:
                await context.bot.send_message(chat_id=chat_id, text="⚠️ الفشل: الملف صغير جدًا.")
                return
        except Exception as e:
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ خطأ أثناء التحقق: {e}")
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
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ خطأ أثناء التنزيل: {e}")
            return

    try:
        with open(file_path, "rb") as f:
            await context.bot.send_document(chat_id=chat_id, document=f)
        await context.bot.send_message(chat_id=chat_id, text="✅ تم إرسال الكتاب.")
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"⚠️ خطأ أثناء الإرسال: {e}")
    finally:
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except:
            pass

# ---------------- Telegram handlers ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📚 بوت البحث في مكتبات الكتب — أرسل /search <اسم الكتاب> أو استخدم النتائج.")

async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("استخدم: /search اسم الكتاب أو المؤلف")
        return
    msg = await update.message.reply_text(f"🔍 أبحث عن **{query}**...")
    try:
        results = []
        with DDGS(timeout=6) as ddgs:
            q = " OR ".join([f"site:{d}" for d in TRUSTED_DOMAINS])
            full_q = f"{query} filetype:pdf OR {q}"
            for r in ddgs.text(full_q, max_results=15):
                link = r.get('href')
                title = r.get('title') or link
                if link and title:
                    results.append({"title": title.strip(), "link": link})
        # dedupe
        uniq = {}
        for it in results:
            uniq[it['link']] = it
        results = list(uniq.values())[:8]
        if not results:
            await msg.edit_text("❌ لم أجد نتائج موثوقة في مصادر الكتب الموثوقة.")
            return
        context.user_data[TEMP_LINKS_KEY] = [it['link'] for it in results]
        buttons = []
        lines = []
        for i, it in enumerate(results):
            lines.append(f"{i+1}. {it['title'][:120]}")
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
        idx = int(data.split("|",1)[1])
        link = context.user_data[TEMP_LINKS_KEY][idx]
    except Exception:
        await query.message.reply_text("⚠️ خطأ: الرابط غير صالح.")
        return

    await query.edit_message_text("⏳ جارى استخلاص رابط PDF — سأحاول قطعياً (مع استراتيجيات متعددة)...")
    prefer_browser = USE_BROWSER and _playwright_available
    if USE_BROWSER and not _playwright_available:
        print("[WARN] USE_BROWSER=true لكن Playwright غير مثبت:", _playwright_err)

    pdf_link, referer = await get_pdf_link(link, prefer_browser=prefer_browser)
    if pdf_link:
        await download_and_send_pdf(context, query.message.chat_id, pdf_link, title="book", referer_link=referer)
    else:
        text = f"📄 لم أتمكن من استخلاص رابط PDF من المصدر: {link}\n"
        if USE_BROWSER and not _playwright_available:
            text += "ملاحظة: طلبت وضع المتصفح لكن Playwright غير مُثبت في البيئة."
        else:
            text += "ملاحظة: إن كانت الصفحة تعتمد على جافاسكربت معقد أو CAPTCHA فالطريقة المثلى هي تشغيل المتصفح (Playwright)."
        await context.bot.send_message(chat_id=query.message.chat_id, text=text)

# ---------------- Main ----------------
def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN مفقود في المتغيرات البيئية.")
    print("Bot starting. USE_BROWSER=", USE_BROWSER, "playwright_available=", _playwright_available)
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.run_polling()

if __name__ == "__main__":
    main()
