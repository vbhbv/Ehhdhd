import os
import asyncio
import tempfile
import aiofiles
from aiohttp import ClientSession
from bs4 import BeautifulSoup
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes 
from playwright.async_api import async_playwright, Page 
from urllib.parse import urljoin 
from ddgs import DDGS # التأكد من استخدام ddgs فقط

# --- إعدادات البوت والثوابت ---
BOT_TOKEN = os.getenv("BOT_TOKEN")

USER_AGENT_HEADER = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
MIN_PDF_SIZE_BYTES = 50 * 1024 
TEMP_LINKS_KEY = "current_search_links" 
# المصادر الأقل حماية فقط
TRUSTED_DOMAINS = [
    "kotobati.com", 
    "masaha.org", 
    "books-library.net"
]

# --- دالة البحث الثورية (DuckDuckGo) ---
async def search_duckduckgo(query: str):
    """يستخدم DuckDuckGo API للبحث عن روابط PDF مباشرة في المواقع الموثوقة."""
    
    sites_query = " OR ".join([f"site:{d}" for d in TRUSTED_DOMAINS])
    full_query = f"{query} filetype:pdf OR {sites_query}"
    
    print(f"Executing search query: {full_query}")
    
    results = []
    
    # التأكد من استخدام DDGS بالشكل الصحيح
    with DDGS(timeout=5) as ddgs:
        search_results = ddgs.text(full_query, max_results=10)
        
        for r in search_results:
            link = r.get("href")
            title = r.get("title")
            
            if any(d in link for d in TRUSTED_DOMAINS) or link.lower().endswith(".pdf"):
                results.append({"title": title, "link": link})

    unique_links = {}
    for item in results:
        unique_links[item['link']] = item
    
    return list(unique_links.values())[:5]

# --- الإستراتيجية الرابعة المبتكرة: التنقيب في جميع روابط الشبكة ---
async def fallback_strategy_4_network_mine(page: Page, download_selector_css: str, link: str):
    
    network_urls = set()

    def capture_url(response):
        if response.status in [200, 206, 301, 302]:
            network_urls.add(response.url)
            
    page.on("response", capture_url)
    
    try:
        await page.locator(download_selector_css).click(timeout=15000) 
        await asyncio.sleep(7) 
        
        for url in network_urls:
            url_lower = url.lower()
            if url_lower.endswith('.pdf') or 'drive.google.com' in url_lower or 'dropbox.com' in url_lower or 'archive.org/download' in url_lower:
                print(f"PDF link found via Network Mining: {url}")
                return url
        
        return None 
        
    except Exception as e:
        print(f"Network mining failed: {e}")
        return None
        
    finally:
        try:
            page.remove_listener("response", capture_url)
        except:
            pass 

# --- دالة الاستخلاص المطلقة المُحسَّنة (V7.0) ---
async def get_pdf_link_from_page(link: str):
    """
    يستخدم Playwright لمحاكاة الضغط وينتظر استجابة شبكة تحمل ملف PDF.
    """
    pdf_link = None
    page_title = "book" 
    browser = None 
    
    # 💥 التحقق الأول: إذا كان الرابط مباشراً، لا داعي لـ Playwright
    if link.lower().endswith('.pdf') or 'archive.org/download' in link.lower() or 'drive.google.com' in link.lower():
        print(f"Direct PDF link detected. Bypassing Playwright: {link}")
        return link, "Direct PDF"
        
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            
            await page.goto(link, wait_until="domcontentloaded", timeout=40000) 
            
            html_content = await page.content()
            soup = BeautifulSoup(html_content, "html.parser")
            page_title = soup.title.string if soup.title else "book"
            
            download_selector_css = 'a[href*="pdf"], a.book-dl-btn, a.btn-download, button:has-text("تحميل"), a:has-text("Download"), a:has-text("ابدأ التحميل"), a:has-text("اضغط هنا للتحميل")'
            
            # --- محاولة 1: التزامن (gather) ---
            try:
                pdf_response, _ = await asyncio.gather(
                    page.wait_for_response(
                        lambda response: response.status in [200, 206, 301, 302] and (
                            'application/pdf' in response.headers.get('content-type', '') or 
                            response.url.lower().endswith('.pdf')
                        ),
                        timeout=30000
                    ),
                    page.click(download_selector_css, timeout=25000) 
                )
                
                pdf_link = pdf_response.url
                
            except Exception as e:
                print(f"Initial gather failed, attempting fallback strategies: {e}")
                
                # --- محاولة 2: النقر ثم التأخير ثم التنصت ---
                try:
                    await page.click(download_selector_css, timeout=25000) 
                    await asyncio.sleep(4)
                    
                    pdf_response = await page.wait_for_response(
                         lambda response: response.status in [200, 206, 301, 302] and (
                            'application/pdf' in response.headers.get('content-type', '') or 
                            response.url.lower().endswith('.pdf')
                        ),
                        timeout=10000 
                    )
                    pdf_link = pdf_response.url
                    
                except Exception as fallback_error:
                    print(f"Second fallback failed, checking HTML (Strategy 3): {fallback_error}")
                    
                    # --- محاولة 3: فحص HTML بعد النقر والتأخير ---
                    await asyncio.sleep(5) 
                    final_html_content = await page.content()
                    final_soup = BeautifulSoup(final_html_content, "html.parser")
                    
                    for a_tag in final_soup.find_all('a', href=True):
                        href = urljoin(link, a_tag['href'])
                        href_lower = href.lower()
                        
                        if href_lower.endswith('.pdf'):
                            pdf_link = href
                            print(f"PDF link found in HTML (Strategy 3): {pdf_link}")
                            break
                        
                    if not pdf_link:
                         for a_tag in final_soup.find_all('a', href=True):
                            href = urljoin(link, a_tag['href'])
                            href_lower = href.lower()

                            if 'download' in href_lower or 'drive.google.com' in href_lower or 'dropbox.com' in href_lower or 'archive.org/download' in href_lower:
                                pdf_link = href
                                print(f"General download link found in HTML (Strategy 3): {pdf_link}")
                                break
                    
                    # --- محاولة 4 (الأخيرة): التنقيب في الشبكة ---
                    if not pdf_link:
                         print("HTML check failed. Executing Network Mining (Strategy 4).")
                         pdf_link = await fallback_strategy_4_network_mine(page, download_selector_css, link)


            return pdf_link, page_title
    
    except Exception as e:
        print(f"Critical error in get_pdf_link_from_page: {e}")
        raise e
    
    finally:
        if 'page' in locals():
            try:
                await page.close()
            except:
                pass
        if browser:
            await browser.close()
            print("تم ضمان إغلاق متصفح Playwright.")


