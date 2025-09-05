import os
import ssl
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from itsdangerous import TimestampSigner, BadSignature
from telegram import Update
from telegram.ext import Application, ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String, BigInteger, select, desc

# --- Config ---
BOT_TOKEN = os.environ["BOT_TOKEN"]
GAME_SHORT_NAME = os.environ["GAME_SHORT_NAME"]
PUBLIC_GAME_URL = os.environ["PUBLIC_GAME_URL"].rstrip("/") + "/"
SECRET = os.environ.get("SECRET", "change-me")
DATABASE_URL = os.environ["DATABASE_URL"]

# --- DB setup ---
Base = declarative_base()

class Score(Base):
    __tablename__ = "scores"
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, unique=True, index=True)
    username = Column(String, nullable=True)
    score = Column(Integer, default=0)

ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE
engine = create_async_engine(DATABASE_URL, connect_args={"ssl": ssl_context})
SessionLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# --- FastAPI & Telegram ---
signer = TimestampSigner(SECRET)
app = FastAPI()

tg_app: Application = ApplicationBuilder().token(BOT_TOKEN).build()

# --- Handlers ---
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_game(chat_id=update.effective_chat.id, game_short_name=GAME_SHORT_NAME)

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

async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("pong ✅")

tg_app.add_handler(CommandHandler("ping", cmd_ping))

async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Liderlik tablosunu (200 kişi) Telegram'da gösterir"""
    async with SessionLocal() as session:
        result = await session.execute(
            select(Score).order_by(desc(Score.score)).limit(200)
        )
        rows = result.scalars().all()

    if not rows:
        await update.message.reply_text("Henüz kimse skor kaydetmedi.")
        return

    lines = []
    for i, row in enumerate(rows, start=1):
        name = row.username or str(row.user_id)
        lines.append(f"{i}. {name} — {row.score}")

    text = "🏆 *Leaderboard (Top 200)* 🏆\n\n" + "\n".join(lines)
    await update.message.reply_text(text, parse_mode="Markdown")

tg_app.add_handler(CommandHandler("top", cmd_top))

# --- Webhook ---
@app.post("/bot/webhook")
async def tg_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return JSONResponse({"ok": True})

# --- API: Score ---
@app.post("/api/score")
async def post_score(request: Request):
    body = await request.json()
    try:
        user_id = int(body["user_id"])
        score = int(body["score"])
        chat_id = int(body["chat_id"])
        message_id = int(body["message_id"])
        token = body["token"]
        username = body.get("username")
    except Exception:
        raise HTTPException(status_code=400, detail="Bad payload")

    try:
        payload = signer.unsign(token, max_age=1800).decode()
    except BadSignature:
        raise HTTPException(status_code=403, detail="Bad token")

    if payload != f"{user_id}:{chat_id}:{message_id}":
        raise HTTPException(status_code=403, detail="Token mismatch")

    async with SessionLocal() as session:
        obj = await session.get(Score, {"user_id": user_id})
        if not obj:
            obj = Score(user_id=user_id, username=username, score=score)
            session.add(obj)
        else:
            obj.username = username or obj.username
            obj.score = obj.score + score if score > 0 else obj.score
        await session.commit()

    await tg_app.bot.set_game_score(
        user_id=user_id, score=score,
        chat_id=chat_id, message_id=message_id,
        force=False, disable_edit_message=False
    )
    return JSONResponse({"ok": True})

# --- API: Leaderboard ---
@app.get("/api/leaderboard")
async def get_leaderboard(limit: int = 200):
    async with SessionLocal() as session:
        result = await session.execute(
            select(Score).order_by(desc(Score.score)).limit(limit)
        )
        rows = result.scalars().all()

    return [
        {"user_id": r.user_id, "username": r.username, "score": r.score}
        for r in rows
    ]

# --- Health ---
@app.get("/health")
def health_root():
    return PlainTextResponse("OK")

@app.get("/api/health")
def health_api():
    return PlainTextResponse("OK from /api/health")
