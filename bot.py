# main.py (محسّن: محاكاة بعض سلوكيات JavaScript بدون متصفح)
import os
import re
import asyncio
import tempfile
import aiofiles
import json
import base64
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
MIN_PDF_SIZE_BYTES = int(os.getenv("MIN_PDF_SIZE_BYTES", 50 * 1024))
TEMP_LINKS_KEY = "current_search_links"

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
            final = str(resp.url)
            print(f"[fetch_text] {url} -> status {resp.status}, final {final}")
            return resp.status, resp.headers, text, final
    except Exception as e:
        print(f"[fetch_text] ERROR fetching {url}: {e}")
        return None, None, None, None

async def head_check(session: ClientSession, url: str, referer: str = None, timeout=10):
    headers = USER_AGENT_HEADER.copy()
    if referer:
        headers['Referer'] = referer
    try:
        async with session.head(url, headers=headers, allow_redirects=True, timeout=ClientTimeout(total=timeout)) as head_resp:
            content_type = head_resp.headers.get('Content-Type', '').lower()
            content_length = int(head_resp.headers.get('Content-Length', 0) or 0)
            print(f"[head_check] HEAD {url} -> {head_resp.status}, {content_type}, {content_length}")
            return head_resp.status, content_type, content_length
    except Exception as e:
        # fallback to GET small-range
        print(f"[head_check] HEAD failed for {url}: {e} — trying GET fallback")
        try:
            async with session.get(url, headers=headers, allow_redirects=True, timeout=ClientTimeout(total=timeout)) as resp:
                content_type = resp.headers.get('Content-Type', '').lower()
                content_length = int(resp.headers.get('Content-Length', 0) or 0)
                print(f"[head_check] GET fallback {url} -> {resp.status}, {content_type}, {content_length}")
                return resp.status, content_type, content_length
        except Exception as e2:
            print(f"[head_check] GET fallback failed for {url}: {e2}")
            return None, None, None

# ---------------- JS parsing helpers ----------------
# extract window.location, setTimeout redirects, window.open, raw pdf urls in scripts
def extract_urls_from_js(js_text: str, base: str):
    results = set()
    if not js_text:
        return []
    patterns = [
        r'window\.location(?:\.href)?\s*=\s*[\'"]([^\'"]+)[\'"]',
        r'location\.href\s*=\s*[\'"]([^\'"]+)[\'"]',
        r'window\.open\s*\(\s*[\'"]([^\'"]+)[\'"]',
        r'setTimeout\s*\(\s*function\s*\(\)\s*\{\s*window\.location\s*=\s*[\'"]([^\'"]+)[\'"]',
        r'setTimeout\s*\(\s*["\']location\.href\s*=\s*[\'"]([^\'"]+)[\'"]',
        r'["\'](https?:\/\/[^"\']+\.pdf[^"\']*)["\']'
    ]
    for p in patterns:
        for m in re.finditer(p, js_text, re.IGNORECASE):
            try:
                results.add(absolute_url(base, m.group(1)))
            except:
                pass
    # find fetch/XHR endpoints that return json with url-like fields
    # pattern: fetch('/api/getfile', { ... })
    for m in re.finditer(r'fetch\(\s*[\'"]([^\'"]+)[\'"]', js_text, re.IGNORECASE):
        ep = m.group(1)
        results.add(absolute_url(base, ep))
    # raw pdf urls inside script
    for m in PDF_LIKE_REGEX.finditer(js_text):
        results.add(m.group(1))
    # find base64-encoded strings that might contain urls
    for m in re.finditer(r'atob\([\'"]([^\'"]+)[\'"]\)', js_text, re.IGNORECASE):
        try:
            decoded = base64.b64decode(m.group(1)).decode(errors='ignore')
            for mm in PDF_LIKE_REGEX.finditer(decoded):
                results.add(absolute_url(base, mm.group(1)))
        except Exception:
            pass
    return list(results)

