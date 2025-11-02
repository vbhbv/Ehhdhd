import os
import asyncio
import tempfile
import aiofiles
from aiohttp import ClientSession
from bs4 import BeautifulSoup
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes 
from playwright.async_api import async_playwright
from urllib.parse import urljoin 

# --- إعدادات Google CSE والمفاتيح ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY") 
GOOGLE_CX = os.getenv("GOOGLE_CX")           
SEARCH_URL = "https://www.googleapis.com/customsearch/v1"

# --- متغيرات ثابتة ---
USER_AGENT_HEADER = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
MIN_PDF_SIZE_BYTES = 50 * 1024 # 50 كيلوبايت كحد أدنى للملف
TEMP_LINKS_KEY = "current_search_links" 
TRUSTED_DOMAINS = [
    "kotobati.com", 
    "masaha.org", 
    "books-library.net"
]

# --- دوال مساعدة للشبكة (Utility Functions) ---

async def fetch_json(session: ClientSession, url: str, params=None):
    """جلب بيانات JSON (تستخدم لاستدعاء Google API)."""
    async with session.get(url, params=params, timeout=20) as resp:
        resp.raise_for_status()
        return await resp.json()

async def search_google_cse(session: ClientSession, query: str):
    """يبحث في محرك Google المخصص ويعيد النتائج."""
    if not GOOGLE_API_KEY or not GOOGLE_CX:
        raise ValueError("Google API Key or CX is missing in environment variables.")
        
    params = {
        "q": query,
        "cx": GOOGLE_CX,
        "key": GOOGLE_API_KEY
    }
    
    data = await fetch_json(session, SEARCH_URL, params=params)
    
    results = []
    for item in data.get("items", [])[:10]: 
        title = item.get("title")
        link = item.get("link")
        results.append({"title": title, "link": link})

    return results


# --- دالة الاستخلاص الموثوقة (باستخدام CSS Selector بسيط) ---
async def get_pdf_link_from_page(link: str):
    """
    يستخدم Playwright لفتح الصفحة والبحث عن زر التحميل المباشر.
    """
    pdf_link = None
    page_title = "book" 
    browser = None 

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            
            await page.goto(link, wait_until="domcontentloaded", timeout=30000) 
            
            html_content = await page.content()
            soup = BeautifulSoup(html_content, "html.parser")
            page_title = soup.title.string if soup.title else "book"
            
            # --- 1. استراتيجية كتباتي: البحث عن زر التحميل (btn-download) ---
            if "kotobati.com" in link:
                download_button = soup.find('a', class_='btn-download')
                if download_button and download_button.get("href"):
                    href = download_button.get("href")
                    pdf_link = urljoin(link, href)

            # --- 2. الاستراتيجية العامة: البحث عن رابط مباشر (يغطي masaha.org و books-library.net) ---
            if not pdf_link:
                # البحث عن أي رابط يحتوي على .pdf أو 'download' في اسمه أو ينتهي بـ .pdf
                for a in soup.select("a[href]"):
                    href = a["href"]
                    if href.lower().endswith(".pdf") or "download" in href.lower():
                        if href.startswith("/"):
                            pdf_link = urljoin(link, href)
                        else:
                            pdf_link = href
                        break 
            
        return pdf_link, page_title
    
    except Exception as e:
        raise e
    
    finally:
        if browser:
            await browser.close()
            print("تم ضمان إغلاق متصفح Playwright.")