# --- باقي دوال تيليجرام (download_and_send_pdf، start، search_cmd، callback_handler، main) ---
async def download_and_send_pdf(context, chat_id, pdf_url, title="book.pdf"):
    """تحميل الملف، إرساله إلى المستخدم، ثم حذفه من القرص الصلب."""
    tmp_dir = tempfile.gettempdir()
    file_path = os.path.join(tmp_dir, title.replace("/", "_")[:40] + ".pdf")
    
    async with ClientSession() as session:
        async with session.get(pdf_url, headers=USER_AGENT_HEADER) as resp:
            if resp.status != 200:
                await context.bot.send_message(
                    chat_id=chat_id, 
                    text=f"⚠️ فشل تحميل الملف من المصدر. رمز الخطأ: {resp.status}"
                )
                return
            
            content = await resp.read()

            if len(content) < MIN_PDF_SIZE_BYTES:
                await context.bot.send_message(
                    chat_id=chat_id, 
                    text="⚠️ فشل التحميل: حجم الملف صغير جداً (غير صالح). قد يكون الرابط خاطئاً."
                )
                return
            
            async with aiofiles.open(file_path, "wb") as f:
                await f.write(content)
            
            try:
                with open(file_path, "rb") as f:
                    await context.bot.send_document(
                        chat_id=chat_id, 
                        document=f
                    )
                await context.bot.send_message(chat_id=chat_id, text="✅ تم إرسال الكتاب بنجاح.")
            except Exception as e:
                 await context.bot.send_message(chat_id=chat_id, text=f"⚠️ خطأ أثناء إرسال الملف إلى تيليجرام: {e}")
            finally:
                if os.path.exists(file_path):
                    os.remove(file_path)
                
# --- دوال أوامر تيليجرام (Telegram Commands) ---

async def start(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 بوت القيامة جاهز!\n"
        "أرسل /search متبوعًا باسم الكتاب أو المؤلف."
    )

async def search_cmd(update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("استخدم: /search اسم الكتاب أو المؤلف")
        return

    msg = await update.message.reply_text("🔍 أبحث عن الكتاب عبر DuckDuckGo (غير مقيد)...")
    
    try:
        results = await search_duckduckgo(query)

        if not results:
            await msg.edit_text("❌ لم أجد نتائج موثوقة في المكتبات المختارة. حاول بكلمات مختلفة.")
            return

        buttons = []
        text_lines = []
        
        context.user_data[TEMP_LINKS_KEY] = [item.get("link") for item in results]
        
        for i, item in enumerate(results, start=0):
            title = item.get("title")[:120]
            source = next((d.replace('.com', '').replace('.net', '') for d in TRUSTED_DOMAINS if d in item.get('link')), "رابط مباشر")
            text_lines.append(f"{i+1}. {title} (المصدر: {source})")
            buttons.append([InlineKeyboardButton(f"📥 تحميل {i+1}", callback_data=f"dl|{i}")])
            
        reply = "\n".join(text_lines)
        await msg.edit_text(reply, reply_markup=InlineKeyboardMarkup(buttons))
        
    except Exception as e:
         await msg.edit_text(f"⚠️ حدث خطأ أثناء البحث: {e}")


async def callback_handler(update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data.startswith("dl|"):
        try:
            index_str = data.split("|", 1)[1]
            index = int(index_str)
            link = context.user_data[TEMP_LINKS_KEY][index]

        except Exception:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="⚠️ حدث خطأ أثناء معالجة زر التحميل (رابط غير صالح). يرجى البحث مجدداً.",
            )
            return
            
        await query.edit_message_text("⏳ تفعيل التنصت على نوع المحتوى (MIME Type) لعبور الحماية...")
        
        try:
            pdf_link, title = await get_pdf_link_from_page(link)
            
            if pdf_link:
                await download_and_send_pdf(context, query.message.chat_id, pdf_link, title=title if title else "book")
            else:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"📄 فشل الاستخلاص. قد تكون الحماية قوية جداً. رابط المصدر: {link}",
                )
        
        except Exception as e:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"⚠️ خطأ Playwright أثناء جلب الملف: {e}",
            )


def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is missing in environment variables.")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler))

    print("البوت بدأ العمل.")
    app.run_polling()

if __name__ == "__main__":
    main()
