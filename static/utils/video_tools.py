# utils/video_tools.py
import os
import subprocess
import json
import time
from typing import List, Dict

DOWNLOADS_DIR = "downloads"
CLIPS_DIR = "clips"
TEMPLATES_DIR = "templates"
SESSIONS_DIR = "sessions"

os.makedirs(TEMPLATES_DIR, exist_ok=True)
os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs(CLIPS_DIR, exist_ok=True)


def _make_session_folder(session_id: str) -> str:
    folder = os.path.join(SESSIONS_DIR, session_id)
    os.makedirs(folder, exist_ok=True)
    return folder


# ---------------------------
# 1. Render Clip With Template
# ---------------------------
def render_template_clip(
    input_file: str,
    start: float,
    end: float,
    template: Dict,
    output_name: str,
    session_id: str = None
) -> str:
    """
    Renders a clip using a JSON-based template that supports multiple text overlays.

    Returns the output filename (relative to CLIPS_DIR).
    """

    input_path = os.path.join(DOWNLOADS_DIR, input_file)
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    # If user provided a session_id, create a session folder copy of the clip (for cleanup)
    session_folder = None
    if session_id:
        session_folder = _make_session_folder(session_id)

    # sanitize output_name
    safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in output_name)
    output_file = f"{safe_name}.mp4"
    output_path = os.path.join(CLIPS_DIR, output_file)

    vf_filters = []

    # Resolution override (expects "WIDTHxHEIGHT", e.g. "1080x1920")
    if template.get("resolution"):
        try:
            w, h = template["resolution"].split("x")
            vf_filters.append(f"scale={w}:{h}")
        except Exception:
            pass

    # Add multiple text overlays (drawtext)
    for txt in template.get("texts", []):
        text = txt.get("text", "")
        # if the text contains the placeholder {USER_TEXT} the UI will replace it beforehand
        escaped_text = text.replace("'", r"\'")
        draw = (
            "drawtext="
            f"text='{escaped_text}':"
            f"fontcolor={txt.get('fontcolor', 'white')}:"
            f"fontsize={txt.get('fontsize', 48)}:"
            f"x={txt.get('x', '(w-text_w)/2')}:"
            f"y={txt.get('y', 'h-150')}"
        )
        if txt.get("fontfile"):
            draw += f":fontfile={txt.get('fontfile')}"
        if txt.get("box"):
            draw += f":box=1:boxcolor={txt.get('boxcolor','black@0.5')}:boxborderw={txt.get('boxborder', 5)}"
        vf_filters.append(draw)

    vf_arg = ",".join(vf_filters) if vf_filters else None

    cmd = ["ffmpeg", "-y", "-ss", str(start), "-i", input_path, "-t", str(end - start)]
    if vf_arg:
        cmd += ["-vf", vf_arg]
    cmd += ["-c:v", "libx264", "-c:a", "aac", output_path]

    subprocess.run(cmd, check=True)

    # If session folder exists, copy the clip into session folder for tracking
    if session_folder:
        try:
            subprocess.run(["cp" if os.name != "nt" else "copy", output_path, os.path.join(session_folder, output_file)], shell=(os.name == "nt"))
        except Exception:
            # fallback to python copy
            import shutil
            shutil.copy(output_path, os.path.join(session_folder, output_file))

    return output_file


# ---------------------------
# 2. Merge Clips
# ---------------------------
def merge_clips(clips: List[str], output_name: str, session_id: str = None) -> str:
    """
    Concatenate clips using ffmpeg concat demuxer.
    clips: list of filenames located in CLIPS_DIR
    output_name: final output filename (e.g. 'merged.mp4')
    session_id: optional; will copy resulting file into session folder
    Returns final filename (relative to CLIPS_DIR)
    """
    # make list file
    list_file = os.path.join(CLIPS_DIR, f"merge_{uuid_name(output_name)}.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for c in clips:
            full = os.path.join(CLIPS_DIR, c)
            if not os.path.exists(full):
                raise FileNotFoundError(f"Clip not found: {full}")
            f.write(f"file '{full}'\n")

    final = f"{output_name}"
    output_path = os.path.join(CLIPS_DIR, final)

    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", output_path]
    subprocess.run(cmd, check=True)

    # copy to session folder if present
    if session_id:
        session_folder = _make_session_folder(session_id)
        try:
            import shutil
            shutil.copy(output_path, os.path.join(session_folder, final))
        except Exception:
            pass

    # cleanup list file
    try:
        os.remove(list_file)
    except Exception:
        pass

    return final


def uuid_name(s: str) -> str:
    """helper to create a safe list file name based on string"""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in s) + "_" + str(int(time.time()))


# ---------------------------
# 3. Cleanup Session
# ---------------------------
def cleanup_session(session_id: str, delete_clips: bool = True, delete_video: bool = True) -> bool:
    """
    Deletes files inside a session folder based on flags. Returns True on success.
    Session folder layout (created by session workflows):
      sessions/{session_id}/
         original.mp4
         clip_xxx.mp4
         transcript.txt
         merged_result.mp4
    """
    session_folder = os.path.join(SESSIONS_DIR, session_id)
    if not os.path.exists(session_folder):
        return False

    for fname in os.listdir(session_folder):
        full = os.path.join(session_folder, fname)
        try:
            if fname.endswith(".mp4"):
                # treat original vs clip indistinguishably; rely on flags
                if delete_clips or delete_video:
                    os.remove(full)
            elif fname.endswith(".txt") or fname.endswith(".vtt") or fname.endswith(".srt"):
                os.remove(full)
            else:
                # remove any other file
                os.remove(full)
        except Exception:
            pass

    # remove folder if empty
    try:
        if not os.listdir(session_folder):
            os.rmdir(session_folder)
    except Exception:
        pass

    return True
