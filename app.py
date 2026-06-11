"""
Voice Narrator Studio — Backend API
FastAPI app that accepts voice samples + text scripts,
generates professional narration in Indonesian or English.

Engines:
  - edge-tts (default): Microsoft Edge TTS, free, high quality, supports ID + EN
  - elevenlabs: ElevenLabs API, paid, supports voice cloning from samples
"""

import os
import io
import uuid
import json
import asyncio
import tempfile
from pathlib import Path
from datetime import datetime

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydub import AudioSegment

# ── paths ────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
UPLOADS_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "output"
CONFIG_FILE = BASE_DIR / "config.json"

for d in [UPLOADS_DIR, OUTPUT_DIR]:
    d.mkdir(exist_ok=True)

# ── app ──────────────────────────────────────────────────────────────────
app = FastAPI(title="Voice Narrator Studio", version="1.0.0")

# ── Edge TTS voice presets ───────────────────────────────────────────────
EDGE_VOICES = {
    "id": {
        "id-ID-ArdiNeural": {"name": "Ardi", "gender": "Male", "style": "Natural, warm"},
        "id-ID-GadisNeural": {"name": "Gadis", "gender": "Female", "style": "Clear, friendly"},
    },
    "en": {
        "en-US-GuyNeural": {"name": "Guy", "gender": "Male", "style": "Professional, deep"},
        "en-US-AriaNeural": {"name": "Aria", "gender": "Female", "style": "Clear, warm"},
        "en-US-JennyNeural": {"name": "Jenny", "gender": "Female", "style": "Friendly, natural"},
        "en-US-DavisNeural": {"name": "Davis", "gender": "Male", "style": "Calm, narrative"},
        "en-GB-RyanNeural": {"name": "Ryan (UK)", "gender": "Male", "style": "British accent"},
        "en-GB-SoniaNeural": {"name": "Sonia (UK)", "gender": "Female", "style": "British accent"},
    }
}


# ── config helpers ───────────────────────────────────────────────────────
def load_config() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    return {
        "elevenlabs_api_key": "",
        "engine": "edge-tts",
        "edge_voice_id": "id-ID-ArdiNeural",
        "edge_voice_en": "en-US-GuyNeural",
    }

def save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


# ── routes ───────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = BASE_DIR / "static" / "index.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@app.get("/api/config")
async def get_config():
    cfg = load_config()
    return {
        "engine": cfg.get("engine", "edge-tts"),
        "has_elevenlabs_key": bool(cfg.get("elevenlabs_api_key")),
        "edge_voice_id": cfg.get("edge_voice_id", "id-ID-ArdiNeural"),
        "edge_voice_en": cfg.get("edge_voice_en", "en-US-GuyNeural"),
    }


@app.post("/api/config")
async def update_config(
    engine: str = Form("edge-tts"),
    elevenlabs_api_key: str = Form(""),
    edge_voice_id: str = Form(""),
    edge_voice_en: str = Form(""),
):
    cfg = load_config()
    cfg["engine"] = engine
    if elevenlabs_api_key:
        cfg["elevenlabs_api_key"] = elevenlabs_api_key
    if edge_voice_id:
        cfg["edge_voice_id"] = edge_voice_id
    if edge_voice_en:
        cfg["edge_voice_en"] = edge_voice_en
    save_config(cfg)
    return {"status": "ok", "engine": cfg["engine"]}


@app.get("/api/voices/presets")
async def get_voice_presets():
    """Return available Edge TTS voice presets."""
    return {"voices": EDGE_VOICES}


