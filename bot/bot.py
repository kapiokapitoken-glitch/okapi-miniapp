import os
import threading
from flask import Flask
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")
GAME_URL  = os.getenv("GAME_URL", "https://okapi-miniapp-7ex5j.ondigitalocean.app/")
PORT      = int(os.getenv("PORT", "8080"))  # DO health check için

# --- Flask: health-check HTTP sunucusu ---
flask_app = Flask(__name__)

@flask_app.get("/")
def health_root():
    return "ok", 200

# --- Telegram Bot ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[KeyboardButton("🎮 Oyunu Aç", web_app=WebAppInfo(url=GAME_URL))]]
    await update.message.reply_text(
        "Okapi Run başlasın! Butona bas ve oyunu aç 🎮",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN env var missing")

    # Flask'ı ARKA PLANDA başlat (health-check için)
    threading.Thread(
        target=lambda: flask_app.run(host="0.0.0.0", port=PORT),
        daemon=True
    ).start()

    # Bot'u ANA THREAD'de çalıştır (event loop hatası olmaz)
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.run_polling()

if __name__ == "__main__":
    main()
