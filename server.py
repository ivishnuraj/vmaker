from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room
import platform
import os
import yt_dlp
import asyncio
import threading
import time
import uuid
import subprocess
import shutil
import queue
from typing import Dict, Any, List, Optional
import json
try:
    from faster_whisper import WhisperModel
    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False
    print("Warning: faster-whisper not available")

# Directory constants
DOWNLOADS_DIR = "downloads"
CLIPS_DIR = "clips"
TRANS_DIR = "transcripts"
TEMPLATES_DIR = "templates"
SESSIONS_DIR = "sessions"
STATIC_DIR = "static"
FONTS_DIR = "fonts"
DEFAULT_EMOJI_FONT = os.path.join(FONTS_DIR, "NotoColorEmoji-Regular.ttf")

# Create directories
for dir_path in [DOWNLOADS_DIR, CLIPS_DIR, TRANS_DIR, TEMPLATES_DIR, SESSIONS_DIR, STATIC_DIR, FONTS_DIR]:
    os.makedirs(dir_path, exist_ok=True)

# Job management
job_queue = queue.Queue()
jobs: Dict[str, Dict[str, Any]] = {}
clips_metadata: Dict[str, Dict[str, Any]] = {}  # clip_filename -> metadata
templates: Dict[str, Dict[str, Any]] = {}  # template_name -> template_data
whisper_model = None

app = Flask(__name__)
CORS(app, origins=["*"], methods=["GET", "POST", "OPTIONS"], allow_headers=["Content-Type", "Authorization", "Accept", "Origin", "X-Requested-With"])
socketio = SocketIO(app, cors_allowed_origins=["*"], async_mode='threading')

def log(message):
    print(message)
    socketio.emit('log', message)

# Job management functions
def create_job(kind: str, meta: dict) -> str:
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

def push_job_update(job_id: str):
    state = jobs.get(job_id, {})
    socketio.emit('job_update', state)

def vertical_filter():
    return "crop=in_h*9/16:in_h:(in_w-(in_h*9/16))/2:0,scale=1080:1920"

def mobile_full_width_filter():
    # Scale to 1080:1920 maintaining aspect ratio, padding with black bars if needed
    return "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black"

def ffmpeg_time_to_secs(timestr: str) -> float:
    try:
        parts = timestr.split(':')
        h, m, s = int(parts[0]), int(parts[1]), float(parts[2])
        return h*3600 + m*60 + s
    except:
        return 0.0

def uuid_name(base: str = "") -> str:
    return f"{base}_{uuid.uuid4().hex[:8]}" if base else uuid.uuid4().hex[:8]

# Template management functions
def load_templates():
    """Load all templates from JSON files"""
    global templates
    templates = {}
    if os.path.exists(TEMPLATES_DIR):
        for file in os.listdir(TEMPLATES_DIR):
            if file.endswith('.json'):
                template_name = os.path.splitext(file)[0]
                try:
                    with open(os.path.join(TEMPLATES_DIR, file), 'r') as f:
                        templates[template_name] = json.load(f)
                except Exception as e:
                    print(f"Error loading template {template_name}: {e}")

def save_template(template_name: str, template_data: dict):
    """Save a template to JSON file"""
    template_path = os.path.join(TEMPLATES_DIR, f"{template_name}.json")
    with open(template_path, 'w') as f:
        json.dump(template_data, f, indent=2)
    templates[template_name] = template_data

def get_templates_list():
    """Get list of available templates"""
    return [{"name": name, "data": data} for name, data in templates.items()]