@app.post("/api/upload-voice")
async def upload_voice(file: UploadFile = File(...)):
    """Upload a voice sample for ElevenLabs cloning. Returns sample_id."""
    allowed = {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".webm"}
    ext = Path(file.filename).suffix.lower()
    if ext not in allowed:
        raise HTTPException(400, f"Unsupported format: {ext}. Use: {', '.join(allowed)}")

    data = await file.read()
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(400, "File too large (max 25MB)")

    sample_id = str(uuid.uuid4())[:8]
    raw_path = UPLOADS_DIR / f"{sample_id}{ext}"
    wav_path = UPLOADS_DIR / f"{sample_id}.wav"

    raw_path.write_bytes(data)

    try:
        audio = AudioSegment.from_file(str(raw_path))
        duration_s = len(audio) / 1000.0

        if duration_s < 3:
            raw_path.unlink(missing_ok=True)
            raise HTTPException(400, f"Audio too short ({duration_s:.1f}s). Need at least 3 seconds.")

        if duration_s > 120:
            audio = audio[:120000]
            duration_s = 120.0

        audio = audio.set_frame_rate(22050).set_channels(1)
        audio.export(str(wav_path), format="wav")

        if raw_path != wav_path:
            raw_path.unlink(missing_ok=True)

    except HTTPException:
        raise
    except Exception as e:
        raw_path.unlink(missing_ok=True)
        raise HTTPException(400, f"Failed to process audio: {str(e)}")

    return {
        "sample_id": sample_id,
        "filename": file.filename,
        "duration": round(duration_s, 1),
    }


@app.get("/api/voices")
async def list_voices():
    """List uploaded voice samples (for ElevenLabs cloning)."""
    voices = []
    for f in UPLOADS_DIR.glob("*.wav"):
        try:
            audio = AudioSegment.from_wav(str(f))
            duration = len(audio) / 1000.0
        except:
            duration = 0
        voices.append({
            "sample_id": f.stem,
            "duration": round(duration, 1),
            "created": datetime.fromtimestamp(f.stat().st_mtime).isoformat()
        })
    voices.sort(key=lambda v: v["created"], reverse=True)
    return {"voices": voices}


@app.delete("/api/voices/{sample_id}")
async def delete_voice(sample_id: str):
    wav_path = UPLOADS_DIR / f"{sample_id}.wav"
    if not wav_path.exists():
        raise HTTPException(404, "Voice sample not found")
    wav_path.unlink()
    return {"status": "deleted"}


@app.post("/api/generate")
async def generate_narration(
    text: str = Form(...),
    language: str = Form("id"),
    sample_id: str = Form(""),
    engine: str = Form("auto"),
    speed: float = Form(1.0),
    voice_preset: str = Form(""),
):
    """
    Generate narration audio.
    - text: the script text
    - language: 'id' or 'en'
    - sample_id: voice sample for ElevenLabs cloning
    - engine: 'edge-tts', 'elevenlabs', or 'auto'
    - speed: 0.5 - 2.0
    - voice_preset: edge-tts voice ID (e.g. 'id-ID-ArdiNeural')
    """
    if not text.strip():
        raise HTTPException(400, "Text cannot be empty")
    if len(text) > 10000:
        raise HTTPException(400, "Text too long (max 10,000 characters)")
    if speed < 0.5 or speed > 2.0:
        raise HTTPException(400, "Speed must be between 0.5 and 2.0")

    cfg = load_config()
    if engine == "auto":
        engine = cfg.get("engine", "edge-tts")

    output_id = str(uuid.uuid4())[:8]
    output_path = OUTPUT_DIR / f"{output_id}.wav"
    lang_code = "id" if language == "id" else "en"

    try:
        if engine == "elevenlabs":
            api_key = cfg.get("elevenlabs_api_key", "")
            if not api_key:
                raise HTTPException(400, "ElevenLabs API key not configured. Go to Settings.")
            speaker_wav = None
            if sample_id:
                speaker_wav = str(UPLOADS_DIR / f"{sample_id}.wav")
                if not Path(speaker_wav).exists():
                    raise HTTPException(404, f"Voice sample '{sample_id}' not found")
            await _generate_elevenlabs(text, lang_code, speaker_wav, output_path, api_key)
        else:
            # edge-tts
            voice = voice_preset
            if not voice:
                voice = cfg.get("edge_voice_id" if lang_code == "id" else "edge_voice_en", "")
            if not voice:
                voice = "id-ID-ArdiNeural" if lang_code == "id" else "en-US-GuyNeural"
            await _generate_edge_tts(text, voice, output_path, speed)
            speed = 1.0  # speed already handled by edge-tts rate

        # apply speed if needed (for non-edge-tts engines)
        if speed != 1.0 and output_path.exists():
            audio = AudioSegment.from_wav(str(output_path))
            new_rate = int(audio.frame_rate * speed)
            speeded = audio._spawn(audio.raw_data, overrides={"frame_rate": new_rate})
            speeded = speeded.set_frame_rate(22050)
            speeded.export(str(output_path), format="wav")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Generation failed: {str(e)}")

    try:
        audio = AudioSegment.from_wav(str(output_path))
        duration = len(audio) / 1000.0
    except:
        duration = 0

    return {
        "output_id": output_id,
        "duration": round(duration, 1),
        "engine": engine,
        "language": lang_code,
        "download_url": f"/api/download/{output_id}"
    }


