"""Audiko — transforme un ebook en livre audio MP3 (un fichier par chapitre + fichier complet)."""
import asyncio
import os
import re
import shutil
import time
import unicodedata
import uuid
from pathlib import Path

import edge_tts
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel

from extract import extract_chapters
from normalize import normalize

ROOT = Path(os.environ.get("AUDIKO_DATA", "/tmp/audiko"))
ROOT.mkdir(parents=True, exist_ok=True)
STATIC = Path(__file__).parent / "static"
MAX_UPLOAD = 60 * 1024 * 1024
JOB_TTL = 24 * 3600
CHUNK = 6000          # caractères envoyés à la voix d'un coup
PARALLEL = 3          # chapitres convertis en même temps

app = FastAPI(title="Audiko")
JOBS: dict[str, dict] = {}

FALLBACK_VOICES = [
    {"id": "fr-FR-HenriNeural", "name": "Henri", "gender": "Homme", "region": "France"},
    {"id": "fr-FR-DeniseNeural", "name": "Denise", "gender": "Femme", "region": "France"},
    {"id": "fr-FR-RemyMultilingualNeural", "name": "Rémy (multilingue, moins stable)", "gender": "Homme", "region": "France"},
    {"id": "fr-FR-VivienneMultilingualNeural", "name": "Vivienne (multilingue, moins stable)", "gender": "Femme", "region": "France"},
    {"id": "fr-FR-EloiseNeural", "name": "Éloïse", "gender": "Femme", "region": "France"},
    {"id": "fr-CA-AntoineNeural", "name": "Antoine", "gender": "Homme", "region": "Canada"},
    {"id": "fr-CA-SylvieNeural", "name": "Sylvie", "gender": "Femme", "region": "Canada"},
    {"id": "fr-CA-JeanNeural", "name": "Jean", "gender": "Homme", "region": "Canada"},
    {"id": "fr-CA-ThierryNeural", "name": "Thierry", "gender": "Homme", "region": "Canada"},
    {"id": "fr-BE-GerardNeural", "name": "Gérard", "gender": "Homme", "region": "Belgique"},
    {"id": "fr-BE-CharlineNeural", "name": "Charline", "gender": "Femme", "region": "Belgique"},
    {"id": "fr-CH-FabriceNeural", "name": "Fabrice", "gender": "Homme", "region": "Suisse"},
    {"id": "fr-CH-ArianeNeural", "name": "Ariane", "gender": "Femme", "region": "Suisse"},
]
REGIONS = {"FR": "France", "CA": "Canada", "BE": "Belgique", "CH": "Suisse"}
_voices_cache: list | None = None


def slug(s: str, n=60):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
    return (s[:n] or "audio").strip("-")


def split_text(text: str, size=CHUNK):
    """Découpe en morceaux à la fin d'une phrase."""
    parts, cur = [], ""
    for para in text.split("\n\n"):
        sentences = re.split(r"(?<=[.!?…»])\s+", para.strip())
        for s in sentences:
            if not s:
                continue
            while len(s) > size:  # phrase énorme sans ponctuation
                cut = s.rfind(" ", 0, size)
                cut = cut if cut > 0 else size
                if cur:
                    parts.append(cur)
                    cur = ""
                parts.append(s[:cut])
                s = s[cut:].strip()
            if len(cur) + len(s) + 1 > size and cur:
                parts.append(cur)
                cur = ""
            cur += (" " if cur else "") + s
        if cur and not cur.endswith("\n"):
            cur += "\n"
    if cur.strip():
        parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


async def tts_to_bytes(text: str, voice: str, rate: str, pitch: str) -> bytes:
    last = None
    for attempt in range(5):
        try:
            com = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
            buf = bytearray()
            async for chunk in com.stream():
                if chunk["type"] == "audio":
                    buf.extend(chunk["data"])
            if buf:
                return bytes(buf)
            last = RuntimeError("audio vide")
        except Exception as e:  # réseau, limite temporaire…
            last = e
        await asyncio.sleep(2 + attempt * 3)
    raise RuntimeError(f"La voix n'a pas répondu ({last})")


def cleanup_old():
    now = time.time()
    for jid, job in list(JOBS.items()):
        if now - job["created"] > JOB_TTL and job["status"] != "running":
            shutil.rmtree(ROOT / jid, ignore_errors=True)
            JOBS.pop(jid, None)