def generate_overlay_filter(overlays):
    """Generate FFmpeg filter string for overlay elements"""
    filters = []

    for i, overlay in enumerate(overlays):
        overlay_type = overlay.get("type", "text")

        if overlay_type == "text":
            # Text overlay
            text = overlay.get("text", "")
            if not text:
                continue

            safe_text = text.replace("'", "\\'").replace(":", "\\:")

            # Position
            x = overlay.get("x", "(w-text_w)/2")
            y = overlay.get("y", "(h-text_h)/2")

            # Font settings
            font_file = overlay.get("font", DEFAULT_EMOJI_FONT)
            # If no font specified, use default emoji font
            if not font_file:
                font_file = DEFAULT_EMOJI_FONT
            font_size = overlay.get("fontSize", 28)
            font_color = overlay.get("textColor", "white")

            # Build drawtext filter
            drawtext = f"drawtext=text='{safe_text}':fontfile={font_file}:fontsize={font_size}:fontcolor={font_color}"

            # Add positioning
            drawtext += f":x={x}:y={y}"

            # Box/background
            if overlay.get("box", False):
                box_color = overlay.get("boxColor", "black@0.6")
                box_border = overlay.get("boxBorder", 10)
                drawtext += f":box=1:boxcolor={box_color}:boxborderw={box_border}"

            # Shadow
            if overlay.get("shadow", False):
                shadow_color = overlay.get("shadowColor", "black@0.8")
                shadow_x = overlay.get("shadowX", 2)
                shadow_y = overlay.get("shadowY", 2)
                drawtext += f":shadowcolor={shadow_color}:shadowx={shadow_x}:shadowy={shadow_y}"

            # Stroke/outline
            if overlay.get("stroke", False):
                stroke_color = overlay.get("strokeColor", "black")
                stroke_width = overlay.get("strokeWidth", 1)
                drawtext += f":bordercolor={stroke_color}:borderw={stroke_width}"

            filters.append(drawtext)

        elif overlay_type == "emoji":
            # Emoji overlay (treated as text)
            emoji = overlay.get("emoji", "")
            if not emoji:
                continue

            x = overlay.get("x", "(w-text_w)/2")
            y = overlay.get("y", "(h-text_h)/2")
            font_size = overlay.get("fontSize", 48)
            font_file = overlay.get("font", DEFAULT_EMOJI_FONT)
            # If no font specified, use default emoji font
            if not font_file:
                font_file = DEFAULT_EMOJI_FONT

            drawtext = f"drawtext=text='{emoji}':fontfile={font_file}:fontsize={font_size}:fontcolor=white:x={x}:y={y}"

            if overlay.get("shadow", False):
                drawtext += ":shadowcolor=black@0.8:shadowx=2:shadowy=2"

            filters.append(drawtext)

        elif overlay_type == "image":
            # Image overlay (future enhancement)
            # Would require additional FFmpeg filter complexity
            pass

    return filters

# Template management functions
def load_templates():
    """Load all templates from JSON files"""
    global templates
    templates = {}
    if os.path.exists(TEMPLATES_DIR):
        for file in os.listdir(TEMPLATES_DIR):
            if file.endswith('.json'):
                template_name = os.path.splitext(file)[0]
                try:
                    with open(os.path.join(TEMPLATES_DIR, file), 'r') as f:
                        templates[template_name] = json.load(f)
                except Exception as e:
                    print(f"Error loading template {template_name}: {e}")

def save_template(template_name: str, template_data: dict):
    """Save a template to JSON file"""
    template_path = os.path.join(TEMPLATES_DIR, f"{template_name}.json")
    with open(template_path, 'w') as f:
        json.dump(template_data, f, indent=2)
    templates[template_name] = template_data

def get_templates_list():
    """Get list of available templates"""
    return [{"name": name, "data": data} for name, data in templates.items()]

def get_system_fonts():
    """Get list of available system fonts"""
    fonts = []

    # Common font directories
    font_dirs = [
        "/System/Library/Fonts",  # macOS
        "/Library/Fonts",         # macOS user fonts
        "/usr/share/fonts",       # Linux
        "/usr/local/share/fonts", # Linux
        "C:\\Windows\\Fonts",     # Windows
    ]

    # Also check our custom fonts directory
    if os.path.exists(FONTS_DIR):
        font_dirs.append(FONTS_DIR)

    for font_dir in font_dirs:
        if os.path.exists(font_dir):
            for root, dirs, files in os.walk(font_dir):
                for file in files:
                    if file.lower().endswith(('.ttf', '.otf', '.woff', '.woff2')):
                        # Get just the filename without extension for FFmpeg
                        font_name = os.path.splitext(file)[0]
                        fonts.append({
                            "name": font_name,
                            "file": os.path.join(root, file),
                            "path": os.path.join(root, file)
                        })

    # Remove duplicates and sort
    seen = set()
    unique_fonts = []
    for font in fonts:
        if font['name'] not in seen:
            seen.add(font['name'])
            unique_fonts.append(font)

    return sorted(unique_fonts, key=lambda x: x['name'])

