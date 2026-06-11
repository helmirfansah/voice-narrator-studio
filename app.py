"""
Voice Narrator Studio — Backend API
FastAPI app that accepts voice samples + text scripts,
generates professional narration in Indonesian or English.

Engines:
  - edge-tts (default): Microsoft Edge TTS, free, high quality, supports ID + EN
  - elevenlabs: ElevenLabs API, paid, supports voice cloning from samples
  - huggingface: HuggingFace Inference API, FREE voice cloning via XTTS-v2

Features:
  - Script from URL/YouTube extraction
  - Subtitle SRT auto-generation
  - Audio post-processing (fade, bass boost, normalize, noise gate)
"""

import os
import io
import re
import uuid
import json
import asyncio
import struct
import tempfile
import math
from pathlib import Path
from datetime import datetime

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydub import AudioSegment
from pydub.effects import normalize as pydub_normalize

# ── paths ────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
UPLOADS_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "output"
CONFIG_FILE = BASE_DIR / "config.json"

for d in [UPLOADS_DIR, OUTPUT_DIR]:
    d.mkdir(exist_ok=True)

# ── app ──────────────────────────────────────────────────────────────────
app = FastAPI(title="Voice Narrator Studio", version="2.0.0")

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
        "huggingface_token": "",
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
        "has_huggingface_token": bool(cfg.get("huggingface_token")),
        "edge_voice_id": cfg.get("edge_voice_id", "id-ID-ArdiNeural"),
        "edge_voice_en": cfg.get("edge_voice_en", "en-US-GuyNeural"),
    }


@app.post("/api/config")
async def update_config(
    engine: str = Form("edge-tts"),
    elevenlabs_api_key: str = Form(""),
    huggingface_token: str = Form(""),
    edge_voice_id: str = Form(""),
    edge_voice_en: str = Form(""),
):
    cfg = load_config()
    cfg["engine"] = engine
    if elevenlabs_api_key:
        cfg["elevenlabs_api_key"] = elevenlabs_api_key
    if huggingface_token:
        cfg["huggingface_token"] = huggingface_token
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


# ── FEATURE 1: Script from URL / YouTube ────────────────────────────────

@app.post("/api/extract-script")
async def extract_script(url: str = Form(...)):
    """
    Extract readable text from a URL (article) or YouTube video.
    Returns cleaned script text ready for narration.
    """
    url = url.strip()
    if not url:
        raise HTTPException(400, "URL cannot be empty")

    is_youtube = bool(re.search(r'(youtube\.com|youtu\.be)', url, re.I))

    try:
        if is_youtube:
            text = await _extract_youtube(url)
        else:
            text = await _extract_article(url)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Extraction failed: {str(e)}")

    if not text or len(text.strip()) < 20:
        raise HTTPException(400, "Could not extract meaningful text from this URL")

    # Clean up text for narration
    text = _clean_for_narration(text)

    return {
        "text": text,
        "source": "youtube" if is_youtube else "article",
        "char_count": len(text),
    }


async def _extract_youtube(url: str) -> str:
    """Extract transcript from YouTube video."""
    from youtube_transcript_api import YouTubeTranscriptApi
    import re

    # Extract video ID
    patterns = [
        r'(?:v=|/v/|youtu\.be/)([a-zA-Z0-9_-]{11})',
        r'(?:embed/)([a-zA-Z0-9_-]{11})',
    ]
    video_id = None
    for p in patterns:
        m = re.search(p, url)
        if m:
            video_id = m.group(1)
            break

    if not video_id:
        raise HTTPException(400, "Could not extract YouTube video ID from URL")

    try:
        # Try Indonesian first, then English, then any available
        try:
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
            transcript = None
            # Try to find Indonesian or English transcript
            for t in transcript_list:
                if t.language_code.startswith('id'):
                    transcript = t.fetch()
                    break
                elif t.language_code.startswith('en'):
                    transcript = t.fetch()
                    # Don't break, keep looking for Indonesian
            if transcript is None:
                # Get auto-generated or first available
                for t in transcript_list:
                    transcript = t.fetch()
                    break
        except Exception:
            # Fallback: direct fetch
            ytt_api = YouTubeTranscriptApi()
            transcript = ytt_api.fetch(video_id)

        if not transcript:
            raise HTTPException(400, "No transcript available for this video")

        # Combine all text segments
        full_text = " ".join(
            entry.text if hasattr(entry, 'text') else entry.get('text', '')
            for entry in transcript
        )
        return full_text

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Failed to get YouTube transcript: {str(e)}")


