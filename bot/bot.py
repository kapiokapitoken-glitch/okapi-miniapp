# bot/bot.py
import os
import threading
import logging
from flask import Flask
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes

# ----- Logging -----
logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("okapi-bot")

# ----- Env -----
BOT_TOKEN = os.getenv("BOT_TOKEN")
GAME_URL  = os.getenv("GAME_URL", "https://okapi-miniapp-7ex5j.ondigitalocean.app/")
PORT      = int(os.getenv("PORT", "8080"))  # DigitalOcean health check

# ----- Flask: health-check -----
flask_app = Flask(__name__)

@flask_app.get("/")
def health_root():
    return "ok", 200

# ----- Telegram handlers -----
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[KeyboardButton("🎮 Oyunu Aç", web_app=WebAppInfo(url=GAME_URL))]]
    await update.message.reply_text(
        "Okapi Run başlasın! Butona bas ve oyunu aç 🎮",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )

# polling başlamadan önce bir kez çalışacak
async def _post_init(app: Application):
    try:
        # Eski webhook varsa sil; kuyruktaki bekleyenleri de at
        await app.bot.delete_webhook(drop_pending_updates=True)
        log.info("Webhook deleted & pending updates dropped.")
    except Exception as e:
        log.warning(f"delete_webhook skipped: {e}")

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN env var missing")

    # Flask'ı arka planda başlat (health için)
    threading.Thread(
        target=lambda: flask_app.run(host="0.0.0.0", port=PORT),
        daemon=True
    ).start()

    # Telegram botu ana thread'de çalıştır
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.post_init = _post_init

    log.info("Starting polling...")
    app.run_polling(drop_pending_updates=True)  # temiz başlangıç

if __name__ == "__main__":
    main()

