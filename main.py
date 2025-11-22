import os
import re
import tempfile
from typing import Iterator

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from database import create_document, get_documents, db

# Import yt_dlp lazily inside endpoint to keep startup fast

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root():
    return {"message": "Assassin Stealth API running"}


@app.get("/api/hello")
def hello():
    return {"message": "Hello from the backend API!"}


@app.get("/test")
def test_database():
    """Test endpoint to check if database is available and accessible"""
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }

    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"

            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"

    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    # Check environment variables
    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"

    return response


# ----- Leaderboard models and endpoints -----
class ScoreIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=20)
    points: int = Field(..., ge=0)
    level: int = Field(1, ge=1)
    duration_ms: int = Field(..., ge=0)


@app.post("/api/score")
def submit_score(payload: ScoreIn):
    try:
        score_id = create_document("score", payload)
        return {"ok": True, "id": score_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/leaderboard")
def get_leaderboard(limit: int = 20):
    try:
        # Higher points first, then shorter duration
        docs = get_documents("score", {})
        docs_sorted = sorted(
            docs,
            key=lambda d: (-int(d.get("points", 0)), int(d.get("duration_ms", 1_000_000)))
        )
        top = [
            {
                "name": d.get("name", "Player"),
                "points": int(d.get("points", 0)),
                "level": int(d.get("level", 1)),
                "duration_ms": int(d.get("duration_ms", 0)),
            }
            for d in docs_sorted[:limit]
        ]
        return {"ok": True, "items": top}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ----- YouTube download endpoint (for content you own or have permission to download) -----
YOUTUBE_URL_RE = re.compile(r"^(https?://)?(www\.)?(youtube\.com|youtu\.be)/.+", re.IGNORECASE)


@app.get("/api/download")
async def download_youtube(url: str = Query(..., description="YouTube video URL")):
    # Basic validation
    if not YOUTUBE_URL_RE.match(url):
        raise HTTPException(status_code=400, detail="Please provide a valid YouTube URL")

    # Import yt_dlp lazily
    try:
        import yt_dlp  # type: ignore
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Downloader unavailable: {e}")

    tmpdir = tempfile.mkdtemp(prefix="yt_")

    # We'll try to get best MP4 if possible, otherwise best available
    ydl_opts = {
        "quiet": True,
        "noprogress": True,
        "outtmpl": os.path.join(tmpdir, "%(title).200B.%(ext)s"),
        "restrictfilenames": True,
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b/bestaudio+bv*",
        "merge_output_format": "mp4",
    }

    filename_holder = {"path": None}

    def run_download() -> str:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filepath = ydl.prepare_filename(info)
            # If post-processing merged to mp4, adjust extension
            if info.get("ext") != "mp4" and os.path.exists(filepath.rsplit(".", 1)[0] + ".mp4"):
                filepath = filepath.rsplit(".", 1)[0] + ".mp4"
            return filepath

    try:
        filepath = run_download()
        if not os.path.exists(filepath):
            raise HTTPException(status_code=500, detail="Failed to download video")
        filename = os.path.basename(filepath)

        def file_iterator(path: str) -> Iterator[bytes]:
            try:
                with open(path, "rb") as f:
                    while True:
                        chunk = f.read(1024 * 1024)
                        if not chunk:
                            break
                        yield chunk
            finally:
                # Cleanup
                try:
                    os.remove(path)
                except Exception:
                    pass
                try:
                    os.rmdir(tmpdir)
                except Exception:
                    pass

        headers = {
            "Content-Disposition": f"attachment; filename=\"{filename}\""
        }
        return StreamingResponse(file_iterator(filepath), media_type="video/mp4", headers=headers)

    except yt_dlp.utils.DownloadError as e:  # type: ignore
        raise HTTPException(status_code=400, detail=f"Download error: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