# API Endpoints
@app.route('/api/data')
def data():
    log("Data route called")
    return {"message": "Hello from Python"}

@app.route('/api/job/<job_id>')
def get_job(job_id):
    job = jobs.get(job_id)
    if not job:
        return {"error": "job not found"}, 404
    return job

@app.route('/api/templates')
def get_templates():
    return {"templates": get_templates_list()}

@app.route('/api/fonts')
def get_fonts():
    return {"fonts": get_system_fonts()}

@app.route('/api/templates', methods=['POST', 'OPTIONS'])
def create_template():
    if request.method == 'OPTIONS':
        response = jsonify({'message': 'OK'})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type, Authorization, Accept, Origin, X-Requested-With')
        return response

    data = request.get_json()
    template_name = data.get('name')
    template_data = data.get('data')

    if not template_name or not template_data:
        return {"error": "name and data required"}, 400

    save_template(template_name, template_data)
    return {"message": "template created", "name": template_name}

@app.route('/api/clips/<path:video_filename>')
def get_clips_for_video(video_filename):
    clips = []
    video_name = os.path.splitext(video_filename)[0]  # Remove extension

    # Check video-specific folder
    video_clips_dir = os.path.join(CLIPS_DIR, video_name)
    if os.path.exists(video_clips_dir):
        for clip_file in os.listdir(video_clips_dir):
            if clip_file.endswith('.mp4'):
                clip_path = os.path.join(video_clips_dir, clip_file)
                clip_filename = f"{video_name}/{clip_file}"  # Include folder in path

                # Get metadata if available
                metadata = clips_metadata.get(clip_filename, {})

                # Handle both old format (text) and new format (overlays)
                text_content = ""
                if "overlays" in metadata:
                    # Extract text from overlays for display
                    text_overlays = [o for o in metadata["overlays"] if o.get("type") == "text"]
                    if text_overlays:
                        text_content = text_overlays[0].get("text", "")
                elif "text" in metadata:
                    text_content = metadata["text"]

                clips.append({
                    "filename": clip_filename,
                    "start": metadata.get("start", 0),
                    "end": metadata.get("end", 0),
                    "text": text_content,
                    "overlays": metadata.get("overlays", []),
                    "template": metadata.get("template", ""),
                    "created_at": metadata.get("created_at", os.path.getctime(clip_path)),
                    "path": clip_path
                })

    # Also check legacy clips (for backward compatibility)
    for clip_filename, metadata in clips_metadata.items():
        if metadata.get("source_video") == video_filename and not clip_filename.startswith(f"{video_name}/"):
            text_content = ""
            if "overlays" in metadata:
                text_overlays = [o for o in metadata["overlays"] if o.get("type") == "text"]
                if text_overlays:
                    text_content = text_overlays[0].get("text", "")
            elif "text" in metadata:
                text_content = metadata["text"]

            clips.append({
                "filename": clip_filename,
                "start": metadata["start"],
                "end": metadata["end"],
                "text": text_content,
                "overlays": metadata.get("overlays", []),
                "template": metadata.get("template", ""),
                "created_at": metadata["created_at"],
                "path": metadata["path"]
            })

    return {"clips": clips}

@app.route('/api/download', methods=['POST', 'OPTIONS'])
def api_download():
    if request.method == 'OPTIONS':
        response = jsonify({'message': 'OK'})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type, Authorization, Accept, Origin, X-Requested-With')
        return response

    data = request.get_json()
    url = data.get('url')
    session_id = data.get('session_id')

    if not url:
        return {"error": "url required"}, 400

    job_id = create_job("download", {"url": url, "session_id": session_id})
    job_queue.put(job_id)
    push_job_update(job_id)
    return {"job_id": job_id}

@app.route('/api/transcribe', methods=['POST', 'OPTIONS'])
def api_transcribe():
    if request.method == 'OPTIONS':
        response = jsonify({'message': 'OK'})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type, Authorization, Accept, Origin, X-Requested-With')
        return response

    data = request.get_json()
    filename = data.get('filename')
    session_id = data.get('session_id')

    if not filename:
        return {"error": "filename required"}, 400

    job_id = create_job("transcribe", {"filename": filename, "session_id": session_id})
    job_queue.put(job_id)
    push_job_update(job_id)
    return {"job_id": job_id}

