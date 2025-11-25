# app.py
import os
import time
import uuid
import asyncio
import subprocess
import shutil
from typing import Dict, Any, List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from faster_whisper import WhisperModel
import yt_dlp

# IMPORT utilities (templating helpers etc.)
from utils.video_tools import uuid_name  # small helper used below

# -----------------------
# CONFIG / PATHS
# -----------------------
DOWNLOADS_DIR = "downloads"
CLIPS_DIR = "clips"
TRANS_DIR = "transcripts"
TEMPLATES_DIR = "templates"
SESSIONS_DIR = "sessions"
STATIC_DIR = "static"
FONTS_DIR = "fonts"
DEFAULT_EMOJI_FONT = os.path.join(FONTS_DIR, "NotoColorEmoji-Regular.ttf")

os.makedirs(DOWNLOADS_DIR, exist_ok=True)
os.makedirs(CLIPS_DIR, exist_ok=True)
os.makedirs(TRANS_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)
os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(FONTS_DIR, exist_ok=True)

# Use the uploaded logo path from your session (will be transformed to a served URL).
LOGO_PATH = r'/mnt/data/A_logo_for_"Tamil_Scoop"_is_set_against_a_textured.png'

# -----------------------
# APP + QUEUE + JOBS
# -----------------------
app = FastAPI(title="TamilScoop - full pipeline (faster-whisper)")

app.mount("/static", StaticFiles(directory="static"), name="static")

job_queue: asyncio.Queue = asyncio.Queue()
jobs: Dict[str, Dict[str, Any]] = {}  # job_id -> job state

# -----------------------
# WebSocket manager
# -----------------------
class ConnectionManager:
    def __init__(self):
        self.active: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, message: dict):
        living = []
        for ws in list(self.active):
            try:
                await ws.send_json(message)
                living.append(ws)
            except Exception:
                try:
                    await ws.close()
                except Exception:
                    pass
        self.active = living

manager = ConnectionManager()

# -----------------------
# Pydantic models
# -----------------------
class DownloadRequest(BaseModel):
    url: str
    session_id: Optional[str] = None

class TranscribeRequest(BaseModel):
    filename: str
    session_id: Optional[str] = None

class ClipRequest(BaseModel):
    filename: str
    start: float
    end: float
    text: Optional[str] = ""
    session_id: Optional[str] = None
    output_name: Optional[str] = None

class TemplateClipRequest(BaseModel):
    filename: str
    start: float
    end: float
    template_name: Optional[str] = None
    template_json: Optional[dict] = None
    output_name: Optional[str] = None
    session_id: Optional[str] = None

class MergeRequest(BaseModel):
    clips: List[str]
    output_name: str
    session_id: Optional[str] = None

class CleanupRequest(BaseModel):
    session_id: str
    delete_clips: bool = True
    delete_video: bool = True

# -----------------------
# Job helpers
# -----------------------
def vertical_filter():
    # 9:16 center crop then scale to 1080x1920
    return "crop=in_h*9/16:in_h:(in_w-(in_h*9/16))/2:0,scale=1080:1920"

def create_job(kind: str, meta: dict):
    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "id": job_id,
        "kind": kind,
        "meta": meta,
        "status": "queued",
        "progress": 0.0,
        "created_at": time.time(),
        "updated_at": time.time(),
        "result": None,
        "error": None
    }
    return job_id

async def push_job_update(job_id: str):
    state = jobs.get(job_id, {})
    await manager.broadcast({"type": "job_update", "job": state})

# -----------------------
# Whisper model (lazy)
# -----------------------
WHISPER_MODEL = None
async def ensure_model_loaded(model_name: str = "small", device: str = "cpu"):
    global WHISPER_MODEL
    if WHISPER_MODEL is None:
        # compute_type chosen for CPU by default; change to int8_float16 for GPU as needed
        compute_type = "float32" if device == "cpu" else "int8_float16"
        WHISPER_MODEL = WhisperModel(model_name, device=device, compute_type=compute_type)
    return WHISPER_MODEL

# -----------------------
# Worker functions
# -----------------------

