import os
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from itsdangerous import TimestampSigner, BadSignature
from telegram import Update
from telegram.ext import Application, ApplicationBuilder, CommandHandler, CallbackQueryHandler

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

# Ortam değişkenleri
BOT_TOKEN = os.environ["BOT_TOKEN"]
GAME_SHORT_NAME = os.environ["GAME_SHORT_NAME"]
PUBLIC_GAME_URL = os.environ["PUBLIC_GAME_URL"].rstrip("/") + "/"
SECRET = os.environ.get("SECRET", "change-me")
DATABASE_URL = os.environ["DATABASE_URL"]  # asyncpg bağlantısı

# DB engine (async)
engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)

signer = TimestampSigner(SECRET)
app = FastAPI()

# Telegram bot uygulaması
tg_app: Application = ApplicationBuilder().token(BOT_TOKEN).build()


# --- Telegram Bot Komutları ---

async def cmd_start(update: Update, context):
    await context.bot.send_game(chat_id=update.effective_chat.id, game_short_name=GAME_SHORT_NAME)


async def cmd_ping(update: Update, context):
    await update.message.reply_text("pong 🏓")


tg_app.add_handler(CommandHandler(["start", "play"], cmd_start))
tg_app.add_handler(CommandHandler("ping", cmd_ping))


async def on_callback(update: Update, context):
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


# --- Webhook endpoint ---

@app.post("/bot/webhook")
async def tg_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return JSONResponse({"ok": True})


# --- Skor kaydetme & leaderboard ---

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

    async with engine.begin() as conn:
        # Kullanıcı var mı kontrol et
        result = await conn.execute(
            text("SELECT score FROM scores WHERE user_id = :uid"),
            {"uid": user_id}
        )
        row = result.first()
        if row:
            new_score = row[0] + score
            await conn.execute(
                text("UPDATE scores SET score = :s WHERE user_id = :uid"),
                {"s": new_score, "uid": user_id}
            )
        else:
            await conn.execute(
                text("INSERT INTO scores (user_id, score) VALUES (:uid, :s)"),
                {"uid": user_id, "s": score}
            )

    await tg_app.bot.set_game_score(
        user_id=user_id, score=score,
        chat_id=chat_id, message_id=message_id,
        force=False, disable_edit_message=False
    )
    return JSONResponse({"ok": True})


@app.get("/api/leaderboard")
async def leaderboard():
    async with engine.begin() as conn:
        result = await conn.execute(
            text("SELECT user_id, score FROM scores ORDER BY score DESC LIMIT 10")
        )
        rows = result.fetchall()
    return [{"user_id": r[0], "score": r[1]} for r in rows]


# --- Health Check ---

@app.get("/health")
def health_root():
    return PlainTextResponse("OK")


@app.get("/api/health")
def health_api():
    return PlainTextResponse("OK from /api/health")
