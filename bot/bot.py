import os
import threading
from flask import Flask
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")
GAME_URL  = os.getenv("GAME_URL", "https://okapi-miniapp-7ex5j.ondigitalocean.app/")
PORT      = int(os.getenv("PORT", "8080"))  # DO health check için

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[KeyboardButton("🎮 Oyunu Aç", web_app=WebAppInfo(url=GAME_URL))]]
    await update.message.reply_text(
        "Okapi Run başlasın! Butona bas ve oyunu aç 🎮",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )

def run_bot():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN env var missing")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.run_polling(close_loop=False)  # bloklamasın diye thread'de

# Flask health server
flask_app = Flask(__name__)
@flask_app.get("/")
def health_root():
    return "ok", 200

if __name__ == "__main__":
    t = threading.Thread(target=run_bot, daemon=True)
    t.start()
    flask_app.run(host="0.0.0.0", port=PORT)
