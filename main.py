import os
import asyncio
import tempfile
import aiofiles
import random 
from aiohttp import ClientSession
from bs4 import BeautifulSoup
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes 
from playwright.async_api import async_playwright, Page 
from urllib.parse import urljoin 
from ddgs import DDGS 

# --- إعدادات البوت والثوابت ---
BOT_TOKEN = os.getenv("BOT_TOKEN")

# وكيل مستخدم لسطح المكتب (للتحصين ضد الكشف)
USER_AGENT = 'Mozilla/50 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
USER_AGENT_HEADER = {'User-Agent': USER_AGENT}

MIN_PDF_SIZE_BYTES = 50 * 1024 
TEMP_LINKS_KEY = "current_search_links" 
TRUSTED_DOMAINS = [
    "kotobati.com", 
    "masaha.org", 
    "books-library.net"
]

# --- دالة البحث (DDGS - بدون تغيير) ---
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
                    is_general_section = ("kotobati.com" in link and ("/section/" in link or "/category/" in link))
                    if not is_general_section:
                         results.append({"title": title.strip(), "link": link})
    except Exception as e:
        print(f"DDGS search failed: {e}")
        return []

    unique_links = {}
    for item in results:
        unique_links[item['link']] = item
    
    return list(unique_links.values())[:5]