@app.route('/api/clip', methods=['POST', 'OPTIONS'])
def api_clip():
    if request.method == 'OPTIONS':
        # Handle preflight request
        response = jsonify({'message': 'OK'})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type, Authorization, Accept, Origin, X-Requested-With')
        return response

    data = request.get_json()
    filename = data.get('filename')
    start = data.get('start')
    end = data.get('end')
    text = data.get('text', '')
    session_id = data.get('session_id')
    output_name = data.get('output_name')

    if not all([filename, start is not None, end is not None]):
        return {"error": "filename, start, end required"}, 400

    job_id = create_job("clip", {
        "filename": filename,
        "start": start,
        "end": end,
        "text": text,
        "session_id": session_id,
        "output_name": output_name
    })
    job_queue.put(job_id)
    push_job_update(job_id)
    return {"job_id": job_id}

@app.route('/api/clip-template', methods=['POST', 'OPTIONS'])
def api_clip_with_template():
    if request.method == 'OPTIONS':
        response = jsonify({'message': 'OK'})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type, Authorization, Accept, Origin, X-Requested-With')
        return response

    data = request.get_json()
    filename = data.get('filename')
    template_name = data.get('template_name')
    custom_overlays = data.get('custom_overlays')
    custom_start = data.get('custom_start')
    custom_duration = data.get('custom_duration')
    custom_output_name = data.get('custom_output_name')
    custom_resolution = data.get('custom_resolution')
    custom_flip = data.get('custom_flip')
    session_id = data.get('session_id')

    if not filename or not template_name:
        return {"error": "filename and template_name required"}, 400

    if template_name not in templates:
        return {"error": "template not found"}, 404

    template = templates[template_name].copy()

    # Override with custom properties if provided
    if custom_overlays is not None:
        template["overlays"] = custom_overlays
    if custom_start is not None:
        template["start"] = custom_start
    if custom_duration is not None:
        template["duration"] = custom_duration
    if custom_output_name is not None:
        template["output_name"] = custom_output_name
    if custom_resolution is not None:
        template["resolution"] = custom_resolution
    if custom_flip is not None:
        template["flip"] = custom_flip

    job_id = create_job("clip_template", {
        "filename": filename,
        "template": template,
        "session_id": session_id
    })
    job_queue.put(job_id)
    push_job_update(job_id)
    return {"job_id": job_id}

@app.route('/api/os-details')
def os_details():
    log("OS details route called")
    return {
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor()
    }

@socketio.on('download')
def handle_download(data):
    url = data.get('url')
    session_id = data.get('session_id')

    if not url:
        emit('error', {"message": "No URL provided"})
        return

    job_id = create_job("download", {"url": url, "session_id": session_id})
    job_queue.put(job_id)
    push_job_update(job_id)
    emit('download_started', {"job_id": job_id})

@socketio.on('get_videos')
def handle_get_videos():
    log("Getting videos list")
    downloads_dir = './downloads'
    videos_list = []
    if os.path.exists(downloads_dir):
        for file in os.listdir(downloads_dir):
            if file.endswith('.mp4'):
                path = os.path.join(downloads_dir, file)
                title = os.path.splitext(file)[0]  # filename without .mp4
                videos_list.append({"title": title, "path": path})
    socketio.emit('videos_update', videos_list)

# Background job functions
def do_download(job_id: str):
    jobs[job_id]["status"] = "running"
    push_job_update(job_id)

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
            total_bytes = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            prog = (downloaded / total_bytes * 100) if total_bytes else 0.0
            jobs[job_id]["progress"] = round(prog, 2)
            jobs[job_id]["updated_at"] = time.time()
            socketio.emit('download_progress', {"job_id": job_id, "progress": jobs[job_id]["progress"]})
            push_job_update(job_id)
        elif d.get("status") == "finished":
            jobs[job_id]["progress"] = 100.0
            push_job_update(job_id)

    ydl_opts["progress_hooks"].append(progress)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        jobs[job_id]["status"] = "finished"
        jobs[job_id]["result"] = {"filename": os.path.basename(outpath), "path": outpath}
        jobs[job_id]["progress"] = 100.0

        if session_id:
            session_folder = os.path.join(SESSIONS_DIR, session_id)
            os.makedirs(session_folder, exist_ok=True)
            shutil.copy(outpath, os.path.join(session_folder, os.path.basename(outpath)))

        push_job_update(job_id)
        socketio.emit('videos_update', get_videos_list())  # Refresh videos list

    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)
        push_job_update(job_id)

