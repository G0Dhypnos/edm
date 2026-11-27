import asyncio
import os
import shutil
from pathlib import Path

from telegram import Update
from telegram.ext import (ApplicationBuilder, CommandHandler,
                          ContextTypes, MessageHandler, filters)

TOKEN = os.environ["BOT_TOKEN"]
# Optional: only this Telegram user ID can use the bot
ALLOWED = os.environ.get("ALLOWED_USER_ID")
DATA = Path("data")


def allowed(update: Update) -> bool:
    return not ALLOWED or str(update.effective_user.id) == ALLOWED


def folder(uid: int) -> Path:
    p = DATA / str(uid)
    (p / "in").mkdir(parents=True, exist_ok=True)
    return p


async def ffmpeg(*args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args,
        stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    return proc.returncode, err.decode()[-500:]


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await update.message.reply_text(
        "Send me your audio files (vocals, music, effects).\n\n"
        "/mix - combine all tracks\n"
        "/normalize - balance loudness of the mix\n"
        "/generate - mix + normalize + send MP3\n"
        "/status - see uploaded files\n"
        "/reset - delete all uploaded files")


async def receive(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    m = update.message
    f = m.audio or m.voice or m.document
    if f is None:
        return
    inp = folder(update.effective_user.id) / "in"
    name = getattr(f, "file_name", None) or f"{f.file_unique_id}.ogg"
    name = f"{len(list(inp.iterdir())) + 1:02d}_{Path(name).name}"
    tg_file = await f.get_file()
    await tg_file.download_to_drive(inp / name)
    await m.reply_text(f"Saved: {name}\nSend more, or use /generate.")


async def do_mix(uid: int) -> tuple[bool, str]:
    base = folder(uid)
    files = sorted((base / "in").iterdir())
    if not files:
        return False, "No files yet. Send some audio first."
    cmd = []
    for f in files:
        cmd += ["-i", str(f)]
    flt = (f"amix=inputs={len(files)}:duration=longest:normalize=0,"
           "alimiter=limit=0.95")
    code, err = await ffmpeg(*cmd, "-filter_complex", flt,
                             str(base / "mix.wav"))
    return code == 0, err


async def do_normalize(uid: int) -> tuple[bool, str]:
    base = folder(uid)
    if not (base / "mix.wav").exists():
        return False, "Run /mix first."
    code, err = await ffmpeg("-i", str(base / "mix.wav"), "-af",
                             "loudnorm=I=-14:TP=-1.5:LRA=11",
                             str(base / "final.wav"))
    return code == 0, err


async def mix(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await update.message.reply_text("Mixing...")
    ok, err = await do_mix(update.effective_user.id)
    await update.message.reply_text("Mix done. Use /normalize or /generate."
                                    if ok else f"Failed: {err}")


async def normalize(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await update.message.reply_text("Normalizing...")
    ok, err = await do_normalize(update.effective_user.id)
    await update.message.reply_text("Normalized. Use /generate to export."
                                    if ok else f"Failed: {err}")


async def generate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    uid = update.effective_user.id
    await update.message.reply_text("Working on your song...")
    for step in (do_mix, do_normalize):
        ok, err = await step(uid)
        if not ok:
            await update.message.reply_text(f"Failed: {err}")
            return
    base = folder(uid)
    code, err = await ffmpeg("-i", str(base / "final.wav"),
                             "-codec:a", "libmp3lame", "-q:a", "2",
                             str(base / "final_song.mp3"))
    if code != 0:
        await update.message.reply_text(f"Export failed: {err}")
        return
    await update.message.reply_text("Your song has been generated!")
    with open(base / "final_song.mp3", "rb") as fh:
        await update.message.reply_document(fh, filename="final_song.mp3")


async def status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    base = folder(update.effective_user.id)
    files = sorted(p.name for p in (base / "in").iterdir())
    text = f"{len(files)} file(s) uploaded:\n" + "\n".join(files)
    text += f"\nMix: {'yes' if (base / 'mix.wav').exists() else 'no'}"
    await update.message.reply_text(text)


async def reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    shutil.rmtree(DATA / str(update.effective_user.id), ignore_errors=True)
    await update.message.reply_text("All files cleared.")


def main():
    app = ApplicationBuilder().token(TOKEN).build()
    for name, fn in [("start", start), ("mix", mix), ("normalize", normalize),
                     ("generate", generate), ("status", status),
                     ("reset", reset)]:
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(MessageHandler(
        filters.AUDIO | filters.VOICE | filters.Document.ALL, receive))
    app.run_polling()


if __name__ == "__main__":
    main()