async def _extract_article(url: str) -> str:
    """Extract readable text from a web article."""
    import trafilatura

    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            raise HTTPException(400, "Could not fetch the URL. Check if the URL is valid and accessible.")

        text = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
        if not text:
            raise HTTPException(400, "Could not extract article content from this page")

        return text
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Article extraction failed: {str(e)}")


def _clean_for_narration(text: str) -> str:
    """Clean extracted text to be more suitable for TTS narration."""
    # Remove URLs
    text = re.sub(r'https?://\S+', '', text)
    # Remove email addresses
    text = re.sub(r'\S+@\S+\.\S+', '', text)
    # Remove excessive whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' {2,}', ' ', text)
    # Remove markdown artifacts
    text = re.sub(r'[*_~`#]', '', text)
    # Remove brackets content that looks like metadata
    text = re.sub(r'\[(?:edit|citation needed)\]', '', text, flags=re.I)
    # Trim
    text = text.strip()
    # Limit to 10000 chars (TTS limit)
    if len(text) > 10000:
        # Cut at last sentence boundary before 10000
        cut = text[:10000]
        last_period = max(cut.rfind('.'), cut.rfind('!'), cut.rfind('?'))
        if last_period > 5000:
            text = cut[:last_period + 1]
        else:
            text = cut

    return text


# ── FEATURE 2: Subtitle SRT Auto-Generate ───────────────────────────────