# 1) DOWNLOAD using yt-dlp with progress hooks
async def do_download(job_id: str):
    jobs[job_id]["status"] = "running"
    await push_job_update(job_id)
    url = jobs[job_id]["meta"]["url"]
    session_id = jobs[job_id]["meta"].get("session_id")

    outname = f"{int(time.time())}_{uuid.uuid4().hex[:8]}.mp4"
    outpath = os.path.join(DOWNLOADS_DIR, outname)

    ydl_opts = {
        "format": "mp4/best",
        "outtmpl": outpath,
        "noplaylist": True,
        "progress_hooks": []
    }

    def progress(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes", None)  # ignore weird keys
        # fallback safe approach using available keys
        if d.get("status") == "downloading":
            total_bytes = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            prog = float(downloaded) / total_bytes if total_bytes else 0.0
            jobs[job_id]["progress"] = round(prog * 100, 2)
            jobs[job_id]["updated_at"] = time.time()
            # small broadcast
            asyncio.create_task(manager.broadcast({"type":"download_progress","job_id":job_id,"progress":jobs[job_id]["progress"]}))
            asyncio.create_task(push_job_update(job_id))
        elif d.get("status") == "finished":
            jobs[job_id]["progress"] = 100.0
            jobs[job_id]["updated_at"] = time.time()
            asyncio.create_task(push_job_update(job_id))

    ydl_opts["progress_hooks"].append(progress)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        jobs[job_id]["status"] = "finished"
        jobs[job_id]["result"] = {"filename": os.path.basename(outpath), "path": outpath}
        jobs[job_id]["progress"] = 100.0
        jobs[job_id]["updated_at"] = time.time()
        # copy original to session folder if session_id provided
        if session_id:
            session_folder = os.path.join(SESSIONS_DIR, session_id)
            os.makedirs(session_folder, exist_ok=True)
            try:
                shutil.copy(outpath, os.path.join(session_folder, os.path.basename(outpath)))
            except Exception:
                pass
        await push_job_update(job_id)
    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)
        jobs[job_id]["updated_at"] = time.time()
        await push_job_update(job_id)

# 2) TRANSCRIBE using faster-whisper (streams segments)
async def do_transcribe(job_id: str):
    jobs[job_id]["status"] = "running"
    await push_job_update(job_id)
    filename = jobs[job_id]["meta"]["filename"]
    session_id = jobs[job_id]["meta"].get("session_id")
    path = os.path.join(DOWNLOADS_DIR, filename)
    if not os.path.exists(path):
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = "file not found"
        await push_job_update(job_id)
        return

    # detect device
    device = "cpu"
    if os.getenv("USE_CUDA", "0") == "1":
        device = "cuda"

    model_name = os.getenv("WHISPER_MODEL", "small")
    model = await ensure_model_loaded(model_name=model_name, device=device)

    # get duration (ffprobe)
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, check=True
        )
        total_duration = float(out.stdout.strip()) if out.stdout else None
    except Exception:
        total_duration = None

    transcription_lines = []
    jobs[job_id]["progress"] = 0.0
    await push_job_update(job_id)

    try:
        # generator from faster-whisper
        segments_iter = model.transcribe(path, beam_size=5, word_timestamps=False)

        processed_time = 0.0
        for segment in segments_iter:
            segs = segment if isinstance(segment, list) else [segment]
            for s in segs:
                start = s.get("start", 0.0)
                end = s.get("end", start)
                text = s.get("text") or s.get("transcript") or ""
                transcription_lines.append(f"[{start:0.2f}] {text}")

                # send immediate segment message to UI
                await manager.broadcast({
                    "type": "transcript_segment",
                    "job_id": job_id,
                    "segment": {"start": start, "end": end, "text": text}
                })

                processed_time = max(processed_time, end)

            # update progress
            if total_duration:
                jobs[job_id]["progress"] = round(min(100.0, (processed_time / total_duration) * 100.0), 2)
            else:
                jobs[job_id]["progress"] = min(99.0, jobs[job_id]["progress"] + 5.0)
            jobs[job_id]["updated_at"] = time.time()
            await push_job_update(job_id)

        final_text = "\n".join(transcription_lines).strip()
        outfile = os.path.join(TRANS_DIR, f"{filename}.txt")
        with open(outfile, "w", encoding="utf-8") as f:
            f.write(final_text)

        # copy transcript to session folder if given
        if session_id:
            session_folder = os.path.join(SESSIONS_DIR, session_id)
            os.makedirs(session_folder, exist_ok=True)
            shutil.copy(outfile, os.path.join(session_folder, os.path.basename(outfile)))

        jobs[job_id]["status"] = "finished"
        jobs[job_id]["result"] = {"transcript_file": outfile, "text_preview": final_text[:400]}
        jobs[job_id]["progress"] = 100.0
        jobs[job_id]["updated_at"] = time.time()
        await push_job_update(job_id)

    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)
        jobs[job_id]["updated_at"] = time.time()
        await push_job_update(job_id)