def public(job):
    return {
        "id": job["id"],
        "filename": job["filename"],
        "book": job["book"],
        "status": job["status"],
        "error": job.get("error"),
        "voice": job.get("voice"),
        "chapters": [
            {k: c[k] for k in ("index", "title", "chars", "status", "progress", "error", "selected", "size")}
            for c in job["chapters"]
        ],
    }


# ------------------------------------------------------------------ pages

@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"))


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/voices")
async def voices():
    global _voices_cache
    if _voices_cache is None:
        try:
            vs = await asyncio.wait_for(edge_tts.list_voices(), timeout=10)
            out = []
            for v in vs:
                if v["Locale"].startswith("fr-"):
                    short = v["ShortName"]
                    multi = "Multilingual" in short
                    name = short.split("-")[2].replace("Neural", "").replace("Multilingual", "")
                    if multi:
                        name += " (multilingue, moins stable)"
                    out.append({
                        "id": short, "name": name,
                        "gender": "Femme" if v["Gender"] == "Female" else "Homme",
                        "region": REGIONS.get(v["Locale"].split("-")[1], v["Locale"]),
                    })
            order = {"France": 0, "Belgique": 1, "Suisse": 2, "Canada": 3}
            out.sort(key=lambda x: (order.get(x["region"], 9), "multilingue" in x["name"], x["gender"], x["name"]))
            _voices_cache = out or FALLBACK_VOICES
        except Exception:
            return FALLBACK_VOICES
    return _voices_cache


class PreviewReq(BaseModel):
    voice: str
    rate: int = 0
    pitch: int = 0
    text: str | None = None


def fmt_rate(r: int):
    r = max(-50, min(100, int(r)))
    return f"{'+' if r >= 0 else ''}{r}%"


def fmt_pitch(p: int):
    p = max(-30, min(30, int(p)))
    return f"{'+' if p >= 0 else ''}{p}Hz"