# Job queue worker
def job_worker():
    while True:
        try:
            job_id = job_queue.get(timeout=1)
            job = jobs.get(job_id)
            if not job:
                continue

            kind = job["kind"]
            try:
                if kind == "download":
                    do_download(job_id)
                elif kind == "transcribe":
                    do_transcribe(job_id)
                elif kind == "clip":
                    do_clip(job_id)
                elif kind == "clip_template":
                    do_clip_with_template(job_id)
                else:
                    jobs[job_id]["status"] = "error"
                    jobs[job_id]["error"] = f"unknown job kind: {kind}"
                    push_job_update(job_id)
            except Exception as e:
                jobs[job_id]["status"] = "error"
                jobs[job_id]["error"] = str(e)
                push_job_update(job_id)
            finally:
                job_queue.task_done()
        except:
            continue

# Start background worker
worker_thread = threading.Thread(target=job_worker, daemon=True)
worker_thread.start()

def do_clip(job_id: str):
    jobs[job_id]["status"] = "running"
    push_job_update(job_id)

    meta = jobs[job_id]["meta"]
    filename = meta["filename"]
    start = float(meta["start"])
    end = float(meta["end"])
    text = meta.get("text", "")
    flip = meta.get("flip", False)
    session_id = meta.get("session_id")
    output_name = meta.get("output_name") or f"clip_{int(time.time())}.mp4"

    input_path = os.path.join(DOWNLOADS_DIR, filename)
    if not os.path.exists(input_path):
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = "input file not found"
        push_job_update(job_id)
        return

    duration = end - start
    if duration <= 0:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = "invalid start/end"
        push_job_update(job_id)
        return

    # Create video-specific folder
    video_name = os.path.splitext(filename)[0]  # Remove extension
    video_clips_dir = os.path.join(CLIPS_DIR, video_name)
    os.makedirs(video_clips_dir, exist_ok=True)

    # Make filename unique with timestamp
    timestamp = int(time.time() * 1000)  # milliseconds for uniqueness
    unique_output_name = f"{os.path.splitext(output_name)[0]}_{timestamp}.mp4"
    outpath = os.path.join(video_clips_dir, unique_output_name)

    vf_filters = []

    # Add horizontal flip if requested
    if flip:
        vf_filters.append("hflip")

    if text:
        safe_text = text.replace("'", "\\'")
        vf = f"drawtext=text='{safe_text}':fontfile={DEFAULT_EMOJI_FONT}:fontcolor=white:fontsize=28:x=(w-text_w)/2:y=h-200:box=1:boxcolor=black@0.6:boxborderw=10"
        vf_filters.append(vf)

    vf_filters.append(vertical_filter())
    vf_arg = ",".join(vf_filters)

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start), "-i", input_path,
        "-t", str(duration),
        "-vf", vf_arg,
        "-c:v", "libx264",
        "-c:a", "aac",
        outpath
    ]

    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, universal_newlines=True)
    try:
        import re
        while True:
            line = proc.stderr.readline()
            if not line:
                break
            if "time=" in line:
                m = re.search(r"time=(\d+:\d+:\d+\.\d+)", line)
                if m:
                    secs = ffmpeg_time_to_secs(m.group(1))
                    prog = (secs / duration) * 100.0
                    jobs[job_id]["progress"] = round(min(100.0, prog), 2)
                    jobs[job_id]["updated_at"] = time.time()
                    socketio.emit('clip_progress', {"job_id": job_id, "progress": jobs[job_id]["progress"]})
                    push_job_update(job_id)
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed with code {proc.returncode}")

        jobs[job_id]["status"] = "finished"
        jobs[job_id]["result"] = {"clip_file": outpath}
        jobs[job_id]["progress"] = 100.0

        # Store clip metadata
        video_name = os.path.splitext(filename)[0]
        clip_filename = os.path.basename(outpath)
        metadata_key = f"{video_name}/{clip_filename}"
        clips_metadata[metadata_key] = {
            "source_video": filename,
            "start": start,
            "end": end,
            "text": text,
            "created_at": time.time(),
            "path": outpath
        }

        if session_id:
            session_folder = os.path.join(SESSIONS_DIR, session_id)
            os.makedirs(session_folder, exist_ok=True)
            shutil.copy(outpath, os.path.join(session_folder, os.path.basename(outpath)))

        push_job_update(job_id)

    except Exception as e:
        if proc:
            proc.kill()
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)
        push_job_update(job_id)