# --- دالة التحميل والإرسال والحذف ---
async def download_and_send_pdf(context, chat_id, pdf_url, title="book.pdf"):
    """تحميل الملف، إرساله إلى المستخدم، ثم حذفه من القرص الصلب."""
    # (هذه الدالة تبقى كما هي وهي مستقرة)
    tmp_dir = tempfile.gettempdir()
    file_path = os.path.join(tmp_dir, title.replace("/", "_")[:40] + ".pdf")
    
    async with ClientSession() as session:
        async with session.get(pdf_url, headers=USER_AGENT_HEADER) as resp:
            if resp.status != 200:
                await context.bot.send_message(
                    chat_id=chat_id, 
                    text=f"⚠️ لم أتمكن من تحميل الملف من المصدر. رمز الخطأ: {resp.status}"
                )
                return
            
            content = await resp.read()

            if len(content) < MIN_PDF_SIZE_BYTES:
                await context.bot.send_message(
                    chat_id=chat_id, 
                    text="⚠️ فشل التحميل: الملف المُرسَل يبدو فارغًا أو حجمه صغير جدًا. قد يكون رابط التحميل غير صحيح."
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
                    print(f"تم حذف الملف المؤقت: {file_path}")
                
# --- دوال أوامر تيليجرام (Telegram Commands) ---

async def start(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 مرحبًا بك في بوت تحميل الكتب!\n"
        "أرسل أمر /search متبوعًا باسم الكتاب أو المؤلف.\n\n"
        "مثال:\n/search قلعة العز"
    )

async def search_cmd(update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("استخدم: /search اسم الكتاب أو المؤلف")
        return

    msg = await update.message.reply_text("🔍 أبحث عن الكتاب عبر Google API...")
    
    try:
        async with ClientSession() as session:
            # 1. جلب النتائج الأولية من Google CSE
            initial_results = await search_google_cse(session, query) 

        if not initial_results:
            await msg.edit_text("❌ لم أجد نتائج. حاول بكلمات مختلفة.")
            return

        # 2. التصفية اليدوية: قبول الروابط من النطاقات الموثوقة (الأقل حماية) فقط
        results = [
            item for item in initial_results 
            if any(domain in item.get('link') for domain in TRUSTED_DOMAINS)
        ][:5]
        
        if not results:
            await msg.edit_text("❌ لم أجد أي رابط تحميل مباشر موثوق. حاول بكلمات بحث أخرى.")
            return

        buttons = []
        text_lines = []
        
        context.user_data[TEMP_LINKS_KEY] = [item.get("link") for item in results]
        
        # عرض النتائج التي تم تصفيتها
        for i, item in enumerate(results, start=0):
            title = item.get("title")[:120]
            source = next((d.replace('.com', '').replace('.net', '') for d in TRUSTED_DOMAINS if d in item.get('link')), "مصدر خارجي")
            text_lines.append(f"{i+1}. {title} (المصدر: {source})")
            buttons.append([InlineKeyboardButton(f"📥 تحميل {i+1}", callback_data=f"dl|{i}")])
            
        reply = "\n".join(text_lines)
        await msg.edit_text(reply, reply_markup=InlineKeyboardMarkup(buttons))
        
    except ValueError as e:
         await msg.edit_text(f"⚠️ خطأ في الإعداد: {e}")
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
            
            if TEMP_LINKS_KEY not in context.user_data or index >= len(context.user_data[TEMP_LINKS_KEY]):
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text="❌ انتهت صلاحية رابط التحميل أو لم يعد موجودًا. يرجى البحث مجدداً.",
                )
                return

            link = context.user_data[TEMP_LINKS_KEY][index]

        except Exception:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="⚠️ حدث خطأ أثناء معالجة زر التحميل (رابط غير صالح). يرجى البحث مجدداً.",
            )
            return
            
        await query.edit_message_text("⏳ أستخدم متصفح وهمي لجلب رابط الملف النهائي...")
        
        try:
            pdf_link, title = await get_pdf_link_from_page(link)
            
            if pdf_link:
                await download_and_send_pdf(context, query.message.chat_id, pdf_link, title=title if title else "book")
            else:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"📄 لم نتمكن من جلب رابط PDF مباشر من هذا المصدر. يرجى المحاولة بمصدر آخر.\n{link}",
                )
        
        except Exception as e:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"⚠️ خطأ Playwright أثناء جلب الملف من المصدر: {e}",
            )


def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is missing in environment variables.")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler))

    print("البوت بدأ العمل... اضغط Ctrl+C للإيقاف.")
    app.run_polling()

if __name__ == "__main__":
    main()