@app.post("/api/preview")
async def preview(req: PreviewReq):
    text = (req.text or "Bonjour, je suis la voix qui va lire ton livre. Installe-toi confortablement, "
            "et laisse-toi porter par l'histoire.")[:400]
    try:
        audio = await tts_to_bytes(text, req.voice, fmt_rate(req.rate), fmt_pitch(req.pitch))
    except Exception as e:
        raise HTTPException(502, str(e))
    return Response(audio, media_type="audio/mpeg")


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    cleanup_old()
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Fichier trop lourd (60 Mo maximum).")
    name = file.filename or "livre"
    try:
        chapters = await asyncio.to_thread(extract_chapters, name, data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Impossible de lire ce fichier ({type(e).__name__}). Essaie de l'exporter en PDF ou en .docx.")
    if not chapters:
        raise HTTPException(400, "Aucun texte lisible trouvé dans ce fichier.")
    jid = uuid.uuid4().hex[:12]
    (ROOT / jid).mkdir(parents=True, exist_ok=True)
    book = re.sub(r"\.[A-Za-z0-9]+$", "", name).replace("_", " ").strip() or "Livre audio"
    job = {
        "id": jid, "filename": name, "book": book, "status": "ready", "created": time.time(),
        "chapters": [
            {"index": i, "title": c["title"], "text": c["text"], "chars": len(c["text"]),
             "status": "pending", "progress": 0, "error": None, "selected": c.get("selected", True), "size": 0}
            for i, c in enumerate(chapters)
        ],
    }
    JOBS[jid] = job
    return public(job)


@app.get("/api/job/{jid}/chapter/{idx}/text")
def chapter_text(jid: str, idx: int):
    job = JOBS.get(jid) or _404()
    return {"title": job["chapters"][idx]["title"], "text": normalize(job["chapters"][idx]["text"])}


class ChapterEdit(BaseModel):
    index: int
    title: str
    selected: bool = True


class ConvertReq(BaseModel):
    voice: str
    rate: int = 0
    pitch: int = 0
    read_titles: bool = True
    book: str | None = None
    chapters: list[ChapterEdit] = []


def _404():
    raise HTTPException(404, "Session introuvable ou expirée. Recharge la page et redépose ton livre.")


@app.post("/api/job/{jid}/convert")
async def convert(jid: str, req: ConvertReq):
    job = JOBS.get(jid) or _404()
    if job["status"] == "running":
        raise HTTPException(409, "Conversion déjà en cours.")
    edits = {c.index: c for c in req.chapters}
    for c in job["chapters"]:
        e = edits.get(c["index"])
        if e:
            c["title"] = e.title.strip()[:120] or c["title"]
            c["selected"] = e.selected
        if c["selected"] and c["status"] != "done":
            c["status"], c["progress"], c["error"] = "pending", 0, None
    if req.book:
        job["book"] = req.book.strip()[:120]
    job.update(status="running", voice=req.voice, rate=fmt_rate(req.rate), pitch=fmt_pitch(req.pitch),
               read_titles=req.read_titles, error=None)
    # si les réglages de voix changent, on refait tout
    sig = (req.voice, job["rate"], job["pitch"], req.read_titles)
    if job.get("sig") and job["sig"] != sig:
        for c in job["chapters"]:
            c["status"], c["progress"], c["size"] = "pending", 0, 0
    job["sig"] = sig
    job["task"] = asyncio.create_task(run_job(job))
    return public(job)


async def run_chapter(job, c, sem):
    async with sem:
        c["status"], c["progress"] = "running", 0
        text = normalize(c["text"])
        if job["read_titles"] and not c["title"].startswith("Début du livre") and \
                not text.lower().startswith(normalize(c["title"]).lower()[:20].rstrip(".")):
            text = f"{normalize(c['title'])}\n\n{text}"
        parts = split_text(text)
        out = ROOT / job["id"] / f"{c['index']:03d}.mp3"
        tmp = out.with_suffix(".part")
        try:
            with open(tmp, "wb") as f:
                for n, p in enumerate(parts):
                    f.write(await tts_to_bytes(p, job["voice"], job["rate"], job["pitch"]))
                    c["progress"] = round((n + 1) / len(parts) * 100)
            tmp.replace(out)
            c["status"], c["size"] = "done", out.stat().st_size
        except Exception as e:
            c["status"], c["error"] = "error", str(e)[:300]
            tmp.unlink(missing_ok=True)


async def run_job(job):
    sem = asyncio.Semaphore(PARALLEL)
    todo = [c for c in job["chapters"] if c["selected"] and c["status"] != "done"]
    await asyncio.gather(*(run_chapter(job, c, sem) for c in todo))
    errs = [c for c in job["chapters"] if c["selected"] and c["status"] == "error"]
    job["status"] = "partial" if errs else "done"
    job["created"] = time.time()


@app.get("/api/job/{jid}")
def job_status(jid: str):
    return public(JOBS.get(jid) or _404())


@app.get("/api/job/{jid}/chapter/{idx}.mp3")
def chapter_mp3(jid: str, idx: int, dl: int = 0):
    job = JOBS.get(jid) or _404()
    path = ROOT / jid / f"{idx:03d}.mp3"
    if not path.exists():
        raise HTTPException(404, "Ce chapitre n'est pas encore prêt.")
    c = job["chapters"][idx]
    fname = f"{idx + 1:02d} - {slug(c['title'])}.mp3"
    return FileResponse(path, media_type="audio/mpeg", filename=fname if dl else None,
                        content_disposition_type="attachment" if dl else "inline")


@app.get("/api/job/{jid}/full.mp3")
def full_mp3(jid: str):
    """Rassemble tous les chapitres prêts, dans l'ordre, en un seul MP3."""
    job = JOBS.get(jid) or _404()
    files = [ROOT / jid / f"{c['index']:03d}.mp3" for c in job["chapters"]
             if c["selected"] and (ROOT / jid / f"{c['index']:03d}.mp3").exists()]
    if not files:
        raise HTTPException(404, "Aucun chapitre prêt.")
    total = sum(f.stat().st_size for f in files)

    def gen():
        for f in files:
            with open(f, "rb") as fh:
                while True:
                    b = fh.read(1 << 20)
                    if not b:
                        break
                    yield b

    fname = f"{slug(job['book'])} - livre audio complet.mp3"
    return StreamingResponse(gen(), media_type="audio/mpeg", headers={
        "Content-Disposition": f'attachment; filename="{fname}"',
        "Content-Length": str(total),
    })


@app.post("/api/job/{jid}/retry")
async def retry(jid: str):
    job = JOBS.get(jid) or _404()
    if job["status"] == "running":
        return public(job)
    for c in job["chapters"]:
        if c["status"] == "error":
            c["status"], c["error"], c["progress"] = "pending", None, 0
    job["status"] = "running"
    job["task"] = asyncio.create_task(run_job(job))
    return public(job)
