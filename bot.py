import os
import asyncio
import tempfile
import aiofiles
import random
import json
import subprocess
from aiohttp import ClientSession
from bs4 import BeautifulSoup
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from playwright.async_api import async_playwright, Page
from urllib.parse import urljoin
from ddgs import DDGS

# --- تثبيت Chromium تلقائيًا (في حال لم يكن مثبتًا) ---
try:
    subprocess.run(["playwright", "install", "chromium"], check=True)
except Exception as e:
    print(f"⚠️ لم يتم تثبيت Chromium تلقائيًا: {e}")

# --- إعدادات البوت والثوابت ---
BOT_TOKEN = os.getenv("BOT_TOKEN")

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
USER_AGENT_HEADER = {'User-Agent': USER_AGENT}

MIN_PDF_SIZE_BYTES = 50 * 1024
TEMP_LINKS_KEY = "current_search_links"
COOKIES_FILE = "browser_cookies.json"

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

# --- البحث في DuckDuckGo ---
async def search_duckduckgo(query: str):
    sites_query = " OR ".join([f"site:{d}" for d in TRUSTED_DOMAINS])
    full_query = f"{query} filetype:pdf OR {sites_query}"
    results = []

    try:
        with DDGS(timeout=5) as ddgs:
            search_results = ddgs.text(full_query, max_results=10)
            for r in search_results:
                link = r.get("href")
                title = r.get("title")
                if title and link and (any(d in link for d in TRUSTED_DOMAINS) or link.lower().endswith(".pdf")):
                    if not ("/section/" in link or "/category/" in link):
                        results.append({"title": title.strip(), "link": link})
    except Exception as e:
        print(f"DDGS search failed: {e}")
        return []

    unique_links = {item['link']: item for item in results}
    return list(unique_links.values())[:5]

# --- fallback network mining ---
async def fallback_strategy_4_network_mine(page: Page, download_selector_css: str, link: str):
    network_urls = set()
    def capture_url(response):
        if response.status in [200, 206, 301, 302]:
            network_urls.add(response.url)

    page.on("response", capture_url)

    try:
        try:
            await page.locator(download_selector_css).scroll_into_view_if_needed(timeout=5000)
        except:
            pass

        await page.locator(download_selector_css).click(timeout=10000, force=True)
        await asyncio.sleep(7)

        for i in range(15):
            await asyncio.sleep(1)
            for url in network_urls:
                url_lower = url.lower()
                if url_lower.endswith('.pdf') or 'drive.google.com' in url_lower or 'dropbox.com' in url_lower or 'archive.org/download' in url_lower:
                    print(f"✅ PDF link found via network mining: {url}")
                    return url

        # فحص iframes الإضافية
        for frame in page.frames:
            for a in await frame.query_selector_all("a[href]"):
                href = await a.get_attribute("href")
                if href and (href.lower().endswith(".pdf") or "download" in href.lower()):
                    full = urljoin(link, href)
                    print(f"✅ PDF link found in iframe: {full}")
                    return full

        return None
    except Exception as e:
        print(f"❌ network mining failed: {e}")
        return None
    finally:
        try:
            page.remove_listener("response", capture_url)
        except:
            pass

# --- استخلاص الرابط من الصفحة ---
async def get_pdf_link_from_page(link: str):
    pdf_link = None
    page_title = "book"
    browser = None
    is_local_path = False
    navigation_successful = False

    if link.lower().endswith('.pdf') or 'archive.org/download' in link.lower() or 'drive.google.com' in link.lower():
        return link, "Direct PDF", False, link

    try:
        await asyncio.sleep(2)
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-dev-shm-usage']
            )

            storage_state_kwargs = {}
            if os.path.exists(COOKIES_FILE):
                storage_state_kwargs['storage_state'] = COOKIES_FILE

            context = await browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent=USER_AGENT,
                locale='ar-EG',
                **storage_state_kwargs
            )

            page = await context.new_page()

            try:
                await page.goto(link, wait_until="networkidle", timeout=45000)
                navigation_successful = True
            except Exception as nav_e:
                print(f"❌ Navigation failed: {nav_e}")

            if navigation_successful:
                await asyncio.sleep(2)
                html_content = await page.content()
                soup = BeautifulSoup(html_content, "html.parser")
                page_title = soup.title.string if soup.title else "book"

                download_selector_css = 'a[href*="pdf"], a.book-dl-btn, a.btn-download, button:has-text("تحميل"), a:has-text("Download"), a:has-text("ابدأ التحميل")'

                try:
                    pdf_response, _ = await asyncio.gather(
                        page.wait_for_response(
                            lambda response: response.status in [200, 206, 301, 302] and (
                                'application/pdf' in response.headers.get('content-type', '') or
                                response.url.lower().endswith('.pdf')
                            ),
                            timeout=25000
                        ),
                        page.locator(download_selector_css).click(force=True)
                    )
                    pdf_link = pdf_response.url
                    print(f"✅ PDF direct link captured: {pdf_link}")
                except Exception:
                    pass

                if not pdf_link:
                    pdf_link = await fallback_strategy_4_network_mine(page, download_selector_css, link)

                if not pdf_link:
                    await asyncio.sleep(3)
                    final_html = await page.content()
                    final_soup = BeautifulSoup(final_html, "html.parser")
                    for a_tag in final_soup.find_all("a", href=True):
                        href = urljoin(link, a_tag["href"])
                        if href.lower().endswith(".pdf") or "download" in href.lower():
                            pdf_link = href
                            print(f"✅ PDF link found in HTML fallback: {pdf_link}")
                            break

                await context.storage_state(path=COOKIES_FILE)

            return pdf_link, page_title, is_local_path, link

    except Exception as e:
        print(f"Critical error: {e}")
        return None, "book", False, link
    finally:
        if browser:
            await browser.close()

