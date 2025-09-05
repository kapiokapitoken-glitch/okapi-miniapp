import os
import logging
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from itsdangerous import TimestampSigner, BadSignature

from telegram import Update
from telegram.ext import (
    Application, ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
)

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("kapi-run")

# ---------- ENV ----------
BOT_TOKEN = os.environ["BOT_TOKEN"]
GAME_SHORT_NAME = os.environ["GAME_SHORT_NAME"]
PUBLIC_GAME_URL = os.environ["PUBLIC_GAME_URL"].rstrip("/") + "/"
SECRET = os.environ.get("SECRET", "change-me")

# ---------- Signer ----------
signer = TimestampSigner(SECRET)

# ---------- FastAPI & PTB ----------
app = FastAPI()
tg_app: Application = ApplicationBuilder().token(BOT_TOKEN).build()

# ---------- Handlers ----------
async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="pong ✅")

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        log.info("Sending game: short_name=%s to chat_id=%s", GAME_SHORT_NAME, chat_id)
        await context.bot.send_game(chat_id=chat_id, game_short_name=GAME_SHORT_NAME)
    except Exception as e:
        err = f"send_game error: {e}"
        log.error(err)
        await context.bot.send_message(chat_id=chat_id, text=f"⚠️ {err}")

tg_app.add_handler(CommandHandler("ping", cmd_ping))
tg_app.add_handler(CommandHandler(["start", "play"], cmd_start))

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cq = update.callback_query
    if cq and cq.game_short_name == GAME_SHORT_NAME:
        user_id = cq.from_user.id
        chat_id = cq.message.chat.id if cq.message else None
        message_id = cq.message.message_id if cq.message else None

        payload = f"{user_id}:{chat_id}:{message_id}"
        token = signer.sign(payload).decode()
        url = f"{PUBLIC_GAME_URL}#u={user_id}&c={chat_id}&m={message_id}&t={token}"
        await cq.answer(url=url)
    else:
        await cq.answer(text="Unknown game.", show_alert=True)

tg_app.add_handler(CallbackQueryHandler(on_callback))

# ---------- PTB lifecycle ----------
@app.on_event("startup")
async def _on_startup():
    # Application'ı initialize ediyoruz; tg_app.bot artık initialized
    await tg_app.initialize()
    log.info("PTB Application initialized.")

@app.on_event("shutdown")
async def _on_shutdown():
    await tg_app.shutdown()
    log.info("PTB Application shutdown.")

# ---------- Basit kontrol route'u ----------
@app.get("/bot/check")
def bot_check():
    return PlainTextResponse("bot route OK")

# ---------- Webhook endpoint ----------
@app.post("/bot/webhook")
async def tg_webhook(request: Request):
    data = await request.json()
    log.info("Webhook hit ✓")
    # ARTIK initialize edilmiş botu kullanıyoruz:
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return JSONResponse({"ok": True})

# ---------- Score endpoint ----------
@app.post("/api/score")
async def post_score(request: Request):
    body = await request.json()
    try:
        user_id = int(body["user_id"])
        score = int(body["score"])
        chat_id = int(body["chat_id"])
        message_id = int(body["message_id"])
        token = body["token"]
    except Exception:
        raise HTTPException(status_code=400, detail="Bad payload")

    try:
        payload = signer.unsign(token, max_age=1800).decode()
    except BadSignature:
        raise HTTPException(status_code=403, detail="Bad token")

    if payload != f"{user_id}:{chat_id}:{message_id}":
        raise HTTPException(status_code=403, detail="Token mismatch")

    await tg_app.bot.set_game_score(
        user_id=user_id, score=score,
        chat_id=chat_id, message_id=message_id,
        force=False, disable_edit_message=False
    )
    return JSONResponse({"ok": True})

# ---------- Health ----------
@app.get("/health")
def health_root():
    return PlainTextResponse("OK")

@app.get("/api/health")
def health_api():
    return PlainTextResponse("OK from /api/health")