# Try to call endpoints that scripts reference (GET or POST attempt)
async def try_call_js_endpoint(session: ClientSession, endpoint: str, base_url: str, referer: str = None):
    full = absolute_url(base_url, endpoint)
    headers = USER_AGENT_HEADER.copy()
    if referer:
        headers['Referer'] = referer
    print(f"[try_call_js_endpoint] Trying endpoint {full}")
    # try GET
    try:
        async with session.get(full, headers=headers, allow_redirects=True, timeout=ClientTimeout(total=12)) as resp:
            text = await resp.text(errors='ignore')
            final = str(resp.url)
            print(f"[try_call_js_endpoint] GET {full} -> {resp.status}, {final}")
            # scan for pdf link
            for m in PDF_LIKE_REGEX.finditer(text):
                return absolute_url(final, m.group(1))
            # if JSON
            try:
                j = await resp.json(content_type=None)
                # search for any value that looks like url/pdf
                def scan_json(v):
                    if isinstance(v, str):
                        if v.lower().endswith('.pdf') or 'drive.google.com' in v or 'dropbox.com' in v:
                            return absolute_url(final, v)
                        # sometimes url encoded
                        if 'http' in v and '.pdf' in v:
                            uu = re.search(r'https?://[^\s"\']+\.pdf', v)
                            if uu:
                                return uu.group(0)
                    if isinstance(v, dict):
                        for val in v.values():
                            res = scan_json(val)
                            if res:
                                return res
                    if isinstance(v, list):
                        for item in v:
                            res = scan_json(item)
                            if res:
                                return res
                    return None
                found = scan_json(j)
                if found:
                    return found
            except Exception:
                pass
    except Exception as e:
        print(f"[try_call_js_endpoint] GET failed {full}: {e}")

    # try POST minimal
    try:
        async with session.post(full, headers=headers, data={}, allow_redirects=True, timeout=ClientTimeout(total=12)) as resp:
            text = await resp.text(errors='ignore')
            final = str(resp.url)
            for m in PDF_LIKE_REGEX.finditer(text):
                return absolute_url(final, m.group(1))
    except Exception as e:
        print(f"[try_call_js_endpoint] POST failed {full}: {e}")

    return None

# ---------------- HTML extractor (improved) ----------------
async def extract_pdf_candidate_from_html(html: str, base_url: str):
    candidates = []
    soup = BeautifulSoup(html, "html.parser")

    # 1) <a href> direct patterns
    for a in soup.find_all('a', href=True):
        href = a['href'].strip()
        full = absolute_url(base_url, href)
        low = full.lower()
        if low.endswith('.pdf') or 'drive.google.com' in low or 'dropbox.com' in low or 'archive.org/download' in low:
            candidates.append(full)
        if 'download' in low and ('/file' in low or low.endswith('/download') or 'dl=' in low or '/downloading/' in low):
            candidates.append(full)
        # look for links whose visible text indicates download
        text = (a.get_text() or '').strip().lower()
        if any(k in text for k in ("تحميل", "download", "download book", "تحميل الكتاب")):
            candidates.append(full)

    # 2) data-* attributes
    for tag in soup.find_all(True, attrs=True):
        for attr in ('data-href', 'data-url', 'data-download', 'data-link', 'data-file'):
            if tag.has_attr(attr):
                candidates.append(absolute_url(base_url, tag[attr]))

    # 3) onclick handlers
    for tag in soup.find_all(True, onclick=True):
        onclick = tag['onclick']
        for url in extract_urls_from_js(onclick, base_url):
            candidates.append(url)

    # 4) meta refresh
    m = soup.find('meta', attrs={'http-equiv': re.compile(r'refresh', re.I)})
    if m and m.get('content'):
        content = m['content']
        parts = content.split(';')
        if len(parts) >= 2 and 'url=' in parts[1].lower():
            url_part = parts[1].split('=', 1)[1].strip(' "\'')
            candidates.append(absolute_url(base_url, url_part))

    # 5) inline scripts
    for script in soup.find_all('script'):
        script_text = script.string or ''
        for url in extract_urls_from_js(script_text, base_url):
            candidates.append(url)
        # try to find JSON objects inside scripts (var data = {...})
        for jm in re.finditer(r'var\s+([a-zA-Z0-9_]+)\s*=\s*(\{[\s\S]{10,2000}?\});', script_text):
            try:
                jsobj = jm.group(2)
                # try to convert to valid JSON (simple replacements)
                json_text = re.sub(r'(\w+):', r'"\1":', jsobj)  # crude
                parsed = json.loads(json_text)
                # scan parsed for pdf links
                def scan(v):
                    if isinstance(v, str):
                        if v.lower().endswith('.pdf') or 'drive.google' in v or 'dropbox' in v:
                            return v
                    if isinstance(v, dict):
                        for val in v.values():
                            res = scan(val)
                            if res:
                                return res
                    if isinstance(v, list):
                        for item in v:
                            res = scan(item)
                            if res:
                                return res
                    return None
                found = scan(parsed)
                if found:
                    candidates.append(absolute_url(base_url, found))
            except Exception:
                pass

    # 6) forms (we will try to submit later)
    # return unique preserving order
    seen = set()
    unique = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            unique.append(c)
    return unique

