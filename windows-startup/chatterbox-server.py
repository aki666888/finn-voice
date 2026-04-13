"""
Chatterbox TTS Server for Finn Voice
Port: 8004, Bind: 0.0.0.0

Endpoints:
  GET  /health -> status + GPU info
  POST /tts -> generate speech (Chatterbox native format)
  POST /tts/speak -> convenience wrapper (finn-voice format)
  GET  /tts/voices -> list available voices
  GET  /api/ui/initial-data -> voice list for UI (compatibility)
"""

import os
import io
import sys
import torch
import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
from loguru import logger

logger.remove()
logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | <level>{message}</level>", level="DEBUG", colorize=True)
logger.add("D:/finn/finn-voice/logs/chatterbox_{time:YYYY-MM-DD}.log", rotation="1 day", retention="7 days", level="DEBUG")

CUDA_DEVICE = os.environ.get("CHATTERBOX_CUDA", "cuda:1")
VOICE_DIR = os.environ.get("CHATTERBOX_VOICES", "D:/finn/finn-voice/voices")

app = FastAPI(title="Finn Chatterbox TTS", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# Load model at startup
logger.info(f"Loading Chatterbox TTS on {CUDA_DEVICE}...")
model = None
try:
    from chatterbox.tts import ChatterboxTTS
    model = ChatterboxTTS.from_pretrained(device=CUDA_DEVICE)
    logger.info(f"Chatterbox loaded on {CUDA_DEVICE}")
except Exception as e:
    logger.error(f"Failed to load Chatterbox: {e}")
    logger.warning("TTS will be unavailable - install chatterbox-tts package")

# Scan for voice WAV files
def get_voices():
    voices = ["default"]
    if os.path.isdir(VOICE_DIR):
        for f in os.listdir(VOICE_DIR):
            if f.endswith(".wav"):
                voices.append(f)
    return voices

class TTSRequest(BaseModel):
    text: str
    voice_mode: str = "predefined"
    predefined_voice_id: str = "default"
    speed_factor: float = 1.0
    exaggeration: float = 0.7
    temperature: float = 0.7
    output_format: str = "wav"

class SpeakRequest(BaseModel):
    text: str
    voice: str = "default"
    speed: float = 1.0

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "gpu": CUDA_DEVICE,
        "model_loaded": model is not None,
        "voices": get_voices()
    }

@app.get("/tts/voices")
async def tts_voices():
    return {"voices": get_voices()}

@app.get("/api/ui/initial-data")
async def ui_initial_data():
    return {"voices": get_voices(), "status": "ok"}

@app.post("/tts")
async def tts(request: TTSRequest):
    if model is None:
        raise HTTPException(status_code=503, detail="Chatterbox model not loaded")

    import time
    start = time.time()
    logger.info(f"TTS request: text_len={len(request.text)} voice={request.predefined_voice_id}")

    try:
        # Generate speech
        audio_tensor = model.generate(
            request.text,
            exaggeration=request.exaggeration,
            temperature=request.temperature
        )

        # Convert to numpy
        if isinstance(audio_tensor, torch.Tensor):
            audio_np = audio_tensor.cpu().numpy().squeeze()
        else:
            audio_np = np.array(audio_tensor).squeeze()

        # Apply speed factor
        if request.speed_factor != 1.0:
            import scipy.signal
            new_len = int(len(audio_np) / request.speed_factor)
            audio_np = scipy.signal.resample(audio_np, new_len)

        # Write WAV to buffer
        buf = io.BytesIO()
        sf.write(buf, audio_np, 24000, format="WAV")
        buf.seek(0)

        elapsed = (time.time() - start) * 1000
        logger.info(f"TTS done in {elapsed:.0f}ms, audio_len={len(audio_np)}")

        return StreamingResponse(buf, media_type="audio/wav")
    except Exception as e:
        logger.error(f"TTS error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tts/speak")
async def tts_speak(request: SpeakRequest):
    """Convenience endpoint matching finn-voice format"""
    tts_req = TTSRequest(
        text=request.text,
        predefined_voice_id=request.voice,
        speed_factor=request.speed
    )
    return await tts(tts_req)

if __name__ == "__main__":
    os.makedirs("D:/finn/finn-voice/logs", exist_ok=True)
    os.makedirs(VOICE_DIR, exist_ok=True)
    logger.info(f"Starting Chatterbox TTS server on 0.0.0.0:8004")
    uvicorn.run(app, host="0.0.0.0", port=8004, log_level="info")