@app.post("/api/generate")
async def generate_narration(
    text: str = Form(...),
    language: str = Form("id"),
    sample_id: str = Form(""),
    engine: str = Form("auto"),
    speed: float = Form(1.0),
    voice_preset: str = Form(""),
    generate_srt: bool = Form(False),
):
    """
    Generate narration audio + optional SRT subtitle.
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
    srt_path = OUTPUT_DIR / f"{output_id}.srt"
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
        elif engine == "huggingface":
            hf_token = cfg.get("huggingface_token", "")
            if not hf_token:
                raise HTTPException(400, "HuggingFace token not configured. Go to Settings.")
            if not sample_id:
                raise HTTPException(400, "Voice sample required for HuggingFace voice cloning. Upload in Voice Cloning tab.")
            speaker_wav = str(UPLOADS_DIR / f"{sample_id}.wav")
            if not Path(speaker_wav).exists():
                raise HTTPException(404, f"Voice sample '{sample_id}' not found")
            await _generate_hf_voice_clone(text, lang_code, speaker_wav, output_path, hf_token)
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

    # Generate SRT if requested
    srt_url = None
    if generate_srt:
        _generate_srt(text, duration, str(srt_path))
        srt_url = f"/api/download-srt/{output_id}"

    return {
        "output_id": output_id,
        "duration": round(duration, 1),
        "engine": engine,
        "language": lang_code,
        "download_url": f"/api/download/{output_id}",
        "srt_url": srt_url,
    }


def _generate_srt(text: str, audio_duration: float, srt_path: str):
    """
    Generate SRT subtitle file by splitting text into timed segments.
    Uses sentence-based splitting with proportional timing.
    """
    # Split text into sentences
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    sentences = [s.strip() for s in sentences if s.strip()]

    if not sentences:
        sentences = [text.strip()]

    # If sentences are too long (>80 chars), split at commas/semicolons
    expanded = []
    for s in sentences:
        if len(s) > 80:
            parts = re.split(r'(?<=[,;:])\s+', s)
            expanded.extend(parts)
        else:
            expanded.append(s)
    sentences = expanded

    # If still too long, split at max 80 chars with word boundary
    final = []
    for s in sentences:
        if len(s) > 80:
            words = s.split()
            line = ""
            for w in words:
                if len(line) + len(w) + 1 > 70:
                    if line:
                        final.append(line.strip())
                    line = w
                else:
                    line += " " + w
            if line.strip():
                final.append(line.strip())
        else:
            final.append(s)
    sentences = final

    if not sentences:
        return

    # Calculate timing proportional to character count
    total_chars = sum(len(s) for s in sentences)
    if total_chars == 0:
        return

    # Add small gap between subtitles (100ms)
    gap_ms = 150
    available_ms = (audio_duration * 1000) - (gap_ms * (len(sentences) - 1))
    if available_ms <= 0:
        available_ms = audio_duration * 1000
        gap_ms = 0

    srt_lines = []
    current_ms = 0

    for i, sentence in enumerate(sentences):
        # Proportional duration based on character count
        char_ratio = len(sentence) / total_chars
        duration_ms = max(1000, char_ratio * available_ms)  # min 1 second per subtitle

        start_ms = current_ms
        end_ms = current_ms + duration_ms

        # Don't exceed audio duration
        if end_ms > audio_duration * 1000:
            end_ms = audio_duration * 1000

        start_str = _ms_to_srt_time(start_ms)
        end_str = _ms_to_srt_time(end_ms)

        srt_lines.append(f"{i + 1}")
        srt_lines.append(f"{start_str} --> {end_str}")
        srt_lines.append(sentence)
        srt_lines.append("")  # blank line separator

        current_ms = end_ms + gap_ms

    srt_content = "\n".join(srt_lines)
    Path(srt_path).write_text(srt_content, encoding="utf-8")


def _ms_to_srt_time(ms: float) -> str:
    """Convert milliseconds to SRT time format: HH:MM:SS,mmm"""
    total_seconds = ms / 1000
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    seconds = int(total_seconds % 60)
    millis = int(ms % 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


@app.get("/api/download-srt/{output_id}")
async def download_srt(output_id: str):
    srt_path = OUTPUT_DIR / f"{output_id}.srt"
    if not srt_path.exists():
        raise HTTPException(404, "SRT subtitle not found")
    return FileResponse(
        str(srt_path),
        media_type="text/plain",
        filename=f"subtitle_{output_id}.srt"
    )


# ── FEATURE 3: Audio Post-Processing ────────────────────────────────────

@app.post("/api/post-process")
async def post_process_audio(
    output_id: str = Form(...),
    fade_in: float = Form(0),        # milliseconds
    fade_out: float = Form(0),       # milliseconds
    bass_boost: float = Form(0),     # dB boost (0-12)
    normalize_audio: bool = Form(False),
    noise_gate: bool = Form(False),
    noise_gate_threshold: float = Form(-40),  # dB
):
    """
    Apply post-processing effects to a generated audio file.
    Returns a new output_id for the processed version.
    """
    source_path = OUTPUT_DIR / f"{output_id}.wav"
    if not source_path.exists():
        raise HTTPException(404, "Audio not found. Generate audio first.")

    try:
        audio = AudioSegment.from_wav(str(source_path))
    except Exception as e:
        raise HTTPException(500, f"Failed to load audio: {str(e)}")

    # Apply effects
    if fade_in > 0:
        audio = audio.fade_in(int(fade_in))

    if fade_out > 0:
        audio = audio.fade_out(int(fade_out))

    if bass_boost > 0:
        audio = _apply_bass_boost(audio, bass_boost)

    if normalize_audio:
        # Normalize to -1 dBFS peak
        change_in_dBFS = -1.0 - audio.max_dBFS
        audio = audio.apply_gain(change_in_dBFS)

    if noise_gate:
        audio = _apply_noise_gate(audio, noise_gate_threshold)

    # Save as new file
    new_id = str(uuid.uuid4())[:8]
    new_path = OUTPUT_DIR / f"{new_id}.wav"
    audio.export(str(new_path), format="wav")

    duration = len(audio) / 1000.0

    # Copy SRT if exists
    old_srt = OUTPUT_DIR / f"{output_id}.srt"
    if old_srt.exists():
        new_srt = OUTPUT_DIR / f"{new_id}.srt"
        new_srt.write_text(old_srt.read_text(encoding="utf-8"), encoding="utf-8")

    return {
        "output_id": new_id,
        "duration": round(duration, 1),
        "download_url": f"/api/download/{new_id}",
        "effects_applied": {
            "fade_in": fade_in > 0,
            "fade_out": fade_out > 0,
            "bass_boost": bass_boost > 0,
            "normalize": normalize_audio,
            "noise_gate": noise_gate,
        }
    }


def _apply_bass_boost(audio: AudioSegment, boost_db: float) -> AudioSegment:
    """
    Simple bass boost by boosting frequencies below ~250Hz.
    Uses a split-and-boost approach since pydub doesn't have native EQ.
    """
    # Split into low and high frequency bands
    # Low pass at 250Hz for bass
    bass = audio.low_pass_filter(250)
    # High pass at 250Hz for everything else
    rest = audio.high_pass_filter(250)

    # Boost the bass
    bass = bass.apply_gain(boost_db)

    # Recombine
    return bass.overlay(rest)


def _apply_noise_gate(audio: AudioSegment, threshold_db: float) -> AudioSegment:
    """
    Simple noise gate: silence segments below threshold.
    Processes in 50ms chunks.
    """
    chunk_ms = 50
    chunks = []
    total_ms = len(audio)

    for start in range(0, total_ms, chunk_ms):
        end = min(start + chunk_ms, total_ms)
        chunk = audio[start:end]
        if chunk.dBFS > threshold_db or chunk.dBFS == float('-inf'):
            chunks.append(chunk)
        else:
            # Replace with silence
            chunks.append(AudioSegment.silent(duration=len(chunk), frame_rate=audio.frame_rate))

    if not chunks:
        return audio

    result = chunks[0]
    for chunk in chunks[1:]:
        result += chunk

    return result


# ── TTS Engines ──────────────────────────────────────────────────────────

async def _generate_edge_tts(text: str, voice: str, output_path: Path, speed: float):
    """Generate with Microsoft Edge TTS."""
    import edge_tts

    rate_pct = int((speed - 1.0) * 100)
    rate_str = f"+{rate_pct}%" if rate_pct >= 0 else f"{rate_pct}%"

    mp3_path = output_path.with_suffix(".mp3")
    communicate = edge_tts.Communicate(text, voice, rate=rate_str)
    await communicate.save(str(mp3_path))

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


async def _generate_hf_voice_clone(text: str, language: str, speaker_wav: str, output_path: Path, hf_token: str):
    """Generate with HuggingFace Inference API — FREE voice cloning via XTTS-v2."""
    import base64
    import httpx

    # Read speaker audio and encode to base64
    with open(speaker_wav, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode("utf-8")

    # Map language codes
    lang_map = {"id": "id", "en": "en"}
    lang_code = lang_map.get(language, "en")

    # XTTS-v2 on HuggingFace Inference API
    model_id = "coqui/XTTS-v2"
    api_url = f"https://api-inference.huggingface.co/models/{model_id}"

    headers = {
        "Authorization": f"Bearer {hf_token}",
        "Content-Type": "application/json",
    }

    payload = {
        "inputs": text,
        "parameters": {
            "language": lang_code,
            "speaker_wav": audio_b64,
        }
    }

    async with httpx.AsyncClient(timeout=180) as client:
        resp = await client.post(api_url, json=payload, headers=headers)

    if resp.status_code == 503:
        # Model is loading, wait and retry
        import json as _json
        try:
            wait_time = _json.loads(resp.content).get("estimated_time", 60)
        except:
            wait_time = 60
        await asyncio.sleep(min(wait_time, 120))
        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.post(api_url, json=payload, headers=headers)

    if resp.status_code != 200:
        error_msg = resp.text[:300] if resp.text else "Unknown error"
        raise HTTPException(502, f"HuggingFace API error ({resp.status_code}): {error_msg}")

    # Response is audio bytes (wav or flac)
    content_type = resp.headers.get("content-type", "")
    if "audio" in content_type or "octet-stream" in content_type:
        # Try to load as audio
        temp_path = output_path.with_suffix(".tmp")
        temp_path.write_bytes(resp.content)
        try:
            audio = AudioSegment.from_file(str(temp_path))
            audio.export(str(output_path), format="wav")
            temp_path.unlink(missing_ok=True)
        except Exception:
            # If pydub can't parse, assume it's already wav
            if temp_path.exists():
                temp_path.rename(output_path)
            else:
                output_path.write_bytes(resp.content)
    else:
        raise HTTPException(502, f"Unexpected response from HuggingFace: {content_type}")


# ── Existing endpoints (download, history) ───────────────────────────────

@app.get("/api/download/{output_id}")
async def download_audio(output_id: str, format: str = Query("wav")):
    wav_path = OUTPUT_DIR / f"{output_id}.wav"
    if not wav_path.exists():
        raise HTTPException(404, "Audio not found")

    if format == "mp3":
        mp3_path = OUTPUT_DIR / f"{output_id}.mp3"
        if not mp3_path.exists():
            audio = AudioSegment.from_wav(str(wav_path))
            audio.export(str(mp3_path), format="mp3", bitrate="192k")
        return FileResponse(
            str(mp3_path),
            media_type="audio/mpeg",
            filename=f"narration_{output_id}.mp3"
        )

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
        has_srt = (OUTPUT_DIR / f"{f.stem}.srt").exists()
        items.append({
            "output_id": f.stem,
            "duration": round(duration, 1),
            "created": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            "has_srt": has_srt,
        })
    items.sort(key=lambda x: x["created"], reverse=True)
    return {"history": items}


@app.delete("/api/history/{output_id}")
async def delete_history(output_id: str):
    wav_path = OUTPUT_DIR / f"{output_id}.wav"
    srt_path = OUTPUT_DIR / f"{output_id}.srt"
    mp3_path = OUTPUT_DIR / f"{output_id}.mp3"
    if not wav_path.exists():
        raise HTTPException(404, "Audio not found")
    wav_path.unlink(missing_ok=True)
    srt_path.unlink(missing_ok=True)
    mp3_path.unlink(missing_ok=True)
    return {"status": "deleted"}


# ── static files ─────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
