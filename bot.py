import os
import asyncio
import tempfile
import aiofiles
import re
from telethon import TelegramClient, errors
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# ===== إعدادات Telethon وTelegram Bot =====
API_ID = 26597373
API_HASH = "03b65897b8dfe7b9d237fb69d687d615"
BOT_TOKEN = os.getenv("BOT_TOKEN")  # ضع توكن البوت هنا أو كمتغير بيئة

# قناة البحث
CHANNELS = ["books921383837"]  # معرف القناة فقط

# الحد الأقصى للرسائل لكل قناة
PER_CHANNEL_LIMIT = 200
GLOBAL_RESULTS_LIMIT = 12

# مفتاح لتخزين نتائج البحث
TEMP_LINKS_KEY = "tg_search_results"

# جلسة Telethon
tele_client = TelegramClient('bot_session', API_ID, API_HASH)

# ==== دوال مساعدة ====
def safe_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", name)[:60]

# ==== Telethon ==== 
async def telethon_start():
    await tele_client.start(bot_token=BOT_TOKEN)
    me = await tele_client.get_me()
    print("Telethon started as bot:", me.username or me.id)

async def search_in_channels(query: str):
    query_lc = query.lower()
    found = []

    for ch in CHANNELS:
        try:
            async for msg in tele_client.iter_messages(ch, limit=PER_CHANNEL_LIMIT):
                if not msg:
                    continue
                text = (msg.message or "").lower()
                file_name = (getattr(msg.file, "name", "") or "").lower() if msg.file else ""
                combined = f"{text} {file_name}".lower()
                if query_lc in combined:
                    found.append({
                        "chat_id": msg.chat_id,
                        "channel": ch,
                        "msg_id": msg.id,
                        "snippet": file_name or text[:120],
                        "has_file": bool(msg.file)
                    })
                if len(found) >= GLOBAL_RESULTS_LIMIT:
                    return found
        except errors.ChannelPrivateError:
            print(f"Private channel or bot not member: {ch}")
            continue
        except Exception as e:
            print(f"Error scanning {ch}: {e}")
            continue
    return found

async def download_message_media(chat_id: int, msg_id: int):
    try:
        msg = await tele_client.get_messages(chat_id, ids=msg_id)
        if not msg or not msg.file:
            return None, "لا يوجد ملف في الرسالة"
        tmp_dir = tempfile.gettempdir()
        out_name = safe_filename(f"tg_{chat_id}_{msg_id}")
        out_path = await tele_client.download_media(msg, file=os.path.join(tmp_dir, out_name))
        return out_path, None
    except Exception as e:
        return None, str(e)

# ==== Telegram Bot Handlers ====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 مرحبًا! أرسل /search <اسم الكتاب> للبحث في قناة books921383837."
    )

async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("استخدم: /search <اسم الكتاب>")
        return
    msg = await update.message.reply_text(f"🔎 أبحث عن: {query} ...")

    try:
        results = await search_in_channels(query)
    except Exception as e:
        await msg.edit_text(f"⚠️ خطأ أثناء البحث: {e}")
        return

    if not results:
        await msg.edit_text("❌ لم يتم العثور على نتائج في القناة.")
        return

    # حفظ النتائج للوصول عند الضغط على الزر
    context.user_data[TEMP_LINKS_KEY] = results

    lines = []
    buttons = []
    for i, r in enumerate(results):
        snippet = (r["snippet"][:80] + "...") if len(r["snippet"]) > 80 else r["snippet"]
        lines.append(f"{i+1}. {snippet} — {r['channel']} {'📎' if r['has_file'] else ''}")
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

    # تنزيل الملف
    path, err = await download_message_media(item["chat_id"], item["msg_id"])
    if not path:
        try:
            # إذا لم يكن هناك ملف، إعادة التوجيه
            await tele_client.forward_messages(entity=update.effective_user.id,
                                               messages=item["msg_id"],
                                               from_peer=item["chat_id"])
            await query.message.reply_text("✅ تم إعادة توجيه الرسالة (لا يوجد ملف مباشر).")
            return
        except Exception as e:
            await query.message.reply_text(f"⚠️ لا يوجد ملف ولا يمكن إعادة التوجيه: {err} / {e}")
            return

    # إرسال الملف عبر البوت
    try:
        async with aiofiles.open(path, "rb") as f:
            await context.bot.send_document(chat_id=update.effective_user.id, document=await f.read())
        await query.message.reply_text("✅ تم إرسال الملف.")
    except Exception as e:
        await query.message.reply_text(f"⚠️ خطأ أثناء الإرسال: {e}")
    finally:
        if os.path.exists(path):
            os.remove(path)

# ==== تشغيل البوت ====
async def async_main():
    await tele_client.start(bot_token=BOT_TOKEN)
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler))
    print("البوت جاهز للعمل...")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(async_main())