# Helper to parse ffmpeg time string HH:MM:SS.xx -> seconds
def ffmpeg_time_to_secs(timestr: str) -> float:
    try:
        parts = timestr.split(':')
        h = int(parts[0]); m = int(parts[1]); s = float(parts[2])
        return h*3600 + m*60 + s
    except Exception:
        return 0.0

# 3) CREATE CLIP (simple drawtext) with FFmpeg progress parsing
async def do_clip(job_id: str):
    jobs[job_id]["status"] = "running"
    await push_job_update(job_id)
    meta = jobs[job_id]["meta"]
    filename = meta["filename"]
    start = float(meta["start"])
    end = float(meta["end"])
    text = meta.get("text", "")
    session_id = meta.get("session_id")
    output_name = meta.get("output_name") or f"clip_{int(time.time())}.mp4"

    input_path = os.path.join(DOWNLOADS_DIR, filename)
    if not os.path.exists(input_path):
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = "input file not found"
        await push_job_update(job_id)
        return

    duration = end - start
    if duration <= 0:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = "invalid start/end"
        await push_job_update(job_id)
        return

    outpath = os.path.join(CLIPS_DIR, output_name)

    vf = None
    if text:
        safe_text = text.replace("'", "\\'")
        vf = (f"drawtext=text='{safe_text}':"f"fontfile={DEFAULT_EMOJI_FONT}:"f"fontcolor=white:fontsize=28:"f"x=(w-text_w)/2:y=h-200:"f"box=1:boxcolor=black@0.6:boxborderw=10")

        # Always apply vertical 9:16 crop + scale
        vf_main = vertical_filter()
        # If user entered text, overlay AFTER resizing
        if vf:
            final_vf = f"{vf_main},hflip,{vf}"
        else:
            final_vf = vf_main

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start), "-i", input_path,
            "-t", str(duration),
            "-vf", final_vf,
            "-c:v", "libx264",
            "-c:a", "aac",
            outpath
        ]
    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, universal_newlines=True)
    try:
        while True:
            line = proc.stderr.readline()
            if not line:
                break
            if "time=" in line:
                # parse time=HH:MM:SS.xx
                import re
                m = re.search(r"time=(\d+:\d+:\d+\.\d+)", line)
                if m:
                    secs = ffmpeg_time_to_secs(m.group(1))
                    prog = (secs / duration) * 100.0
                    jobs[job_id]["progress"] = round(min(100.0, prog), 2)
                    jobs[job_id]["updated_at"] = time.time()
                    await manager.broadcast({"type": "clip_progress", "job_id": job_id, "progress": jobs[job_id]["progress"]})
                    await push_job_update(job_id)
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed with code {proc.returncode}")
        jobs[job_id]["status"] = "finished"
        jobs[job_id]["result"] = {"clip_file": outpath}
        jobs[job_id]["progress"] = 100.0
        jobs[job_id]["updated_at"] = time.time()
        # copy into session if requested
        if session_id:
            os.makedirs(os.path.join(SESSIONS_DIR, session_id), exist_ok=True)
            shutil.copy(outpath, os.path.join(SESSIONS_DIR, session_id, os.path.basename(outpath)))
        await push_job_update(job_id)
    except Exception as e:
        proc.kill()
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)
        jobs[job_id]["updated_at"] = time.time()
        await push_job_update(job_id)

