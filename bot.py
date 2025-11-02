# main.py
import os
import re
import asyncio
import tempfile
import aiofiles
import random
from urllib.parse import urljoin, urlparse
from aiohttp import ClientSession, ClientTimeout
from bs4 import BeautifulSoup
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# --- اختيارات التشغيل ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
USE_PLAYWRIGHT = os.getenv("USE_BROWSER", "true").lower() in ("1", "true", "yes")
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
HEADERS = {'User-Agent': USER_AGENT}
TEMP_LINKS_KEY = "current_search_links"
MIN_PDF_SIZE_BYTES = int(os.getenv("MIN_PDF_SIZE_BYTES", 50 * 1024))

# --- قائمة مكتبات عربية مركّزة (قابلة للتعديل) ---
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

PDF_RE = re.compile(r'https?:\/\/[^\s\'"]+\.pdf', re.IGNORECASE)

# --- محاولة استيراد Playwright (best-effort) ---
_playwright_available = False
_playwright_err = None
if USE_PLAYWRIGHT:
    try:
        from playwright.async_api import async_playwright
        _playwright_available = True
    except Exception as e:
        _playwright_err = e
        _playwright_available = False
        print("Playwright unavailable:", e)

# ------------------- مساعدات الشبكة والملف -------------------
async def head_check(session: ClientSession, url: str, referer: str = None, timeout=10):
    headers = HEADERS.copy()
    if referer:
        headers['Referer'] = referer
    try:
        async with session.head(url, headers=headers, allow_redirects=True, timeout=ClientTimeout(total=timeout)) as r:
            ctype = r.headers.get('Content-Type', '').lower()
            clen = int(r.headers.get('Content-Length', 0) or 0)
            return r.status, ctype, clen
    except Exception:
        # fallback to GET small
        try:
            async with session.get(url, headers=headers, allow_redirects=True, timeout=ClientTimeout(total=timeout)) as r:
                ctype = r.headers.get('Content-Type', '').lower()
                clen = int(r.headers.get('Content-Length', 0) or 0)
                return r.status, ctype, clen
        except Exception:
            return None, None, None

async def download_to_file(url: str, dest_name: str, referer: str = None):
    tmp_dir = tempfile.gettempdir()
    safe = re.sub(r'[\\/*?:"<>|]', "_", dest_name)[:60]
    path = os.path.join(tmp_dir, f"{safe}.pdf")
    headers = HEADERS.copy()
    if referer:
        headers['Referer'] = referer
    async with ClientSession() as session:
        try:
            async with session.get(url, headers=headers, allow_redirects=True, timeout=ClientTimeout(total=120)) as r:
                if r.status != 200:
                    return None, f"HTTP {r.status}"
                # stream in chunks
                f = await aiofiles.open(path, "wb")
                async for chunk in r.content.iter_chunked(64 * 1024):
                    await f.write(chunk)
                await f.close()
                return path, None
        except Exception as e:
            return None, str(e)

# ------------------- مستخرج بدون متصفح (fallback) -------------------
async def extract_candidates_from_html(html: str, base: str):
    soup = BeautifulSoup(html, "html.parser")
    cands = []
    # direct a href
    for a in soup.find_all('a', href=True):
        href = urljoin(base, a['href'])
        text = (a.get_text() or "").strip()
        if href.lower().endswith('.pdf') or 'download' in href.lower() or PDF_RE.search(href):
            cands.append((href, text))
    # data-* attributes or onclick
    for tag in soup.find_all(True, attrs=True):
        for attr in ('data-href','data-url','data-download','data-link'):
            if tag.has_attr(attr):
                cands.append((urljoin(base, tag[attr]), tag.get_text() or ""))
        if tag.has_attr('onclick'):
            onclick = tag['onclick']
            for m in PDF_RE.finditer(onclick):
                cands.append((m.group(0), ""))
            # capture assignment redirects
            m2 = re.search(r'location\.href\s*=\s*[\'"]([^\'"]+)[\'"]', onclick)
            if m2:
                cands.append((urljoin(base, m2.group(1)), ""))
    # scripts
    for s in soup.find_all('script'):
        txt = s.string or ""
        for m in PDF_RE.finditer(txt):
            cands.append((m.group(0), ""))
        # fetch endpoints
        for m in re.finditer(r'fetch\(\s*[\'"]([^\'"]+)[\'"]', txt, re.IGNORECASE):
            cands.append((urljoin(base, m.group(1)), "endpoint"))
    # unique preserve order
    seen = set()
    unique = []
    for u,t in cands:
        if u and u not in seen:
            seen.add(u)
            unique.append((u,t))
    return unique

