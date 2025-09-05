import os
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from itsdangerous import TimestampSigner, BadSignature

from telegram import Update
from telegram.ext import (
    Application, ApplicationBuilder,
    CommandHandler, CallbackQueryHandler, ContextTypes,
)

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

# --------------------
# ENV & globals
# --------------------
BOT_TOKEN = os.environ["BOT_TOKEN"]
GAME_SHORT_NAME = os.environ["GAME_SHORT_NAME"]          # BotFather'da verdiğin kısa ad ile birebir aynı
PUBLIC_GAME_URL = os.environ["PUBLIC_GAME_URL"].rstrip("/") + "/"  # oyunun kök URL'i (index.html burada)
DATABASE_URL = os.environ["DATABASE_URL"]               # postgresql+asyncpg://...?sslmode=require
SECRET = os.environ.get("SECRET", "change-me")

signer = TimestampSigner(SECRET)

# DB (async)
engine = create_async_engine(DATABASE_URL, pool_pre_ping=True, future=True)

# FastAPI
app = FastAPI()

# Telegram Application (PTB v21)
tg_app: Application = ApplicationBuilder().token(BOT_TOKEN).build()


# --------------------
# Telegram handlers
# --------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Oyun kartını gönder
    await context.bot.send_game(
        chat_id=update.effective_chat.id,
        game_short_name=GAME_SHORT_NAME
    )

async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("pong 🏓")

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Oyun butonuna tıklandığında Telegram 'callback_query' gönderir.
    Burada doğrulayıcı token içeren URL'yi döndürürüz.
    """
    cq = update.callback_query
    if cq and cq.game_short_name == GAME_SHORT_NAME:
        user_id = cq.from_user.id
        chat_id = cq.message.chat.id if cq.message else None
        message_id = cq.message.message_id if cq.message else None

        payload = f"{user_id}:{chat_id}:{message_id}"
        token = signer.sign(payload).decode()

        # Oyun URL'sine Telegram parametrelerini fragment ile ekliyoruz
        url = f"{PUBLIC_GAME_URL}#u={user_id}&c={chat_id}&m={message_id}&t={token}"
        await cq.answer(url=url)
    else:
        await cq.answer(text="Unknown game.", show_alert=True)

# Handler kayıtları
tg_app.add_handler(CommandHandler(["start", "play"], cmd_start))
tg_app.add_handler(CommandHandler("ping", cmd_ping))
tg_app.add_handler(CallbackQueryHandler(on_callback))


# --------------------
# App lifecycle (CRITICAL for PTB v21)
# --------------------
@app.on_event("startup")
async def on_startup():
    # Telegram botu başlat
    await tg_app.initialize()
    await tg_app.start()

    # DB tablo oluştur (yoksa)
    create_sql = """
    CREATE TABLE IF NOT EXISTS scores (
        user_id BIGINT PRIMARY KEY,
        username TEXT,
        telegram_username TEXT,
        score INTEGER NOT NULL DEFAULT 0
    );
    """
    async with engine.begin() as conn:
        await conn.execute(text(create_sql))

@app.on_event("shutdown")
async def on_shutdown():
    await tg_app.stop()
    await tg_app.shutdown()


# --------------------
# Webhook endpoint
# --------------------
@app.post("/bot/webhook")
async def tg_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return JSONResponse({"ok": True})


# Basit check
@app.get("/bot/check")
def bot_check():
    return PlainTextResponse("ok")


# --------------------
# Score & leaderboard API
# --------------------
@app.post("/api/score")
async def post_score(request: Request):
    """
    Body:
    {
      "user_id": 123,
      "chat_id": 456,
      "message_id": 789,
      "token": "...",
      "score": 50,                       # bu oynanışta kazanılan skor (artış)
      "username": "Kapi",                # (opsiyonel) oyun içi görünen ad
      "telegram_username": "kapi_official"  # (opsiyonel) @handle
    }
    """
    body = await request.json()
    try:
        user_id = int(body["user_id"])
        chat_id = int(body["chat_id"])
        message_id = int(body["message_id"])
        score_inc = int(body["score"])
        token = body["token"]
        username = str(body.get("username") or "")
        tg_uname = str(body.get("telegram_username") or "")
    except Exception:
        raise HTTPException(status_code=400, detail="Bad payload")

    # Token doğrulama
    try:
        payload = signer.unsign(token, max_age=1800).decode()
    except BadSignature:
        raise HTTPException(status_code=403, detail="Bad token")

    if payload != f"{user_id}:{chat_id}:{message_id}":
        raise HTTPException(status_code=403, detail="Token mismatch")

    # Kümülatif skor: upsert + RETURNING
    upsert_sql = """
    INSERT INTO scores (user_id, username, telegram_username, score)
    VALUES (:uid, :uname, :tg, :inc)
    ON CONFLICT (user_id)
    DO UPDATE SET
        username = COALESCE(NULLIF(:uname, ''), scores.username),
        telegram_username = COALESCE(NULLIF(:tg, ''), scores.telegram_username),
        score = scores.score + EXCLUDED.score
    RETURNING score;
    """
    async with engine.begin() as conn:
        result = await conn.execute(
            text(upsert_sql),
            {"uid": user_id, "uname": username, "tg": tg_uname, "inc": max(0, score_inc)}
        )
        row = result.first()
        total_score = int(row[0]) if row else 0

    # Telegram Game skorunu toplam skorla güncelle
    await tg_app.bot.set_game_score(
        user_id=user_id,
        score=total_score,
        chat_id=chat_id,
        message_id=message_id,
        force=False,
        disable_edit_message=False
    )

    return JSONResponse({"ok": True, "total_score": total_score})


@app.get("/api/leaderboard")
async def leaderboard():
    sql = """
    SELECT
        COALESCE(NULLIF(username,''), '@' || COALESCE(NULLIF(telegram_username,''), 'anon')) AS name,
        score
    FROM scores
    ORDER BY score DESC
    LIMIT 10;
    """
    async with engine.begin() as conn:
        result = await conn.execute(text(sql))
        rows = result.fetchall()

    return {"leaders": [{"username": r[0], "score": int(r[1])} for r in rows]}


# --------------------
# Health
# --------------------
@app.get("/health")
def health_root():
    return PlainTextResponse("OK")

@app.get("/api/health")
def health_api():
    return PlainTextResponse("OK from /api/health")