# 4) TEMPLATE clip (JSON-based) with live progress (ffmpeg)
async def do_template_clip(job_id: str):
    jobs[job_id]["status"] = "running"
    await push_job_update(job_id)
    meta = jobs[job_id]["meta"]
    filename = meta["filename"]
    start = float(meta["start"])
    end = float(meta["end"])
    template = meta["template"]
    output_name = meta.get("output_name") or f"templated_{int(time.time())}.mp4"
    session_id = meta.get("session_id")

    input_path = os.path.join(DOWNLOADS_DIR, filename)
    if not os.path.exists(input_path):
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = "input file not found"
        await push_job_update(job_id)
        return

    duration = end - start
    if duration <= 0:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = "invalid start/end"
        await push_job_update(job_id)
        return

    outpath = os.path.join(CLIPS_DIR, output_name if output_name.endswith(".mp4") else output_name + ".mp4")

    vf_filters = []
    # resolution
    if template.get("resolution"):
        try:
            w, h = template["resolution"].split("x")
            vf_filters.append(f"scale={w}:{h}")
        except Exception:
            pass

        # # Config
        PORTRAIT_W = 1080
        PORTRAIT_H = 1920

        # vertical safe-zone margins
        SAFE_TOP = 120
        SAFE_BOTTOM = 120
        SAFE_CENTER = PORTRAIT_H // 2

        # Track stacking offsets
        bottom_stack = 0
        center_stack = 0
        top_stack = 0

    # texts
    for txt in template.get("texts", []):
        text = txt.get("text", "").replace("'", "\\'")
        fontcolor = txt.get("fontcolor", "white")
        fontsize = txt.get("fontsize", 48)

        # Handle x position
        x_val = txt.get("x", "(w-text_w)/2")
        if x_val == "center":
            x_val = "(w-text_w)/2"

        # Handle y position
        pos = txt.get("y", "").lower()

        # Estimate text height (fontsize * 1.4 for safety)
        estimated_h = int(txt.get("fontsize", 50) * 1.4)

        if "top" in pos:
            auto_y = SAFE_TOP + top_stack
            top_stack += estimated_h + 40  # padding
        elif "mid" in pos or "center" in pos:
            auto_y = SAFE_CENTER + center_stack
            center_stack += estimated_h + 40
        elif "bottom" in pos:
            auto_y = PORTRAIT_H - SAFE_BOTTOM - bottom_stack - estimated_h
            bottom_stack += estimated_h + 40
        else:
            # fallback bottom
            auto_y = PORTRAIT_H - SAFE_BOTTOM - bottom_stack - estimated_h
            bottom_stack += estimated_h + 40

        draw = (
            f"drawtext=text='{text}':"
            f"fontcolor={fontcolor}:fontsize={fontsize}:"
            f"x={x_val}:y={auto_y}"
        )

        # Optional box
        if txt.get("box"):
            boxcolor = txt.get("boxcolor", "black@0.5")
            boxborder = txt.get("boxborder", 5)
            draw += f":box=1:boxcolor={boxcolor}:boxborderw={boxborder}"

        # Optional stroke
        if txt.get("strokecolor"):
            strokewidth = txt.get("strokewidth", 1)
            draw += f":bordercolor={txt.get('strokecolor')}:borderw={strokewidth}"

        # Optional shadow
        if txt.get("shadowcolor"):
            shadowx = txt.get("shadowx", 0)
            shadowy = txt.get("shadowy", 0)
            draw += f":shadowcolor={txt.get('shadowcolor')}:shadowx={shadowx}:shadowy={shadowy}"

        if txt.get("fontfile") and os.path.exists(txt.get("fontfile")):
            draw += f":fontfile={txt.get('fontfile')}"
        else:
            draw += f":fontfile={DEFAULT_EMOJI_FONT}"

        vf_filters.append(draw)

    vf_arg = ",".join(vf_filters) if vf_filters else None
    vf_main = vertical_filter()
    final_vf = f"{vf_main},hflip,{vf_arg}" if vf_arg else vf_main
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start), "-i", input_path,
        "-t", str(duration),
        "-vf", final_vf,
        "-c:v", "libx264",
        "-c:a", "aac",
        outpath
    ]

    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, universal_newlines=True)
    try:
        while True:
            line = proc.stderr.readline()
            if not line:
                break
            if "time=" in line:
                import re
                m = re.search(r"time=(\d+:\d+:\d+\.\d+)", line)
                if m:
                    secs = ffmpeg_time_to_secs(m.group(1))
                    prog = (secs / duration) * 100.0
                    jobs[job_id]["progress"] = round(min(100.0, prog), 2)
                    jobs[job_id]["updated_at"] = time.time()
                    await manager.broadcast({"type": "template_progress", "job_id": job_id, "progress": jobs[job_id]["progress"]})
                    await push_job_update(job_id)
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed with code {proc.returncode}")

        jobs[job_id]["status"] = "finished"
        jobs[job_id]["result"] = {"clip_file": outpath}
        jobs[job_id]["progress"] = 100.0
        jobs[job_id]["updated_at"] = time.time()
        if session_id:
            os.makedirs(os.path.join(SESSIONS_DIR, session_id), exist_ok=True)
            shutil.copy(outpath, os.path.join(SESSIONS_DIR, session_id, os.path.basename(outpath)))
        await push_job_update(job_id)
    except Exception as e:
        proc.kill()
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)
        jobs[job_id]["updated_at"] = time.time()
        await push_job_update(job_id)