async def get_pdf_from_page_no_browser(page_url: str, max_wait=25):
    async with ClientSession() as session:
        try:
            async with session.get(page_url, headers=HEADERS, allow_redirects=True, timeout=ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return None, None
                html = await resp.text(errors='ignore')
                final = str(resp.url)
        except Exception:
            return None, None

        cands = await extract_candidates_from_html(html, final)
        # try endpoints / candidates
        for u, _ in cands:
            st, ctype, clen = await head_check(session, u, referer=final)
            if ctype and ('pdf' in ctype or 'octet-stream' in ctype) and (clen is None or clen >= MIN_PDF_SIZE_BYTES):
                return u, final

        # polling (simulate waiting)
        interval = 5
        attempts = max(1, max_wait // interval)
        for i in range(attempts):
            await asyncio.sleep(interval + random.uniform(0,2))
            try:
                async with session.get(page_url, headers=HEADERS, allow_redirects=True, timeout=ClientTimeout(total=15)) as resp:
                    html = await resp.text(errors='ignore')
                    final = str(resp.url)
            except Exception:
                continue
            cands = await extract_candidates_from_html(html, final)
            for u,_ in cands:
                st, ctype, clen = await head_check(session, u, referer=final)
                if ctype and ('pdf' in ctype or 'octet-stream' in ctype) and (clen is None or clen >= MIN_PDF_SIZE_BYTES):
                    return u, final
        return None, None

# ------------------- مستخرج Playwright (الموصى به الآن) -------------------
async def get_pdf_with_playwright(url: str, timeout_ms: int = 40000):
    if not _playwright_available:
        return None, None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
            context = await browser.new_context(user_agent=USER_AGENT, viewport={'width':1280,'height':720})
            # anti-detection init script
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});
                window.navigator.chrome = { runtime: {} };
            """)
            page = await context.new_page()
            network_urls = set()

            def on_resp(r):
                try:
                    if r.url:
                        network_urls.add(r.url)
                except:
                    pass

            page.on("response", on_resp)
            try:
                await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            except Exception:
                # still continue to try parsing
                pass

            # try clicking common download selectors (best-effort)
            selectors = [
                'a[href*=".pdf"]', 'a[href*="download"]', 'a[class*="download"]',
                'button:has-text("تحميل")', 'button:has-text("download")',
                'a:has-text("تحميل")', 'a:has-text("تحميل الكتاب")'
            ]
            for sel in selectors:
                try:
                    loc = page.locator(sel)
                    if await loc.count() > 0:
                        await loc.first.click(timeout=4000, force=True)
                        await asyncio.sleep(random.uniform(1.5,3.0))
                except Exception:
                    pass

            # wait a bit for network requests to happen
            await asyncio.sleep(2.5)
            # check network captured urls
            for u in list(network_urls):
                lu = u.lower()
                if lu.endswith('.pdf') or 'drive.google' in lu or 'dropbox.com' in lu or '/download/' in lu:
                    await browser.close()
                    return u, url

            # fallback: search html & frames
            content = await page.content()
            soup = BeautifulSoup(content, "html.parser")
            # search anchors
            for a in soup.find_all('a', href=True):
                href = urljoin(url, a['href'])
                if href.lower().endswith('.pdf') or '/book/downloading/' in href.lower() or '/download/' in href.lower():
                    await browser.close()
                    return href, url
            # frames
            try:
                for frame in page.frames:
                    try:
                        htmlf = await frame.content()
                        sf = BeautifulSoup(htmlf, "html.parser")
                        for a in sf.find_all('a', href=True):
                            href = urljoin(url, a['href'])
                            if href.lower().endswith('.pdf'):
                                await browser.close()
                                return href, url
                    except Exception:
                        pass
            except Exception:
                pass

            await browser.close()
            return None, None
    except Exception as e:
        print("Playwright extraction error:", e)
        return None, None

# ------------------- بحث عبر المكتبات (بدون محرك بحث خارجي) -------------------
async def search_all_libraries(query: str, per_site_limit=8):
    results = []
    async with ClientSession(headers=HEADERS) as session:
        tasks = []
        for site in LIBRARY_SITES:
            # try common search paths heuristically
            # many sites use /search?q= or /?s= or /search/
            q1 = f"{site}/?s={query.replace(' ', '+')}"
            q2 = f"{site}/search?q={query.replace(' ', '+')}"
            q3 = f"{site}/search/{query.replace(' ', '+')}"
            tasks.append((site, q1))
            tasks.append((site, q2))
            tasks.append((site, q3))
        # fetch sequentially to be gentle (can parallelize if needed)
        for site, search_url in tasks:
            try:
                async with session.get(search_url, timeout=ClientTimeout(total=12)) as resp:
                    if resp.status != 200:
                        continue
                    html = await resp.text(errors='ignore')
                    base = str(resp.url)
                    # extract candidate links
                    cands = await extract_candidates_from_html(html, base)
                    for u, txt in cands[:per_site_limit]:
                        results.append({"title": txt or u.split('/')[-1], "link": u, "source": site})
            except Exception:
                continue
    # dedupe keep order
    seen = set(); uniq = []
    for it in results:
        if it['link'] not in seen:
            seen.add(it['link'])
            uniq.append(it)
    return uniq[:12]

# ------------------- Telegram integration -------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📚 بوت الكتب — الآن يدعم المتصفح الحقيقي. استخدم /search <اسم الكتاب>")

async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("استخدم: /search اسم الكتاب أو المؤلف")
        return
    m = await update.message.reply_text(f"🔎 أبحث عن '{query}' في مكتبات عربية...")
    try:
        candidates = await search_all_libraries(query)
        if not candidates:
            await m.edit_text("❌ لم أجد نتائج — سأجرب استخراج مباشر بالمتصفح (إن توفر).")
            # try a couple site homepages with playwright fallback? just return
            return
        # keep top 8
        top = candidates[:8]
        context.user_data[TEMP_LINKS_KEY] = [c['link'] for c in top]
        lines = []
        buttons = []
        for i, c in enumerate(top):
            title = c['title'][:80]
            src = urlparse(c['source']).netloc
            lines.append(f"{i+1}. {title} ({src})")
            buttons.append([InlineKeyboardButton(f"📥 تحميل {i+1}", callback_data=f"dl|{i}")])
        await m.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        await m.edit_text(f"⚠️ خطأ أثناء البحث: {e}")

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if not data.startswith("dl|"):
        return
    idx = int(data.split("|",1)[1])
    link = context.user_data.get(TEMP_LINKS_KEY, [])[idx]
    if not link:
        await query.edit_message_text("⚠️ رابط غير صالح.")
        return
    await query.edit_message_text("⏳ أحاول استخلاص رابط PDF (أولاً باستخدام المتصفح إن أمكن)...")
    # 1) try playwright if available
    pdf_url = None
    referer = link
    if _playwright_available:
        pdf_url, ref = await get_pdf_with_playwright(link)
        if pdf_url:
            referer = ref or link
    # 2) fallback no-browser
    if not pdf_url:
        pdf_url, ref = await get_pdf_from_page_no_browser(link)
        if pdf_url:
            referer = ref or link

    if not pdf_url:
        await context.bot.send_message(chat_id=query.message.chat_id, text=f"❌ فشل الاستخلاص من: {link}\nملاحظة: بعض الصفحات تتطلب تفاعل بشري / CAPTCHA أو منطق جافاسكربت معقّد.")
        return

    # verify PDF via HEAD
    async with ClientSession() as session:
        st, ctype, clen = await head_check(session, pdf_url, referer=referer)
    if ctype and ('pdf' in ctype or 'octet-stream' in ctype) and (clen is None or clen >= MIN_PDF_SIZE_BYTES):
        # download and send
        fname = "book_extracted"
        path, err = await download_to_file(pdf_url, fname, referer=referer)
        if path:
            await query.edit_message_text("⏳ جاري إرسال الكتاب ...")
            await asyncio.sleep(0.5)
            try:
                with open(path, "rb") as f:
                    await context.bot.send_document(chat_id=query.message.chat_id, document=f)
                await context.bot.send_message(chat_id=query.message.chat_id, text="✅ تم الإرسال.")
            except Exception as e:
                await context.bot.send_message(chat_id=query.message.chat_id, text=f"⚠️ خطأ أثناء الإرسال: {e}")
            finally:
                try:
                    if os.path.exists(path):
                        os.remove(path)
                except:
                    pass
        else:
            await context.bot.send_message(chat_id=query.message.chat_id, text=f"⚠️ فشل تنزيل الملف: {err}")
    else:
        await context.bot.send_message(chat_id=query.message.chat_id, text=f"⚠️ الاستجابة ليست PDF أو صغيرة جداً ({ctype}, {clen}). الرابط: {pdf_url}")

# ------------------- تشغيل البوت -------------------
def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN missing")
    print("Starting bot. Playwright available:", _playwright_available)
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.run_polling()

if __name__ == "__main__":
    main()
