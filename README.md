# 🎙️ Voice Narrator Studio

A web-based tool for generating professional narration audio in Indonesian and English. Powered by Microsoft Edge TTS with support for voice cloning via ElevenLabs.

## Features

- **Dual Language Support** — Indonesian (2 voices: Ardi & Gadis) and English (6 voices: Guy, Aria, Jenny, Davis, Ryan, Sonia)
- **Voice Cloning** — Upload your own voice sample and generate narration that sounds like you (via ElevenLabs API)
- **Speed Control** — Adjust narration speed from 0.5x to 2.0x
- **Dark Theme UI** — Clean, modern interface
- **Audio History** — All generated audio is saved and accessible
- **Download** — Export audio as WAV files
- **Free** — Core TTS engine uses Edge TTS (Microsoft), completely free

## Quick Start

### Prerequisites

- Python 3.11+
- pip

### Installation

```bash
git clone https://github.com/helmirfansah/voice-narrator-studio.git
cd voice-narrator-studio
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux/Mac
pip install -r requirements.txt
```

### Run

```bash
python app.py
```

Or on Windows, double-click `start.bat`.

Open your browser at **http://localhost:8765**

## Usage

1. **Write your script** — Type or paste your narration text
2. **Select language & voice** — Choose Indonesian or English, then pick a voice
3. **Adjust speed** — Use the slider to control narration pace
4. **Generate** — Click "Generate Narasi" and wait for the audio
5. **Play & Download** — Listen to the result and download the WAV file

### Voice Cloning (ElevenLabs)

1. Get an API key from [ElevenLabs](https://elevenlabs.io)
2. Go to Settings tab → paste your API key → Save
3. Upload a voice sample (6-30 seconds of clear speech)
4. Select your cloned voice in the Generate tab

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python FastAPI |
| TTS Engine | Edge TTS (Microsoft) |
| Voice Cloning | ElevenLabs API |
| Frontend | HTML/CSS/JavaScript |
| Audio Processing | pydub |

## Project Structure

```
voice-narrator-studio/
├── app.py              # FastAPI backend
├── static/
│   └── index.html      # Frontend UI
├── start.bat           # Windows launcher
├── requirements.txt    # Python dependencies
├── uploads/            # Voice samples (gitignored)
├── output/             # Generated audio (gitignored)
└── README.md
```

## License

MIT

## Author

[@helmirfansah](https://github.com/helmirfansah)