# --- تحميل وإرسال PDF ---
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
                try:
                    async with session.head(pdf_url, headers=download_headers, allow_redirects=True, timeout=10) as head_resp:
                        content_type = head_resp.headers.get('Content-Type', '').lower()
                        content_length = int(head_resp.headers.get('Content-Length', 0))
                except:
                    async with session.get(pdf_url, headers=download_headers, allow_redirects=True, timeout=10) as head_resp:
                        content_type = head_resp.headers.get('Content-Type', '').lower()
                        content_length = int(head_resp.headers.get('Content-Length', 0))

                if 'pdf' not in content_type:
                    await context.bot.send_message(chat_id, f"⚠️ فشل التحميل: الرابط ليس PDF ({content_type})")
                    return
                if content_length < MIN_PDF_SIZE_BYTES:
                    await context.bot.send_message(chat_id, "⚠️ الملف صغير جدًا.")
                    return
            except Exception as e:
                await context.bot.send_message(chat_id, f"⚠️ فشل التحقق من الملف: {e}")
                return

            tmp_dir = tempfile.gettempdir()
            safe_title = title.replace("/", "_")[:40]
            file_path = os.path.join(tmp_dir, f"{safe_title}.pdf")

            async with session.get(pdf_url, headers=download_headers) as resp:
                if resp.status != 200:
                    await context.bot.send_message(chat_id, f"⚠️ فشل تحميل الملف (status={resp.status})")
                    return
                content = await resp.read()
                async with aiofiles.open(file_path, "wb") as f:
                    await f.write(content)

    try:
        with open(file_path, "rb") as f:
            await context.bot.send_document(chat_id=chat_id, document=f)
        await context.bot.send_message(chat_id, "✅ تم إرسال الكتاب بنجاح.")
    except Exception as e:
        await context.bot.send_message(chat_id, f"⚠️ خطأ أثناء الإرسال: {e}")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

# --- المعالجات ---
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("dl|"):
        try:
            index = int(data.split("|", 1)[1])
            link = context.user_data[TEMP_LINKS_KEY][index]
        except:
            await query.message.reply_text("⚠️ حدث خطأ أثناء زر التحميل.")
            return

        await query.edit_message_text("⏳ جاري استخراج رابط التحميل...")
        pdf_link, title, is_local_path, referer_link = await get_pdf_link_from_page(link)

        if pdf_link:
            await download_and_send_pdf(context, query.message.chat_id, pdf_link, title, is_local_path, referer_link)
        else:
            await context.bot.send_message(chat_id=query.message.chat_id, text=f"📄 فشل الاستخلاص. المصدر: {link}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📚 أهلاً! أرسل /search متبوعًا باسم الكتاب.")

async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("استخدم: /search اسم الكتاب أو المؤلف")
        return

    msg = await update.message.reply_text(f"🔍 البحث عن **{query}**...")
    results = await search_duckduckgo(query)
    if not results:
        await msg.edit_text("❌ لم أجد نتائج موثوقة.")
        return

    context.user_data[TEMP_LINKS_KEY] = [item["link"] for item in results]
    buttons = []
    lines = []
    for i, item in enumerate(results, start=0):
        title = item["title"][:100]
        lines.append(f"{i+1}. {title}")
        buttons.append([InlineKeyboardButton(f"📥 تحميل {i+1}", callback_data=f"dl|{i}")])
    await msg.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))

def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN مفقود.")
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler))
    print("✅ البوت يعمل الآن.")
    app.run_polling()

if __name__ == "__main__":
    main()
