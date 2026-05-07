from pathlib import Path
import os
import sys
import threading
import webbrowser

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BASE_DIR = get_base_dir()
WEB_DIR = BASE_DIR
STATIC_DIR = WEB_DIR / "static"

INDEX_HTML = WEB_DIR / "index.html"
ADMIN_HTML = WEB_DIR / "admin.html"
CONFIG_JS = WEB_DIR / "config.js"

for required in (INDEX_HTML, ADMIN_HTML, CONFIG_JS, STATIC_DIR):
    if not required.exists():
        raise FileNotFoundError(f"Required frontend asset not found: {required}")

app = FastAPI()


@app.middleware("http")
async def no_cache_static(request, call_next):
    response = await call_next(request)
    if request.url.path.endswith((".html", ".js", ".css")) or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.get("/")
async def root():
    return FileResponse(INDEX_HTML)


@app.get("/index.html")
async def index_file():
    return FileResponse(INDEX_HTML)


@app.get("/admin.html")
async def admin_file():
    return FileResponse(ADMIN_HTML)


@app.get("/config.js")
async def config_file():
    return FileResponse(CONFIG_JS, media_type="application/javascript")


# /static/css/site.css -> WEB_DIR/static/css/site.css
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def open_browser():
    host = os.getenv("FRONT_HOST", "127.0.0.1")
    port = int(os.getenv("FRONT_PORT", "5500"))
    webbrowser.open(f"http://127.0.0.1:{port}")


if __name__ == "__main__":
    host = os.getenv("FRONT_HOST", "127.0.0.1")
    port = int(os.getenv("FRONT_PORT", "5500"))

    if os.getenv("FRONT_OPEN_BROWSER", "1") == "1":
        threading.Timer(1.0, open_browser).start()

    uvicorn.run(app, host=host, port=port)