# 5) MERGE clips with live progress (ffmpeg concat via re-encode to stream progress)
async def do_merge(job_id: str):
    jobs[job_id]["status"] = "running"
    await push_job_update(job_id)
    meta = jobs[job_id]["meta"]
    clips = meta["clips"]
    output_name = meta["output_name"]
    session_id = meta.get("session_id")

    # prepare list file
    list_file = os.path.join(CLIPS_DIR, f"merge_{uuid_name(output_name)}.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for c in clips:
            f.write(f"file '{os.path.join(CLIPS_DIR, c)}'\n")

    outpath = os.path.join(CLIPS_DIR, output_name if output_name.endswith(".mp4") else output_name + ".mp4")

    # We'll run ffmpeg concat re-encode so we can get progress (time)
        # Apply vertical 9:16 crop on merged output
    vf_main = vertical_filter()

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", list_file,
        "-vf", vf_main,
        "-c:v", "libx264",
        "-c:a", "aac",
        outpath
    ]


    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, universal_newlines=True)
    # estimate total duration by summing clip durations
    total_duration = 0.0
    for c in clips:
        clip_path = os.path.join(CLIPS_DIR, c)
        try:
            out = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1", clip_path], capture_output=True, text=True, check=True)
            total_duration += float(out.stdout.strip()) if out.stdout else 0.0
        except Exception:
            total_duration += 0.0

    try:
        while True:
            line = proc.stderr.readline()
            if not line:
                break
            if "time=" in line:
                import re
                m = re.search(r"time=(\d+:\d+:\d+\.\d+)", line)
                if m:
                    secs = ffmpeg_time_to_secs(m.group(1))
                    prog = (secs / total_duration) * 100.0 if total_duration else 0.0
                    jobs[job_id]["progress"] = round(min(100.0, prog), 2)
                    jobs[job_id]["updated_at"] = time.time()
                    await manager.broadcast({"type": "merge_progress", "job_id": job_id, "progress": jobs[job_id]["progress"]})
                    await push_job_update(job_id)
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed with code {proc.returncode}")

        jobs[job_id]["status"] = "finished"
        jobs[job_id]["result"] = {"merged_file": outpath}
        jobs[job_id]["progress"] = 100.0
        jobs[job_id]["updated_at"] = time.time()
        if session_id:
            os.makedirs(os.path.join(SESSIONS_DIR, session_id), exist_ok=True)
            shutil.copy(outpath, os.path.join(SESSIONS_DIR, session_id, os.path.basename(outpath)))
        await push_job_update(job_id)
    except Exception as e:
        proc.kill()
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)
        jobs[job_id]["updated_at"] = time.time()
        await push_job_update(job_id)
    finally:
        try:
            if os.path.exists(list_file):
                os.remove(list_file)
        except Exception:
            pass

# 6) CLEANUP (immediate, not queued)
def cleanup_session_immediate(session_id: str, delete_clips: bool = True, delete_video: bool = True) -> bool:
    folder = os.path.join(SESSIONS_DIR, session_id)
    if not os.path.exists(folder):
        return False
    for fname in os.listdir(folder):
        full = os.path.join(folder, fname)
        try:
            if fname.endswith(".mp4"):
                if delete_clips or delete_video:
                    os.remove(full)
            else:
                os.remove(full)
        except Exception:
            pass
    try:
        if not os.listdir(folder):
            os.rmdir(folder)
    except Exception:
        pass
    return True