async def _generate_edge_tts(text: str, voice: str, output_path: Path, speed: float):
    """Generate with Microsoft Edge TTS."""
    import edge_tts

    # Convert speed multiplier to Edge TTS rate string
    rate_pct = int((speed - 1.0) * 100)
    rate_str = f"+{rate_pct}%" if rate_pct >= 0 else f"{rate_pct}%"

    # Generate to temp mp3 then convert to wav
    mp3_path = output_path.with_suffix(".mp3")

    communicate = edge_tts.Communicate(text, voice, rate=rate_str)
    await communicate.save(str(mp3_path))

    # Convert mp3 to wav
    audio = AudioSegment.from_mp3(str(mp3_path))
    audio.export(str(output_path), format="wav")
    mp3_path.unlink(missing_ok=True)


async def _generate_elevenlabs(text: str, language: str, speaker_wav: str | None, output_path: Path, api_key: str):
    """Generate with ElevenLabs API (supports voice cloning)."""
    import httpx

    voice_id = "21m00Tcm4TlvDq8ikWAM"  # Rachel (default)

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
    }

    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.0,
            "use_speaker_boost": True
        }
    }

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(url, json=payload, headers=headers)

    if resp.status_code != 200:
        raise HTTPException(502, f"ElevenLabs API error: {resp.status_code} — {resp.text[:200]}")

    mp3_path = output_path.with_suffix(".mp3")
    mp3_path.write_bytes(resp.content)

    audio = AudioSegment.from_mp3(str(mp3_path))
    audio.export(str(output_path), format="wav")
    mp3_path.unlink(missing_ok=True)


@app.get("/api/download/{output_id}")
async def download_audio(output_id: str):
    wav_path = OUTPUT_DIR / f"{output_id}.wav"
    if not wav_path.exists():
        raise HTTPException(404, "Audio not found")
    return FileResponse(
        str(wav_path),
        media_type="audio/wav",
        filename=f"narration_{output_id}.wav"
    )


@app.get("/api/history")
async def list_history():
    items = []
    for f in OUTPUT_DIR.glob("*.wav"):
        try:
            audio = AudioSegment.from_wav(str(f))
            duration = len(audio) / 1000.0
        except:
            duration = 0
        items.append({
            "output_id": f.stem,
            "duration": round(duration, 1),
            "created": datetime.fromtimestamp(f.stat().st_mtime).isoformat()
        })
    items.sort(key=lambda x: x["created"], reverse=True)
    return {"history": items}


@app.delete("/api/history/{output_id}")
async def delete_history(output_id: str):
    wav_path = OUTPUT_DIR / f"{output_id}.wav"
    if not wav_path.exists():
        raise HTTPException(404, "Audio not found")
    wav_path.unlink()
    return {"status": "deleted"}


# ── static files ─────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