def do_clip_with_template(job_id: str):
    jobs[job_id]["status"] = "running"
    push_job_update(job_id)

    meta = jobs[job_id]["meta"]
    filename = meta["filename"]
    template = meta["template"]
    session_id = meta.get("session_id")

    input_path = os.path.join(DOWNLOADS_DIR, filename)
    if not os.path.exists(input_path):
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = "input file not found"
        push_job_update(job_id)
        return

    # Apply template settings
    start = template.get("start", 0)
    duration = template.get("duration", 10)  # Default 10 seconds
    end = start + duration

    # Replace template variables in output_name
    timestamp = int(time.time() * 1000)  # milliseconds for uniqueness
    output_name = template.get("output_name", f"template_clip_{timestamp}.mp4")
    output_name = output_name.replace("{timestamp}", str(timestamp))

    resolution = template.get("resolution", "1080:1920")  # Default vertical

    # Create video-specific folder
    video_name = os.path.splitext(filename)[0]  # Remove extension
    video_clips_dir = os.path.join(CLIPS_DIR, video_name)
    os.makedirs(video_clips_dir, exist_ok=True)

    outpath = os.path.join(video_clips_dir, output_name)

    vf_filters = []

    # Add horizontal flip if requested
    if template.get("flip", False):
        vf_filters.append("hflip")

    # Handle overlay arrays
    overlays = template.get("overlays", [])
    if overlays:
        overlay_filters = generate_overlay_filter(overlays)
        vf_filters.extend(overlay_filters)

    # Backward compatibility - handle old single text field
    elif template.get("text"):
        safe_text = template.get("text", "").replace("'", "\\'")
        font_size = template.get("font_size", 28)
        vf = f"drawtext=text='{safe_text}':fontfile={DEFAULT_EMOJI_FONT}:fontcolor=white:fontsize={font_size}:x=(w-text_w)/2:y=h-200:box=1:boxcolor=black@0.6:boxborderw=10"
        vf_filters.append(vf)

    # Apply resolution scaling if specified
    if resolution and resolution != "original":
        vf_filters.append(f"scale={resolution}")

    # Apply appropriate filter based on template
    if template.get("name") == "mobile_full_width":
        vf_filters.append(mobile_full_width_filter())
    else:
        # Default to vertical filter for TikTok-style format
        vf_filters.append(vertical_filter())

    vf_arg = ",".join(vf_filters)

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start), "-i", input_path,
        "-t", str(duration),
        "-vf", vf_arg,
        "-c:v", "libx264",
        "-c:a", "aac",
        outpath
    ]

    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, universal_newlines=True)
    try:
        import re
        while True:
            line = proc.stderr.readline()
            if not line:
                break
            if "time=" in line:
                m = re.search(r"time=(\d+:\d+:\d+\.\d+)", line)
                if m:
                    secs = ffmpeg_time_to_secs(m.group(1))
                    prog = (secs / duration) * 100.0
                    jobs[job_id]["progress"] = round(min(100.0, prog), 2)
                    jobs[job_id]["updated_at"] = time.time()
                    socketio.emit('clip_progress', {"job_id": job_id, "progress": jobs[job_id]["progress"]})
                    push_job_update(job_id)
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed with code {proc.returncode}")

        jobs[job_id]["status"] = "finished"
        jobs[job_id]["result"] = {"clip_file": outpath, "template": template.get("name", "unknown")}
        jobs[job_id]["progress"] = 100.0

        # Store clip metadata
        video_name = os.path.splitext(filename)[0]
        clip_filename = os.path.basename(outpath)
        metadata_key = f"{video_name}/{clip_filename}"
        clips_metadata[metadata_key] = {
            "source_video": filename,
            "start": start,
            "end": end,
            "overlays": overlays,
            "template": template.get("name", "unknown"),
            "created_at": time.time(),
            "path": outpath
        }

        if session_id:
            session_folder = os.path.join(SESSIONS_DIR, session_id)
            os.makedirs(session_folder, exist_ok=True)
            shutil.copy(outpath, os.path.join(session_folder, os.path.basename(outpath)))

        push_job_update(job_id)

    except Exception as e:
        if proc:
            proc.kill()
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)
        push_job_update(job_id)

