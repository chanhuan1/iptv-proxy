import json
import os
import re
import signal
import subprocess
import threading
import time
from pathlib import Path

from flask import Flask, Response, abort
from waitress import serve

app = Flask(__name__)

# RTSP URL database: slug → {name, url}
with open("channels.json") as f:
    RTSP_DB = {f"ch{ch['id']}": ch for ch in json.load(f)}

# Active channels parsed from user-editable M3U
M3U_PATH = "iptv_channels.m3u"
_m3u_mtime = 0.0
_active_slugs: set[str] = set()


def _parse_m3u_slugs(path: str) -> set[str]:
    slugs: set[str] = set()
    with open(path) as f:
        for line in f:
            m = re.search(r"/(ch\d+)/index\.m3u8", line)
            if m:
                slugs.add(m.group(1))
    return slugs


def _get_active_slugs() -> set[str]:
    global _m3u_mtime, _active_slugs
    try:
        mtime = os.stat(M3U_PATH).st_mtime
    except OSError:
        return _active_slugs
    if mtime != _m3u_mtime:
        _active_slugs = _parse_m3u_slugs(M3U_PATH)
        _m3u_mtime = mtime
    return _active_slugs


HLS_DIR = Path("/tmp/hls")
HLS_DIR.mkdir(parents=True, exist_ok=True)

_lock = threading.Lock()
_procs: dict[str, subprocess.Popen] = {}
_last_access: dict[str, float] = {}

IDLE_TIMEOUT = 30
MAX_CONCURRENT = 3

EMPTY = b"#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:0\n"


def _start_ffmpeg(slug: str, rtsp_url: str):
    out_dir = HLS_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in out_dir.glob("*.ts"):
        f.unlink()
    for f in out_dir.glob("*.m3u8"):
        f.unlink()

    playlist = out_dir / "index.m3u8"
    cmd = [
        "ffmpeg",
        "-loglevel", "error",
        "-rtsp_transport", "tcp",
        "-rtsp_flags", "prefer_tcp",
        "-timeout", "5000000",
        "-stimeout", "3000000",
        "-analyzeduration", "500K",
        "-probesize", "500K",
        "-i", rtsp_url,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "20",
        "-threads", "1",
        "-c:a", "aac",
        "-b:a", "64k",
        "-f", "hls",
        "-hls_time", "2",
        "-hls_init_time", "1",
        "-hls_list_size", "10",
        "-hls_flags", "delete_segments+append_list+omit_endlist",
        "-hls_segment_filename", str(out_dir / "seg_%03d.ts"),
        str(playlist),
    ]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)


def _touch(slug: str):
    with _lock:
        _last_access[slug] = time.time()
        if slug not in _procs or _procs[slug].poll() is not None:
            if slug in _procs:
                _procs.pop(slug)
            while len(_procs) >= MAX_CONCURRENT:
                oldest = min((s for s in _last_access if s in _procs), key=lambda s: _last_access[s])
                _kill_proc(oldest)
            _procs[slug] = _start_ffmpeg(slug, RTSP_DB[slug]["url"])


def _cleanup_idle():
    now = time.time()
    with _lock:
        to_kill = [s for s in _procs if s in _last_access and now - _last_access[s] > IDLE_TIMEOUT]
        for slug in to_kill:
            _kill_proc(slug)


def _kill_proc(slug: str):
    p = _procs.pop(slug, None)
    _last_access.pop(slug, None)
    if p is None:
        return
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        p.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass


def _wait_playlist(slug: str) -> bytes:
    playlist = HLS_DIR / slug / "index.m3u8"
    for i in range(50):  # 10s max
        if playlist.exists():
            content = playlist.read_bytes()
            if b"#EXTINF:" in content:
                return content
        # If ffmpeg died, source is dead — stop waiting
        if i > 0 and i % 5 == 0:
            with _lock:
                p = _procs.get(slug)
            if p and p.poll() is not None:
                return EMPTY
        time.sleep(0.2)
    if playlist.exists():
        return playlist.read_bytes()
    return EMPTY


@app.route("/<slug>/<filename>")
def serve_file(slug: str, filename: str):
    _cleanup_idle()

    if slug not in _get_active_slugs() or slug not in RTSP_DB:
        abort(404)

    _touch(slug)

    if filename == "index.m3u8":
        return Response(_wait_playlist(slug), mimetype="application/vnd.apple.mpegurl")

    path = HLS_DIR / slug / filename
    if path.exists():
        return Response(path.read_bytes(), mimetype="video/mp2t")

    abort(404)


@app.route("/iptv.m3u")
def serve_m3u():
    try:
        return Response(open(M3U_PATH).read(), mimetype="audio/x-mpegurl")
    except OSError:
        abort(404)


@app.route("/health")
def health():
    with _lock:
        return {"active_streams": len(_procs)}


@app.route("/")
def index():
    return f"<h1>IPTV Proxy</h1><p>{len(_get_active_slugs())} active / {len(RTSP_DB)} total</p>"


def _cleanup_loop():
    while True:
        time.sleep(30)
        _cleanup_idle()


threading.Thread(target=_cleanup_loop, daemon=True).start()

if __name__ == "__main__":
    serve(app, host="0.0.0.0", port=18888)