# -----------------------
# Worker dispatcher
# -----------------------
async def worker_loop():
    while True:
        job_id = await job_queue.get()
        job = jobs.get(job_id)
        if not job:
            job_queue.task_done()
            continue
        kind = job["kind"]
        try:
            if kind == "download":
                await do_download(job_id)
            elif kind == "transcribe":
                await do_transcribe(job_id)
            elif kind == "clip":
                await do_clip(job_id)
            elif kind == "template_clip":
                await do_template_clip(job_id)
            elif kind == "merge":
                await do_merge(job_id)
            else:
                jobs[job_id]["status"] = "error"
                jobs[job_id]["error"] = f"unknown job kind: {kind}"
                await push_job_update(job_id)
        except Exception as e:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = str(e)
            await push_job_update(job_id)
        finally:
            job_queue.task_done()

# -----------------------
# Startup: spawn workers
# -----------------------
@app.on_event("startup")
async def startup_event():
    # spawn 1 worker by default; increase for concurrency
    for _ in range(1):
        asyncio.create_task(worker_loop())

# -----------------------
# API endpoints
# -----------------------
@app.get("/")
async def read_root():
    return FileResponse("static/index.html")

@app.post("/api/download")
async def api_download(req: DownloadRequest):
    job_id = create_job("download", {"url": req.url, "session_id": req.session_id})
    await job_queue.put(job_id)
    await push_job_update(job_id)
    return {"job_id": job_id}

@app.post("/api/transcribe")
async def api_transcribe(req: TranscribeRequest):
    job_id = create_job("transcribe", {"filename": req.filename, "session_id": req.session_id})
    await job_queue.put(job_id)
    await push_job_update(job_id)
    return {"job_id": job_id}

@app.post("/api/clip")
async def api_clip(req: ClipRequest):
    job_id = create_job("clip", {"filename": req.filename, "start": req.start, "end": req.end, "text": req.text, "session_id": req.session_id, "output_name": req.output_name})
    await job_queue.put(job_id)
    await push_job_update(job_id)
    return {"job_id": job_id}

@app.post("/api/template-clip")
async def api_template_clip(req: TemplateClipRequest):
    # load template from file or use provided json
    template = {}
    if req.template_name:
        path = os.path.join(TEMPLATES_DIR, req.template_name)
        if not os.path.exists(path):
            return JSONResponse({"error": "template not found"}, status_code=404)
        import json
        with open(path, "r", encoding="utf-8") as f:
            template = json.load(f)
    elif req.template_json:
        template = req.template_json
    else:
        return JSONResponse({"error":"template_name or template_json required"}, status_code=400)

    meta = {"filename": req.filename, "start": req.start, "end": req.end, "template": template, "output_name": req.output_name, "session_id": req.session_id}
    job_id = create_job("template_clip", meta)
    await job_queue.put(job_id)
    await push_job_update(job_id)
    return {"job_id": job_id}

@app.post("/api/merge")
async def api_merge(req: MergeRequest):
    meta = {"clips": req.clips, "output_name": req.output_name, "session_id": req.session_id}
    job_id = create_job("merge", meta)
    await job_queue.put(job_id)
    await push_job_update(job_id)
    return {"job_id": job_id}

@app.post("/api/session/cleanup")
async def api_cleanup(req: CleanupRequest):
    ok = cleanup_session_immediate(req.session_id, delete_clips=req.delete_clips, delete_video=req.delete_video)
    return {"ok": ok}

@app.get("/api/job/{job_id}")
async def api_job_status(job_id: str):
    j = jobs.get(job_id)
    if not j:
        return JSONResponse({"error": "job not found"}, status_code=404)
    return j

@app.get("/downloads/{filename}")
async def get_download_file(filename: str):
    path = os.path.join(DOWNLOADS_DIR, filename)
    if not os.path.exists(path):
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path)

@app.get("/clips/{filename}")
async def get_clip_file(filename: str):
    path = os.path.join(CLIPS_DIR, filename)
    if not os.path.exists(path):
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path)

# WebSocket endpoint
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            # keep alive; if client sends data, echo ping
            msg = await ws.receive_text()
            try:
                await ws.send_text(f"pong: {msg}")
            except Exception:
                pass
    except WebSocketDisconnect:
        manager.disconnect(ws)