def get_videos_list():
    videos_list = []
    if os.path.exists(DOWNLOADS_DIR):
        for file in os.listdir(DOWNLOADS_DIR):
            if file.endswith('.mp4'):
                path = os.path.join(DOWNLOADS_DIR, file)
                title = os.path.splitext(file)[0]
                videos_list.append({"title": title, "path": path})
    return videos_list

def ensure_whisper_model():
    global whisper_model
    if whisper_model is None and WHISPER_AVAILABLE:
        device = "cuda" if os.getenv("USE_CUDA", "0") == "1" else "cpu"
        compute_type = "int8_float16" if device == "cuda" else "float32"
        model_name = os.getenv("WHISPER_MODEL", "small")
        whisper_model = WhisperModel(model_name, device=device, compute_type=compute_type)
    return whisper_model

def do_transcribe(job_id: str):
    if not WHISPER_AVAILABLE:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = "faster-whisper not available"
        push_job_update(job_id)
        return

    jobs[job_id]["status"] = "running"
    push_job_update(job_id)

    filename = jobs[job_id]["meta"]["filename"]
    session_id = jobs[job_id]["meta"].get("session_id")
    path = os.path.join(DOWNLOADS_DIR, filename)

    if not os.path.exists(path):
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = "file not found"
        push_job_update(job_id)
        return

    model = ensure_whisper_model()

    # Get duration
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, check=True
        )
        total_duration = float(result.stdout.strip()) if result.stdout else None
    except:
        total_duration = None

    transcription_lines = []
    jobs[job_id]["progress"] = 0.0
    push_job_update(job_id)

    try:
        segments_iter = model.transcribe(path, beam_size=5, word_timestamps=False)
        processed_time = 0.0

        for segment in segments_iter:
            segs = segment if isinstance(segment, list) else [segment]
            for s in segs:
                start = s.get("start", 0.0)
                end = s.get("end", start)
                text = s.get("text") or s.get("transcript") or ""
                transcription_lines.append(f"[{start:0.2f}] {text}")

                socketio.emit('transcript_segment', {
                    "job_id": job_id,
                    "segment": {"start": start, "end": end, "text": text}
                })

                processed_time = max(processed_time, end)

            if total_duration:
                jobs[job_id]["progress"] = round(min(100.0, (processed_time / total_duration) * 100.0), 2)
            else:
                jobs[job_id]["progress"] = min(99.0, jobs[job_id]["progress"] + 5.0)
            jobs[job_id]["updated_at"] = time.time()
            push_job_update(job_id)

        final_text = "\n".join(transcription_lines).strip()
        outfile = os.path.join(TRANS_DIR, f"{filename}.txt")
        with open(outfile, "w", encoding="utf-8") as f:
            f.write(final_text)

        if session_id:
            session_folder = os.path.join(SESSIONS_DIR, session_id)
            os.makedirs(session_folder, exist_ok=True)
            shutil.copy(outfile, os.path.join(session_folder, os.path.basename(outfile)))

        jobs[job_id]["status"] = "finished"
        jobs[job_id]["result"] = {"transcript_file": outfile, "text_preview": final_text[:400]}
        jobs[job_id]["progress"] = 100.0
        push_job_update(job_id)

    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)
        push_job_update(job_id)

@app.route('/video/<filename>')
def get_video_file(filename):
    return send_from_directory('downloads', filename)

@app.route('/clips/<path:filepath>')
def get_clip_file(filepath):
    # filepath can be "video_name/clip_file.mp4"
    parts = filepath.split('/', 1)
    if len(parts) == 2:
        folder, filename = parts
        return send_from_directory(os.path.join('clips', folder), filename)
    else:
        # Legacy support for clips directly in clips folder
        return send_from_directory('clips', filepath)

@app.route('/transcripts/<filename>')
def get_transcript_file(filename):
    return send_from_directory('transcripts', filename)

if __name__ == '__main__':
    load_templates()  # Load templates on startup
    socketio.run(app, port=14562)