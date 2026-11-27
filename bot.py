import asyncio
import json
import os
import shutil
from pathlib import Path

import edm
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


def tracks(uid: int) -> list[Path]:
    return sorted((folder(uid) / "in").iterdir())


def load(uid: int) -> dict:
    p = folder(uid) / "settings.json"
    return json.loads(p.read_text()) if p.exists() else {}


def save(uid: int, s: dict):
    (folder(uid) / "settings.json").write_text(json.dumps(s))


def describe(uid: int) -> str:
    s, lines = load(uid), []
    for i, f in enumerate(tracks(uid), 1):
        c = s.get(f.name, {})
        lines.append(f"{i}. {f.name} | vol {c.get('vol', 1.0)} | "
                     f"offset {c.get('offset', 0)}s | "
                     f"denoise {'on' if c.get('denoise') else 'off'}")
    return "\n".join(lines) or "No files yet."


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await update.message.reply_text(
        "Send me your audio files (vocals, music, effects).\n\n"
        "/tracks - list tracks and their settings\n"
        "/volume N X - track N volume (1 = normal)\n"
        "/offset N S - delay track N by S seconds\n"
        "/enhance [N|off] - noise reduction (all tracks if no N)\n"
        "/edm [bpm] [bars] - generate an original EDM track\n"
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
    files, s = tracks(uid), load(uid)
    if not files:
        return False, "No files yet. Send some audio first."
    cmd, parts, labels = [], [], ""
    for i, f in enumerate(files):
        cmd += ["-i", str(f)]
        c = s.get(f.name, {})
        chain = ["aresample=44100", "aformat=channel_layouts=stereo"]
        if c.get("denoise"):
            chain += ["highpass=f=80", "afftdn=nr=12"]
        off = int(c.get("offset", 0) * 1000)
        if off > 0:
            chain.append(f"adelay={off}:all=1")
        chain.append(f"volume={c.get('vol', 1.0)}")
        parts.append(f"[{i}:a]{','.join(chain)}[a{i}]")
        labels += f"[a{i}]"
    parts.append(f"{labels}amix=inputs={len(files)}:duration=longest:"
                 "normalize=0,alimiter=limit=0.95[out]")
    code, err = await ffmpeg(*cmd, "-filter_complex", ";".join(parts),
                             "-map", "[out]", str(base / "mix.wav"))
    return code == 0, err


async def do_normalize(uid: int) -> tuple[bool, str]:
    base = folder(uid)
    if not (base / "mix.wav").exists():
        return False, "Run /mix first."
    code, err = await ffmpeg("-i", str(base / "mix.wav"), "-af",
                             "loudnorm=I=-14:TP=-1.5:LRA=11",
                             str(base / "final.wav"))
    return code == 0, err


async def tracks_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if allowed(update):
        await update.message.reply_text(describe(update.effective_user.id))


async def set_value(update, ctx, key, lo, hi, usage):
    if not allowed(update):
        return
    uid = update.effective_user.id
    try:
        n, val = int(ctx.args[0]), float(ctx.args[1])
        f = tracks(uid)[n - 1]
        assert n >= 1 and lo <= val <= hi
    except Exception:
        await update.message.reply_text(usage)
        return
    s = load(uid)
    s.setdefault(f.name, {})[key] = val
    save(uid, s)
    await update.message.reply_text(f"Track {n}: {key} = {val}")


async def volume(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await set_value(update, ctx, "vol", 0, 5,
                    "Usage: /volume <track#> <0-5>\nExample: /volume 2 0.5")


async def offset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await set_value(update, ctx, "offset", 0, 600,
                    "Usage: /offset <track#> <seconds>\n"
                    "Example: /offset 2 1.5 (starts track 2 1.5s later)")


async def enhance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    uid = update.effective_user.id
    files, s = tracks(uid), load(uid)
    if not files:
        await update.message.reply_text("No files yet.")
        return
    args = ctx.args
    on = not (args and args[0].lower() == "off")
    try:
        nums = [int(a) for a in args if a.isdigit()]
        targets = [files[n - 1] for n in nums] if nums else files
    except IndexError:
        await update.message.reply_text("That track number doesn't exist.")
        return
    for f in targets:
        s.setdefault(f.name, {})["denoise"] = on
    save(uid, s)
    await update.message.reply_text(
        f"Noise reduction {'on' if on else 'off'} for "
        f"{len(targets)} track(s). Applied on /mix or /generate.")


async def edm_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    uid = update.effective_user.id
    inp = folder(uid) / "in"
    if ctx.args and ctx.args[0].lower() == "off":
        for old in inp.glob("*_edm.wav"):
            old.unlink()
        await update.message.reply_text("Generated EDM track removed.")
        return
    try:
        bpm = int(ctx.args[0]) if ctx.args else 128
        bars = int(ctx.args[1]) if len(ctx.args) > 1 else 32
        assert 90 <= bpm <= 180 and 4 <= bars <= 96
    except Exception:
        await update.message.reply_text(
            "Usage: /edm [bpm 90-180] [bars 4-96]\n"
            "Example: /edm 128 64 (about 2 minutes)\n"
            "Length in seconds = bars x 240 / bpm\n"
            "/edm off removes the generated track.")
        return
    for old in inp.glob("*_edm.wav"):
        old.unlink()
    name = f"{len(list(inp.iterdir())) + 1:02d}_edm.wav"
    await update.message.reply_text("Composing an original track...")
    secs = await asyncio.to_thread(edm.generate, bpm, bars, inp / name)
    await update.message.reply_text(
        f"Done: {name} ({int(secs)}s at {bpm} BPM).\n"
        "It's added like any other track. Lower it with /volume, "
        "e.g. /volume N 0.6, then /generate.")


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
    text = describe(update.effective_user.id)
    text += f"\nMix: {'yes' if (base / 'mix.wav').exists() else 'no'}"
    await update.message.reply_text(text)


async def reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    shutil.rmtree(DATA / str(update.effective_user.id), ignore_errors=True)
    await update.message.reply_text("All files cleared.")


def main():
    app = ApplicationBuilder().token(TOKEN).build()
    for name, fn in [("start", start), ("tracks", tracks_cmd), ("volume", volume),
                     ("offset", offset), ("enhance", enhance), ("edm", edm_cmd), ("mix", mix), ("normalize", normalize),
                     ("generate", generate), ("status", status),
                     ("reset", reset)]:
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(MessageHandler(
        filters.AUDIO | filters.VOICE | filters.Document.ALL, receive))
    app.run_polling()


if __name__ == "__main__":
    main()