# ----------------------------------------------------------------------
# --- دالة الاستخلاص المطلقة المُحسَّنة (V12.1.2 - الضربة التكتيكية النهائية) ---
# ----------------------------------------------------------------------
async def get_pdf_link_from_page(link: str):
    """
    الحد الأقصى للتعدين: إخفاء الهوية، محاكاة السلوك، النقر القسري، وحل Blob الجذري.
    """
    pdf_link = None
    page_title = "book" 
    browser = None 
    
    # تهيئة المتغيرات لضمان عدم حدوث UnboundLocalError
    is_local_path = False 
    network_urls = set() 
    
    if link.lower().endswith('.pdf') or 'archive.org/download' in link.lower() or 'drive.google.com' in link.lower():
        return link, "Direct PDF", False
        
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox', 
                    '--disable-setuid-sandbox',
                    '--disable-blink-features=AutomationControlled', 
                    f'--user-agent={USER_AGENT}' 
                ]
            )
            context = await browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent=USER_AGENT
            )
            
            # 💥 إخفاء الهوية الرقمية (Anti-Detection Script)
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """)
            
            page = await context.new_page()

            await page.goto(link, wait_until="domcontentloaded", timeout=40000) 
            
            # 💥 محاكاة السلوك (Behavioral Emulation)
            try:
                await page.mouse.wheel(0, random.randint(300, 800)) 
                await asyncio.sleep(random.uniform(1.5, 3))         
                await page.mouse.wheel(0, -random.randint(200, 500)) 
                await asyncio.sleep(random.uniform(1, 2.5))
            except Exception:
                 pass
            
            html_content = await page.content()
            soup = BeautifulSoup(html_content, "html.parser")
            page_title = soup.title.string if soup.title else "book"
            download_selector_css = 'a[href*="pdf"], a.book-dl-btn, a.btn-download, button:has-text("تحميل"), a:has-text("Download"), a:has-text("ابدأ التحميل"), a:has-text("اضغط هنا للتحميل")'
            
            # 1. الانتظار الذكي (Strategy 2)
            try:
                await page.wait_for_selector('a[href$=".pdf"], a[href*="download"], a[href*="drive.google.com"]', timeout=10000)
                html_content = await page.content()
                soup = BeautifulSoup(html_content, "html.parser")
                for a_tag in soup.find_all('a', href=True):
                    href = urljoin(link, a_tag['href'])
                    if href.lower().endswith('.pdf') or 'download' in href.lower() or 'drive.google.com' in href.lower():
                        pdf_link = href
                        break
            except Exception:
                pass 
                
            # --- إذا لم يتم العثور على الرابط، نبدأ دورة النقر والمنصتات ---
            if not pdf_link:
                
                # إعداد منصت الشبكة
                def capture_url(response):
                    if response.status in [200, 206, 301, 302]:
                        network_urls.add(response.url)
                page.on("response", capture_url)
                
                # إعداد منصت التنزيل (Download Listener)
                download_event = None
                temp_dir = tempfile.gettempdir()
                temp_file_name = f"temp_{os.getpid()}_{random.randint(100, 999)}.pdf"
                temp_file_path = os.path.join(temp_dir, temp_file_name)
                
                def capture_download(download):
                    nonlocal download_event
                    download_event = download
                page.on('download', capture_download)

                # --- 💥 الضربة التكتيكية: النقر القسري الأول والوحيد ---
                try:
                    # جعل الزر مرئياً والنقر عليه قسرياً مرة واحدة
                    await page.locator(download_selector_css).scroll_into_view_if_needed(timeout=5000)
                    await page.locator(download_selector_css).click(timeout=15000, force=True)
                    await asyncio.sleep(7) 

                except Exception:
                     # محاولة النقر بـ JavaScript إذا فشل النقر بـ Locator
                     try:
                        # 💥 تصحيح V12.1.2: استخدام arg لتمرير المتغير (لمنع خطأ الـ f-string)
                        await page.evaluate("""
                            (selector) => {
                                const element = document.querySelector(selector);
                                if (element) {
                                    element.click();
                                }
                            }
                        """, download_selector_css) # تمرير المتغير هنا كـ arg
                        await asyncio.sleep(7) 
                     except Exception:
                         pass
                
                # --- تقييم النتائج بعد النقر الوحيد ---
                
                # 2. تقييم منصت التنزيل (Strategy 6 - Blob/Local Save)
                if download_event:
                    await download_event.save_as(temp_file_path)
                    pdf_link = temp_file_path
                    is_local_path = True
                
                # 3. تقييم منصت الشبكة (Strategy 4 - Network Mine)
                if not pdf_link:
                    for url in network_urls:
                        url_lower = url.lower()
                        if url_lower.endswith('.pdf') or 'drive.google.com' in url_lower or 'dropbox.com' in url_lower or 'archive.org/download' in url_lower:
                            pdf_link = url
                            break
                
                # تنظيف المنصتات
                try:
                    page.remove_listener("response", capture_url)
                    page.remove_listener('download', capture_download)
                except:
                    pass

            # 5. فحص HTML النهائي (Strategy 3)
            if not pdf_link:
                await asyncio.sleep(5) 
                final_html_content = await page.content()
                final_soup = BeautifulSoup(final_html_content, "html.parser")
                for a_tag in final_soup.find_all('a', href=True):
                    href = urljoin(link, a_tag['href'])
                    href_lower = href.lower()
                    if href_lower.endswith('.pdf') or 'download' in href_lower:
                        pdf_link = href
                        break

            # التأكد من العنوان النهائي
            if not page_title:
                 html_content = await page.content()
                 soup = BeautifulSoup(html_content, "html.parser")
                 page_title = soup.title.string if soup.title else "book"

            return pdf_link, page_title, is_local_path 
    
    except Exception as e:
        return None, "book", False
    
    finally:
        if browser:
            await browser.close()


# --- دالة التحميل والإرسال (بدون تغيير) ---
async def download_and_send_pdf(context, chat_id, source, title="book.pdf", is_local_path=False):
    """تحميل الملف، إرساله إلى المستخدم، ثم حذفه من القرص الصلب."""
    
    if is_local_path:
        file_path = source 
    else:
        pdf_url = source
        tmp_dir = tempfile.gettempdir()
        file_path = os.path.join(tmp_dir, title.replace("/", "_")[:40] + ".pdf")
        
        async with ClientSession() as session:
            async with session.get(pdf_url, headers=USER_AGENT_HEADER) as resp:
                if resp.status != 200:
                    await context.bot.send_message(chat_id=chat_id, text=f"⚠️ فشل تحميل الملف من المصدر. رمز الخطأ: {resp.status}")
                    return
                content = await resp.read()
                if len(content) < MIN_PDF_SIZE_BYTES:
                    await context.bot.send_message(chat_id=chat_id, text="⚠️ فشل التحميل: حجم الملف صغير جداً (غير صالح).")
                    return
                async with aiofiles.open(file_path, "wb") as f:
                    await f.write(content)

    # --- منطق الإرسال والتنظيف ---
    try:
        with open(file_path, "rb") as f:
            await context.bot.send_document(chat_id=chat_id, document=f)
        await context.bot.send_message(chat_id=chat_id, text="✅ تم إرسال الكتاب بنجاح.")
    except Exception as e:
         await context.bot.send_message(chat_id=chat_id, text=f"⚠️ خطأ أثناء إرسال الملف إلى تيليجرام: {e}")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

# --- دالة Callback (بدون تغيير) ---
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data.startswith("dl|"):
        try:
            index_str = data.split("|", 1)[1]
            index = int(index_str)
            link = context.user_data[TEMP_LINKS_KEY][index]

        except Exception:
            await context.bot.send_message(chat_id=query.message.chat_id, text="⚠️ حدث خطأ أثناء معالجة زر التحميل (رابط غير صالح).")
            return
            
        await query.edit_message_text("⏳ تفعيل استراتيجية الاستخلاص الناري (V12.1.2 - الضربة التكتيكية)...")
        
        try:
            pdf_link, title, is_local_path = await get_pdf_link_from_page(link)
            
            if pdf_link:
                await download_and_send_pdf(context, query.message.chat_id, pdf_link, title=title if title else "book", is_local_path=is_local_path)
            else:
                await context.bot.send_message(chat_id=query.message.chat_id, text=f"📄 فشل الاستخلاص. رابط المصدر: {link}")
        
        except Exception as e:
            await context.bot.send_message(chat_id=query.message.chat_id, text=f"⚠️ خطأ Playwright أثناء جلب الملف: {e}")

# --- باقي دوال تيليجرام (start، search_cmd، main) ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 بوت القيامة جاهز!\n"
        "أرسل /search متبوعًا باسم الكتاب أو المؤلف."
    )

async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("استخدم: /search اسم الكتاب أو المؤلف")
        return

    msg = await update.message.reply_text(f"🔍 أبحث عن **{query}** عبر **DuckDuckGo** (فلترة صارمة للنتائج)...")
    
    try:
        results = await search_duckduckgo(query)

        if not results:
            await msg.edit_text("❌ لم أجد نتائج موثوقة في المكتبات المختارة. حاول بكلمات مختلفة أو جرب البحث مرة أخرى.")
            return

        buttons = []
        text_lines = []
        
        context.user_data[TEMP_LINKS_KEY] = [item.get("link") for item in results]
        
        for i, item in enumerate(results, start=0):
            title = item.get("title")[:120]
            source = next((d.replace('.com', '').replace('.net', '') for d in TRUSTED_DOMAINS if d in item.get('link')), "مباشر/عام")
            text_lines.append(f"{i+1}. {title} (المصدر: {source})")
            buttons.append([InlineKeyboardButton(f"📥 تحميل {i+1}", callback_data=f"dl|{i}")])
            
        reply = "\n".join(text_lines)
        await msg.edit_text(reply, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(buttons))
        
    except Exception as e:
         await msg.edit_text(f"⚠️ حدث خطأ أثناء البحث: {e}")

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
