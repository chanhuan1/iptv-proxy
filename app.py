import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from flask import Flask, Response, abort
from waitress import serve

logging.basicConfig(stream=sys.stdout, level=logging.INFO,
                    format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("iptv")

app = Flask(__name__)

M3U_PATH = "iptv_channels.m3u"
_m3u_mtime = 0.0
_channels: list[dict] = []          # ordered list of {slug, name, url, extras}
_RTSP: dict[str, str] = {}          # slug → rtsp_url


def _parse_m3u(path: str) -> list[dict]:
    """Parse an M3U file with RTSP URLs, auto-assign ch1/ch2... slugs."""
    channels: list[dict] = []
    with open(path) as f:
        lines = f.readlines()

    idx = 0
    for i, line in enumerate(lines):
        if not line.startswith("#EXTINF:"):
            continue
        extras: dict[str, str] = {}
        for attr in ["tvg-name", "tvg-logo", "group-title", "tvg-id"]:
            m = re.search(rf'{attr}="([^"]*)"', line)
            if m and m.group(1):
                extras[attr] = m.group(1)
        comma = line.rfind(",")
        display = line[comma + 1:].strip() if comma >= 0 else ""

        if i + 1 >= len(lines):
            continue
        url = lines[i + 1].strip()
        if not (url.startswith("rtsp://") or url.startswith("http://")):
            continue

        idx += 1
        channels.append({
            "slug": f"ch{idx}",
            "name": display or extras.get("tvg-name", ""),
            "url": url,
            "extras": extras,
        })
    return channels


def _reload_if_changed():
    global _m3u_mtime, _channels, _RTSP
    try:
        mtime = os.stat(M3U_PATH).st_mtime
    except OSError:
        return
    if mtime == _m3u_mtime:
        return
    _channels = _parse_m3u(M3U_PATH)
    _RTSP = {ch["slug"]: ch["url"] for ch in _channels}
    _m3u_mtime = mtime
    log.info("M3U reloaded: %d channels", len(_channels))


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
            log.info("Starting ffmpeg for %s", slug)
            _procs[slug] = _start_ffmpeg(slug, _RTSP[slug])


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
    log.info("Stopping ffmpeg for %s", slug)
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
    for i in range(50):
        if playlist.exists():
            content = playlist.read_bytes()
            if b"#EXTINF:" in content:
                return content
        if i > 0 and i % 5 == 0:
            with _lock:
                p = _procs.get(slug)
            if p and p.poll() is not None:
                return EMPTY
        time.sleep(0.2)
    if playlist.exists():
        return playlist.read_bytes()
    return EMPTY


def _generate_proxy_m3u() -> str:
    """Generate a proxy-format M3U from the parsed channels."""
    lines = ['#EXTM3U x-tvg-url="http://epg.51zmt.top:800/e.xml"']
    for ch in _channels:
        ext = ch["extras"]
        parts = ['#EXTINF:-1']
        if ext.get("tvg-name"):
            parts.append(f'tvg-name="{ext["tvg-name"]}"')
        logo = ext.get("tvg-logo", "")
        parts.append(f'tvg-logo="{logo}"')
        group = ext.get("group-title", "")
        parts.append(f'group-title="{group}"')
        parts.append(f',{ch["name"]}')
        lines.append(" ".join(parts))
        lines.append(f"http://NAS_IP:18888/{ch['slug']}/index.m3u8")
        lines.append("")
    return "\n".join(lines)


# ── Routes ──────────────────────────────────────────────


@app.route("/<slug>/<filename>")
def serve_file(slug: str, filename: str):
    _cleanup_idle()
    _reload_if_changed()

    if slug not in _RTSP:
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
    _reload_if_changed()
    return Response(_generate_proxy_m3u(), mimetype="audio/x-mpegurl")


@app.route("/health")
def health():
    with _lock:
        return {"active_streams": len(_procs)}


@app.route("/")
def index():
    _reload_if_changed()
    return f"<h1>IPTV Proxy</h1><p>{len(_channels)} channels</p>"


def _cleanup_loop():
    while True:
        time.sleep(30)
        _cleanup_idle()


threading.Thread(target=_cleanup_loop, daemon=True).start()

if __name__ == "__main__":
    _reload_if_changed()
    log.info("Starting IPTV Proxy — %d channels", len(_channels))
    log.info("Listening on http://0.0.0.0:18888")
    log.info("M3U: http://NAS_IP:18888/iptv.m3u")
    serve(app, host="0.0.0.0", port=18888)
