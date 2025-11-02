# main.py
import os
import asyncio
import tempfile
import re
import aiofiles
from urllib.parse import urlparse
from telethon import TelegramClient, errors
from telethon.tl.types import Message
from telethon.errors import RpcError
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

API_ID = int(os.getenv("API_ID") or "0")
API_HASH = os.getenv("API_HASH") or ""
BOT_TOKEN = os.getenv("BOT_TOKEN") or ""
# channels: comma separated list of channel usernames or ids (bot must be member)
CHANNELS = os.getenv("CHANNELS", "").split(",") if os.getenv("CHANNELS") else [
    # ضع هنا أسماء القنوات أو معرفاتها التي أضفت البوت إليها، مثال:
    # "arab_books_channel1", "arab_ebooks_channel2", "@some_public_channel"
]

# حد نتائج البحث لكل قناة وعدد القنوات التي نفحصها
PER_CHANNEL_LIMIT = 200
GLOBAL_RESULTS_LIMIT = 12

# مفتاح لتخزين النتائج في user_data
TEMP_LINKS_KEY = "tg_search_results"

# اسم جلسة Telethon محلي (لا تضعه حساساً)
SESSION_NAME = "telethon_bot_session"

# helper: safe filename
def safe_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", name)[:60]

# --- إنشاء Telethon client (سيبدأ لاحقًا داخل اليوتيليتي async) ---
tele_client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

# ------------------ دوال Telethon (async) ------------------
async def telethon_start():
    # start as bot using token (this connects as bot, must be member of channels)
    await tele_client.start(bot_token=BOT_TOKEN)
    # verify
    who = await tele_client.get_me()
    print("Telethon started as:", who.username or who.id)

async def search_in_channels(query: str):
    """
    ابحث في القنوات المحددة عن الرسائل التي تحتوي الكلمة في النص أو اسم الملف.
    إرجاع قائمة عناصر: {'chat_id': chat_id, 'msg_id': id, 'snippet': text_or_filename, 'has_file': bool}
    """
    query_lc = query.lower()
    found = []
    for ch in CHANNELS:
        ch = ch.strip()
        if not ch:
            continue
        try:
            async for msg in tele_client.iter_messages(ch, limit=PER_CHANNEL_LIMIT):
                if not msg:
                    continue
                # check message text
                text = (msg.message or "") or ""
                file_name = ""
                if msg.file:
                    # telethon message.file.name may be None; try attributes
                    file_name = (getattr(msg.file, "name", "") or "") 
                combined = f"{text} {file_name}".lower()
                if query_lc in combined:
                    found.append({
                        "chat_id": msg.chat_id,
                        "channel": ch,
                        "msg_id": msg.id,
                        "snippet": (file_name or text[:120]) or "رسالة بدون نص",
                        "has_file": bool(msg.file)
                    })
                # short-circuit if reached global limit
                if len(found) >= GLOBAL_RESULTS_LIMIT:
                    return found
        except errors.ChannelPrivateError:
            print(f"Private channel or access denied: {ch}")
            continue
        except RpcError as e:
            print(f"RPC error for {ch}: {e}")
            continue
        except Exception as e:
            print(f"Error scanning {ch}: {e}")
            continue
    return found

async def download_message_media(chat_id: int, msg_id: int):
    """
    يأخذ chat_id و msg_id ثم ينزل الميديا إن وجدت ويعيد مسار الملف المحلي.
    """
    try:
        msg = await tele_client.get_messages(chat_id, ids=msg_id)
        if not msg:
            return None, "الرسالة غير موجودة"
        if not msg.file:
            return None, "الرسالة لا تحتوي ملفًا"
        tmp_dir = tempfile.gettempdir()
        out_name = safe_filename(f"tg_{chat_id}_{msg_id}")
        out_path = await tele_client.download_media(msg, file=os.path.join(tmp_dir, out_name))
        return out_path, None
    except Exception as e:
        return None, str(e)

# ------------------ Telegram-bot handlers ------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 مرحبًا — أرسل /search <اسم الكتاب> للبحث في قنوات الكتب (البوت يجب أن يكون عضوًا).")

async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("استخدم: /search <اسم الكتاب> (مثال: /search دوستويفسكي)")
        return
    msg = await update.message.reply_text(f"🔎 أبحث عن: {query} ...")
    # perform telethon search (runs in same loop)
    try:
        results = await search_in_channels(query)
    except Exception as e:
        await msg.edit_text(f"⚠️ خطأ أثناء البحث: {e}")
        return

    if not results:
        await msg.edit_text("❌ لم يتم العثور على نتائج في القنوات المحددة.")
        return

    # save results in user_data to reference on callbacks
    context.user_data[TEMP_LINKS_KEY] = results

    # build message text + buttons
    lines = []
    buttons = []
    for i, r in enumerate(results):
        snippet = (r["snippet"][:80] + "...") if len(r["snippet"])>80 else r["snippet"]
        channel_display = r["channel"]
        lines.append(f"{i+1}. {snippet} — {channel_display} {'📎' if r['has_file'] else ''}")
        buttons.append([InlineKeyboardButton(f"📥 تحميل {i+1}", callback_data=f"dl|{i}")])

    await msg.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if not data.startswith("dl|"):
        return
    idx = int(data.split("|",1)[1])
    results = context.user_data.get(TEMP_LINKS_KEY, [])
    if idx < 0 or idx >= len(results):
        await query.message.reply_text("⚠️ نتيجة غير صالحة.")
        return
    item = results[idx]
    await query.edit_message_text("⏳ أحاول تنزيل الملف وإرساله...")

    # download via Telethon
    path, err = await download_message_media(item["chat_id"], item["msg_id"])
    if not path:
        # if no file, try to forward the message (if possible)
        try:
            await tele_client.forward_messages(entity=update.effective_user.id, messages=item["msg_id"], from_peer=item["chat_id"])
            await query.message.reply_text("✅ تم إعادة توجيه الرسالة (لم يحتوي الملف على تنزيل مباشر).")
            return
        except Exception as e:
            await query.message.reply_text(f"⚠️ لا يوجد ملف ولا يمكن إعادة التوجيه: {err} / {e}")
            return

    # send file via bot API
    try:
        async with aiofiles.open(path, "rb") as f:
            await context.bot.send_document(chat_id=update.effective_user.id, document=await f.read())
        await query.message.reply_text("✅ تم إرسال الملف.")
    except Exception as e:
        await query.message.reply_text(f"⚠️ خطأ أثناء الإرسال: {e}")
    finally:
        # cleanup
        try:
            if os.path.exists(path):
                os.remove(path)
        except:
            pass

# ------------------ main runner ------------------
async def async_main():
    # start telethon
    await telethon_start()
    # start telegram-bot
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler))
    print("Bot is running...")
    await app.run_polling()

if __name__ == "__main__":
    # run the async main
    asyncio.run(async_main())