# ---------------- High-level: simulate wait/follow endpoints ----------------
async def get_pdf_link_no_browser(start_url: str, max_wait_seconds=30):
    async with ClientSession() as session:
        referer = start_url
        low = start_url.lower()
        if low.endswith('.pdf') or 'drive.google.com' in low or 'dropbox.com' in low or 'archive.org' in low:
            return start_url, start_url

        status, headers, text, final_url = await fetch_text(session, start_url, referer=start_url, timeout=15)
        if not text:
            return None, None

        # 1. extract candidates from HTML and inline JS
        candidates = await extract_pdf_candidate_from_html(text, final_url)
        print(f"[get_pdf_link_no_browser] initial candidates: {candidates}")

        # 2. attempt to call JS endpoints found in inline scripts (fetch/XHR)
        # extract endpoints from scripts quickly
        script_endpoints = set()
        for m in re.finditer(r'fetch\(\s*[\'"]([^\'"]+)[\'"]', text, re.IGNORECASE):
            script_endpoints.add(m.group(1))
        for m in re.finditer(r'xhr\.open\(\s*[\'"](?:GET|POST)[\'"]\s*,\s*[\'"]([^\'"]+)[\'"]', text, re.IGNORECASE):
            script_endpoints.add(m.group(1))
        for ep in script_endpoints:
            found = await try_call_js_endpoint(session, ep, final_url, referer=referer)
            if found:
                print(f"[get_pdf_link_no_browser] found via endpoint {ep}: {found}")
                return found, final_url

        # 3. try direct candidate checks via HEAD
        for candidate in candidates:
            st, ctype, clen = await head_check(session, candidate, referer=final_url)
            if ctype and ('pdf' in ctype or 'octet-stream' in ctype) and (clen is None or clen >= MIN_PDF_SIZE_BYTES):
                print(f"[get_pdf_link_no_browser] candidate passed HEAD: {candidate}")
                return candidate, final_url

        # 4. try forms (submit simple) and re-scan results
        soup = BeautifulSoup(text, "html.parser")
        for form in soup.find_all('form'):
            method = (form.get('method') or 'get').lower()
            action = form.get('action') or final_url
            # heuristic: only try forms that mention download/file
            form_text = str(form).lower()
            if any(k in form_text for k in ("download", "getfile", "file")):
                st, hd, tx, fu = await try_submit_form(session, form, final_url, referer=referer)
                if tx:
                    extra = await extract_pdf_candidate_from_html(tx, fu or final_url)
                    for e in extra:
                        st2, ctype2, clen2 = await head_check(session, e, referer=fu or final_url)
                        if ctype2 and ('pdf' in ctype2 or 'octet-stream' in ctype2):
                            return e, fu or final_url

        # 5. polling to simulate waiting (e.g., setTimeout redirect or countdown pages)
        wait_interval = 4
        attempts = max(1, max_wait_seconds // wait_interval)
        for i in range(attempts):
            await asyncio.sleep(wait_interval)
            print(f"[get_pdf_link_no_browser] polling attempt {i+1}/{attempts}")
            status, headers, text, final_url = await fetch_text(session, start_url, referer=referer, timeout=15)
            if not text:
                continue
            # re-extract candidates
            candidates = await extract_pdf_candidate_from_html(text, final_url)
            for candidate in candidates:
                st, ctype, clen = await head_check(session, candidate, referer=final_url)
                if ctype and ('pdf' in ctype or 'octet-stream' in ctype) and (clen is None or clen >= MIN_PDF_SIZE_BYTES):
                    print(f"[get_pdf_link_no_browser] found during polling: {candidate}")
                    return candidate, final_url
            # also try js endpoints again
            for m in re.finditer(r'fetch\(\s*[\'"]([^\'"]+)[\'"]', text, re.IGNORECASE):
                ep = m.group(1)
                found = await try_call_js_endpoint(session, ep, final_url, referer=referer)
                if found:
                    return found, final_url

        # 6. last resort: follow redirects history
        try:
            async with session.get(start_url, headers=USER_AGENT_HEADER, allow_redirects=True, timeout=ClientTimeout(total=15)) as resp:
                final = str(resp.url)
                if final and final.lower().endswith('.pdf'):
                    st, ctype, clen = await head_check(session, final, referer=start_url)
                    if ctype and ('pdf' in ctype or 'octet-stream' in ctype):
                        print(f"[get_pdf_link_no_browser] final redirect is pdf: {final}")
                        return final, start_url
        except Exception:
            pass

        print("[get_pdf_link_no_browser] no pdf found")
        return None, None

# helper: submit forms (used above)
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
    print(f"[try_submit_form] submitting form to {target} method={method} data_keys={list(data.keys())}")
    try:
        if method == 'post':
            async with session.post(target, headers=headers, data=data, allow_redirects=True, timeout=ClientTimeout(total=15)) as resp:
                text = await resp.text(errors='ignore')
                return resp.status, resp.headers, text, str(resp.url)
        else:
            async with session.get(target, headers=headers, params=data, allow_redirects=True, timeout=ClientTimeout(total=15)) as resp:
                text = await resp.text(errors='ignore')
                return resp.status, resp.headers, text, str(resp.url)
    except Exception as e:
        print(f"[try_submit_form] failed: {e}")
        return None, None, None, None

# ---------------- Download/send ----------------
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
        await context.bot.send_message(chat_id=chat_id, text=f"⚠️ خطأ أثناء الإرسال: {e}")
    finally:
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except:
            pass

# ---------------- Telegram handlers ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📚 بوت التحميل - محسن لاستخلاص روابط (بدون متصفح). استخدم /search أو أرسل رابط.")

async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("استخدم: /search اسم الكتاب أو رابط.")
        return
    msg = await update.message.reply_text(f"🔍 أبحث عن **{query}** ...")
    try:
        results = []
        with DDGS(timeout=5) as ddgs:
            full_q = f"{query} filetype:pdf"
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

    await query.edit_message_text("⏳ محاولة استخلاص رابط التحميل (محاولة ذكية بدون متصفح)... يرجى الانتظار.")
    pdf_link, referer = await get_pdf_link_no_browser(link, max_wait_seconds=30)
    if pdf_link:
        await download_and_send_pdf(context, query.message.chat_id, pdf_link, title="book", referer_link=referer)
    else:
        await context.bot.send_message(chat_id=query.message.chat_id, text=f"📄 لم أتمكن من استخلاص رابط PDF من المصدر: {link}\n(إذا كان الموقع يعتمد على جافاسكربت معقد أو CAPTCHA فالمتصفح الحقيقي ضروري.)")

# ---------------- Main ----------------
def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN مفقود في المتغيرات البيئية.")
    print("Starting enhanced no-browser bot.")
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.run_polling()

if __name__ == "__main__":
    main()
