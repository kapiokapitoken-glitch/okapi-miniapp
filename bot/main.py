# bot/main.py
import os
import logging
from typing import List, Dict

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse

from itsdangerous import TimestampSigner, BadSignature

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ---------- Env ----------
BOT_TOKEN = os.environ["BOT_TOKEN"]
GAME_SHORT_NAME = os.environ["GAME_SHORT_NAME"]
PUBLIC_GAME_URL = os.environ["PUBLIC_GAME_URL"].rstrip("/") + "/"
SECRET = os.environ.get("SECRET", "change-me")

# ---------- Logging ----------
logging.basicConfig(
    format="%(asctime)s %(levelname)s:%(name)s:%(message)s", level=logging.INFO
)
log = logging.getLogger("kapi-run")

# ---------- Signer ----------
signer = TimestampSigner(SECRET)

# ---------- FastAPI ----------
app = FastAPI(title="KAPI RUN Bot API")

# ---------- Telegram Application ----------
tg_app: Application = ApplicationBuilder().token(BOT_TOKEN).build()


# -------------------- PTB Handlers --------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the Telegram Game message."""
    await context.bot.send_game(
        chat_id=update.effective_chat.id, game_short_name=GAME_SHORT_NAME
    )


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("pong 🏓")


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User taps the Play button on the game message."""
    cq = update.callback_query
    if cq and cq.game_short_name == GAME_SHORT_NAME:
        user_id = cq.from_user.id
        chat_id = cq.message.chat.id if cq.message else 0
        message_id = cq.message.message_id if cq.message else 0

        payload = f"{user_id}:{chat_id}:{message_id}"
        token = signer.sign(payload).decode()

        # Oyunun URL’sine parametreleri ekle
        url = f"{PUBLIC_GAME_URL}#u={user_id}&c={chat_id}&m={message_id}&t={token}"

        await cq.answer(url=url)
    else:
        # Farklı bir oyun adına tıklandıysa
        await cq.answer(text="Unknown game.", show_alert=True)


# Handlers register
tg_app.add_handler(CommandHandler(["start", "play"], cmd_start))
tg_app.add_handler(CommandHandler("ping", cmd_ping))
tg_app.add_handler(CallbackQueryHandler(on_callback))

# ----- PTB initialize on startup (fixes 'Application.initialize' error) -----
@app.on_event("startup")
async def _startup() -> None:
    # PTB internal init (does NOT start polling)
    await tg_app.initialize()
    log.info("Telegram Application initialized.")

@app.on_event("shutdown")
async def _shutdown() -> None:
    # graceful shutdown
    await tg_app.shutdown()
    await tg_app.stop()


# -------------------- Database (SQLAlchemy 2.x) --------------------
from sqlalchemy import create_engine, Integer, String, Column, select, func
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.environ["DATABASE_URL"]

# DigitalOcean PG genelde sslmode=require ister; SQLAlchemy string'inde sorun yoksa direkt çalışır.
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


class Score(Base):
    __tablename__ = "scores_total"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, unique=True, index=True, nullable=False)
    username = Column(String(255), index=True, nullable=False)
    score = Column(Integer, default=0, nullable=False)


# tabloyu oluştur
Base.metadata.create_all(bind=engine)


# -------------------- Health --------------------
@app.get("/health")
def health_root() -> PlainTextResponse:
    return PlainTextResponse("OK")


@app.get("/api/health")
def health_api() -> PlainTextResponse:
    return PlainTextResponse("OK from /api/health")


@app.get("/bot/check")
def bot_check() -> PlainTextResponse:
    return PlainTextResponse("bot route ok")


# -------------------- Telegram Webhook --------------------
@app.post("/bot/webhook")
async def tg_webhook(request: Request) -> JSONResponse:
    """Telegram'ın POST ettiği Update'leri PTB'ye iletir."""
    data = await request.json()
    update = Update.de_json(data, tg_app.bot)

    # PTB v21: initialize() zaten startup'ta çağrıldı
    await tg_app.process_update(update)
    return JSONResponse({"ok": True})


# -------------------- Score API --------------------
# Not: DB işlemlerini senkron yapıyoruz; FastAPI bunları threadpool’da çalıştırır.


@app.post("/api/score")
async def post_score(request: Request) -> JSONResponse:
    """
    Oyundan skor gönderimi.
    Body: { user_id, chat_id, message_id, token, score, username? }
    """
    body = await request.json()
    try:
        user_id = int(body["user_id"])
        chat_id = int(body["chat_id"])
        message_id = int(body["message_id"])
        score_inc = int(body["score"])
        token = body["token"]
        username = str(body.get("username") or user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Bad payload")

    # Token doğrulama
    try:
        payload = signer.unsign(token, max_age=1800).decode()
    except BadSignature:
        raise HTTPException(status_code=403, detail="Bad token")

    if payload != f"{user_id}:{chat_id}:{message_id}":
        raise HTTPException(status_code=403, detail="Token mismatch")

    # DB: toplam skoru artır
    db = SessionLocal()
    try:
        row = db.execute(select(Score).where(Score.user_id == user_id)).scalar_one_or_none()
        if row:
            row.score = (row.score or 0) + max(0, score_inc)
            row.username = username
        else:
            row = Score(user_id=user_id, username=username, score=max(0, score_inc))
            db.add(row)
        db.commit()
        db.refresh(row)
        total = row.score
    finally:
        db.close()

    # Telegram Game skorunu da güncelle (toplam skor)
    await tg_app.bot.set_game_score(
        user_id=user_id,
        score=total,
        chat_id=chat_id,
        message_id=message_id,
        force=False,
        disable_edit_message=False,
    )

    return JSONResponse({"ok": True, "total_score": total})


@app.get("/api/leaderboard")
def leaderboard() -> List[Dict]:
    """En yüksek skora göre ilk 10 kullanıcıyı döndürür."""
    db = SessionLocal()
    try:
        rows = db.execute(
            select(Score.username, Score.score).order_by(Score.score.desc()).limit(10)
        ).all()
        return [{"username": u, "score": s} for (u, s) in rows]
    finally:
        db.close()
