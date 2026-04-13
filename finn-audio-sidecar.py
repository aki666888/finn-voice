"""
Finn Audio Sidecar - GPU Voice Processing

GPU (CUDA configurable):
  - faster-whisper large-v3 (STT)
  - Chatterbox TTS (external proxy)

CPU:
  - Silero VAD
  - OpenWakeWord (configurable)

Config: D:/finn/finn-voice/config/finn-voice-config.json
Logs:   D:/finn/finn-voice/logs/
"""

import os
import io
import json
import time
import uuid
import base64
import asyncio
import sys
import traceback

import torch
import numpy as np
import soundfile as sf
import httpx
from fastapi import FastAPI, WebSocket, HTTPException, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

# ============================================
# LOGGING - loguru comprehensive
# ============================================
from loguru import logger

logger.remove()  # Remove default handler
logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{function}</cyan> | <level>{message}</level>",
    level="DEBUG",
    colorize=True
)
logger.add(
    "D:/finn/finn-voice/logs/sidecar_{time:YYYY-MM-DD}.log",
    rotation="1 day",
    retention="7 days",
    level="DEBUG",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}"
)

logger.info("Finn Audio Sidecar starting up...")

# ============================================
# CONFIG - Read from JSON file
# ============================================
CONFIG_PATH = "D:/finn/finn-voice/config/finn-voice-config.json"

def load_config():
    """Read config from JSON file. Fails hard if missing."""
    logger.info("Loading config from {}", CONFIG_PATH)
    if not os.path.exists(CONFIG_PATH):
        logger.error("Config file not found: {}", CONFIG_PATH)
        raise RuntimeError(f"Config file not found: {CONFIG_PATH} - create it before running")
    try:
        with open(CONFIG_PATH, "r") as f:
            config = json.load(f)
        logger.info("Config loaded successfully: {} keys", len(config))
        logger.debug("Config contents: {}", json.dumps(config, indent=2))
        return config
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON in config file: {}", e)
        raise RuntimeError(f"Invalid JSON in {CONFIG_PATH}: {e}")

CONFIG = load_config()

# Extract config values
CUDA_DEVICE = CONFIG.get("cuda_device", "cuda:0")
STT_MODEL = CONFIG.get("stt_model", "faster-whisper")
TTS_MODEL = CONFIG.get("tts_model", "chatterbox")
CHATTERBOX_URL = CONFIG.get("chatterbox_url", "http://localhost:8004")
VAD_SENSITIVITY = float(CONFIG.get("vad_sensitivity", 0.5))
VAD_SILENCE_MS = int(CONFIG.get("vad_silence_ms", 1500))
OWW_WAKE_MODEL = CONFIG.get("wake_word_start", "hey_jarvis")
OWW_HALT_MODEL = CONFIG.get("wake_word_halt_acoustic", "")
OWW_THRESHOLD = float(CONFIG.get("oww_threshold", 0.5))
SIDECAR_PORT = int(CONFIG.get("sidecar_port", 8082))
TTS_VOICE_DEFAULT = CONFIG.get("tts_voice", "Elena.wav")

logger.info("CUDA device: {}", CUDA_DEVICE)
logger.info("STT model: {}", STT_MODEL)
logger.info("TTS model: {} (proxy to {})", TTS_MODEL, CHATTERBOX_URL)
logger.info("VAD sensitivity: {}, silence: {}ms", VAD_SENSITIVITY, VAD_SILENCE_MS)
logger.info("Wake word start: '{}', halt: '{}'", OWW_WAKE_MODEL, OWW_HALT_MODEL or "(none)")
logger.info("Sidecar port: {}", SIDECAR_PORT)

# ============================================
# GPU CHECK
# ============================================
if not torch.cuda.is_available():
    logger.critical("CUDA NOT AVAILABLE - GPU REQUIRED, NO CPU FALLBACK")
    raise RuntimeError("CUDA NOT AVAILABLE - GPU REQUIRED, NO CPU FALLBACK")

logger.info("CUDA devices available: {}", torch.cuda.device_count())
for i in range(torch.cuda.device_count()):
    logger.info("  GPU {}: {} ({:.0f}MB)", i, torch.cuda.get_device_name(i), torch.cuda.get_device_properties(i).total_mem / 1024 / 1024)

# ============================================
# LOAD MODELS AT STARTUP
# ============================================

# --- Silero VAD (CPU always) ---
logger.info("Loading Silero VAD on CPU...")
t0 = time.time()
vad_model, vad_utils = torch.hub.load(
    repo_or_dir='snakers4/silero-vad',
    model='silero_vad',
    force_reload=False,
    onnx=False
)
(get_speech_timestamps, save_audio, read_audio, VADIterator, collect_chunks) = vad_utils
logger.info("Silero VAD loaded on CPU in {:.0f}ms", (time.time() - t0) * 1000)

# --- faster-whisper on GPU ---
logger.info("Loading faster-whisper large-v3 on {}...", CUDA_DEVICE)
t0 = time.time()
whisper_model = None
try:
    from faster_whisper import WhisperModel
    device_idx = int(CUDA_DEVICE.split(':')[1]) if ':' in CUDA_DEVICE else 0
    whisper_model = WhisperModel(
        "large-v3",
        device="cuda",
        device_index=device_idx,
        compute_type="float16"
    )
    logger.info("faster-whisper loaded on GPU {} (float16) in {:.0f}ms", device_idx, (time.time() - t0) * 1000)
except Exception as e:
    logger.critical("Failed to load faster-whisper: {}", e)
    logger.critical(traceback.format_exc())
    raise RuntimeError(f"faster-whisper initialization failed: {e}")

# --- Kokoro TTS DISABLED (stub only) ---
logger.info("Kokoro TTS DISABLED - using Chatterbox proxy only")
kokoro_model = None

# --- OpenWakeWord (CPU always) ---
logger.info("Loading OpenWakeWord on CPU...")
t0 = time.time()
from openwakeword.model import Model as WakeModel

oww_wake_words = [OWW_WAKE_MODEL]
if OWW_HALT_MODEL and OWW_HALT_MODEL.strip():
    oww_wake_words.append(OWW_HALT_MODEL)
    logger.info("Halt acoustic wake word configured: '{}'", OWW_HALT_MODEL)

oww_model = WakeModel(
    wakeword_models=oww_wake_words,
    inference_framework="onnx"
)
OWW_MODEL_KEYS = list(oww_model.models.keys())
logger.info("OpenWakeWord loaded in {:.0f}ms: START='{}'{}", (time.time() - t0) * 1000, OWW_WAKE_MODEL, f", HALT='{OWW_HALT_MODEL}'" if OWW_HALT_MODEL else "")

# --- Startup summary ---
logger.info("=== MODEL SUMMARY ===")
logger.info("  STT: faster-whisper large-v3 on {}", CUDA_DEVICE)
logger.info("  TTS: Chatterbox proxy at {}", CHATTERBOX_URL)
logger.info("  VAD: Silero on CPU")
logger.info("  WakeWord: OpenWakeWord on CPU (models: {})", OWW_MODEL_KEYS)
logger.info("=====================")

# ============================================
# FASTAPI APP
# ============================================
app = FastAPI(title="Finn Audio Sidecar", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# WebSocket connection tracker
ws_connections = {}
ws_counter = 0


class SpeakRequest(BaseModel):
    text: str
    language: str = "en"
    speaker_wav: str = None


class TranscribeRequest(BaseModel):
    audio_base64: str


class TTSRequest(BaseModel):
    text: str
    voice: str = TTS_VOICE_DEFAULT
    speed: float = 1.0


# ============================================
# HEALTH
# ============================================
@app.get("/health")
async def health():
    logger.debug("Health check requested")
    return {
        "status": "ok",
        "project": "finn",
        "device": CUDA_DEVICE,
        "gpu_models": {
            "stt": "faster-whisper-large-v3" if whisper_model else None,
            "tts_kokoro": None,
            "tts_chatterbox": CHATTERBOX_URL
        },
        "cpu_models": ["silero-vad", "openwakeword"],
        "vad_sensitivity": VAD_SENSITIVITY,
        "vad_silence_ms": VAD_SILENCE_MS,
        "config_path": CONFIG_PATH
    }


# ============================================
# TRANSCRIPTION HELPER
# ============================================
def transcribe_audio(audio_np, temp_path="D:/finn/finn-voice/temp/stt_audio.wav"):
    """
    Transcribe audio using faster-whisper (preloaded at startup).
    Returns (text, language) tuple.
    """
    logger.debug("transcribe_audio() called, audio samples: {}, dtype: {}", len(audio_np), audio_np.dtype)

    global whisper_model

    # Ensure audio is float32 normalized
    if audio_np.dtype != np.float32:
        audio_np = audio_np.astype(np.float32)
    if audio_np.max() > 1.0:
        audio_np = audio_np / 32768.0

    if whisper_model is None:
        logger.error("faster-whisper model not loaded!")
        return "", "unknown"

    try:
        duration_sec = len(audio_np) / 16000
        logger.info("Transcribing {:.2f}s audio ({} samples)...", duration_sec, len(audio_np))

        start_time = time.time()

        # Save audio to temp WAV file
        os.makedirs(os.path.dirname(temp_path), exist_ok=True)
        sf.write(temp_path, audio_np, 16000)
        logger.debug("Temp WAV written to {}", temp_path)

        # Transcribe using preloaded model
        segments, info = whisper_model.transcribe(temp_path, language="en", beam_size=5)

        # Collect all segments
        text_parts = []
        for segment in segments:
            text_parts.append(segment.text.strip())

        text = " ".join(text_parts)
        language = info.language if hasattr(info, 'language') else "en"

        elapsed_ms = (time.time() - start_time) * 1000
        rtf = elapsed_ms / (duration_sec * 1000) if duration_sec > 0 else 0

        logger.info("STT done in {:.0f}ms (RTF: {:.2f}) - '{}'", elapsed_ms, rtf, text[:100] + ("..." if len(text) > 100 else ""))

        return text, language

    except Exception as e:
        logger.error("Transcription failed: {}", e)
        logger.error(traceback.format_exc())
        return "", "unknown"


# ============================================
# POST /transcribe
# ============================================
@app.post("/transcribe")
async def transcribe(request: TranscribeRequest):
    """Transcribe base64 audio to text"""
    logger.info("POST /transcribe - base64 length: {}", len(request.audio_base64))
    try:
        audio_bytes = base64.b64decode(request.audio_base64)
        logger.debug("Decoded {} bytes of audio", len(audio_bytes))

        audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        logger.debug("Audio array: {} samples, {:.2f}s", len(audio_np), len(audio_np) / 16000)

        text, language = transcribe_audio(audio_np, "D:/finn/finn-voice/temp/transcribe_input.wav")

        logger.info("POST /transcribe result: '{}' (lang: {})", text[:80], language)
        return {"success": True, "text": text, "language": language, "model": STT_MODEL}
    except Exception as e:
        logger.error("POST /transcribe failed: {}", e)
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# TTS ENDPOINTS - Proxy to Chatterbox
# ============================================
@app.get("/tts/voices")
async def tts_voices():
    """Proxy to Chatterbox voices endpoint"""
    logger.info("GET /tts/voices - proxying to Chatterbox")
    try:
        async with httpx.AsyncClient() as client:
            t0 = time.time()
            resp = await client.get(f"{CHATTERBOX_URL}/api/ui/initial-data", timeout=10)
            elapsed_ms = (time.time() - t0) * 1000
            logger.info("Chatterbox voices response: {} in {:.0f}ms", resp.status_code, elapsed_ms)
            if resp.status_code == 200:
                return resp.json()
            logger.warning("Chatterbox returned {}", resp.status_code)
            return {"voices": ["default"], "status": "chatterbox not ready"}
    except Exception as e:
        logger.error("Chatterbox voices request failed: {}", e)
        return {"voices": ["default"], "error": str(e)}


@app.post("/tts/preview")
async def tts_preview(request: TTSRequest):
    """Proxy TTS preview to Chatterbox"""
    logger.info("POST /tts/preview - voice: {}, text: '{}'", request.voice, request.text[:60])
    try:
        async with httpx.AsyncClient() as client:
            t0 = time.time()
            resp = await client.post(
                f"{CHATTERBOX_URL}/tts",
                json={
                    "text": request.text,
                    "voice_mode": "predefined",
                    "predefined_voice_id": request.voice,
                    "speed_factor": request.speed,
                    "exaggeration": 0.7,
                    "temperature": 0.7,
                    "output_format": "wav"
                },
                timeout=60
            )
            elapsed_ms = (time.time() - t0) * 1000
            logger.info("TTS preview response: {} in {:.0f}ms", resp.status_code, elapsed_ms)
            if resp.status_code == 200:
                return StreamingResponse(io.BytesIO(resp.content), media_type="audio/wav")
            logger.error("Chatterbox preview returned {}: {}", resp.status_code, resp.text)
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
    except httpx.RequestError as e:
        logger.error("Chatterbox preview request error: {}", e)
        raise HTTPException(status_code=503, detail=f"Chatterbox server not available: {e}")


@app.post("/tts/speak")
async def tts_speak(request: TTSRequest):
    """Proxy TTS speak to Chatterbox"""
    logger.info("POST /tts/speak - voice: {}, text: '{}'", request.voice, request.text[:60])

    t0 = time.time()
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{CHATTERBOX_URL}/tts",
                json={
                    "text": request.text,
                    "voice_mode": "predefined",
                    "predefined_voice_id": request.voice,
                    "speed_factor": request.speed,
                    "exaggeration": 0.7,
                    "temperature": 0.7,
                    "output_format": "wav"
                },
                timeout=60
            )
            elapsed_ms = (time.time() - t0) * 1000
            logger.info("TTS speak response: {} in {:.0f}ms ({} bytes)", resp.status_code, elapsed_ms, len(resp.content))

            if resp.status_code == 200:
                return StreamingResponse(io.BytesIO(resp.content), media_type="audio/wav")

            logger.error("Chatterbox speak returned {}: {}", resp.status_code, resp.text)
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
    except httpx.RequestError as e:
        logger.error("Chatterbox speak request error: {}", e)
        raise HTTPException(status_code=503, detail=f"Chatterbox server not available: {e}")


# ============================================
# WS /listen - Stream PCM16, VAD + transcription
# ============================================
@app.websocket("/listen")
async def listen_websocket(websocket: WebSocket):
    """
    WebSocket: real-time audio streaming with VAD.
    Sends interim transcriptions during speech, final on VAD silence.
    Uses item_id pattern (finn_voice_UUID) for in-place GUI updates.
    """
    global ws_counter
    ws_counter += 1
    ws_id = ws_counter

    await websocket.accept()
    logger.info("[WS-{}] Client connected to /listen", ws_id)

    audio_buffer = []
    vad_buffer = []
    vad_iterator = VADIterator(vad_model, threshold=VAD_SENSITIVITY)
    silence_samples = 0
    silence_threshold = int(VAD_SILENCE_MS * 16)  # 16 samples per ms at 16kHz
    is_speaking = False
    speech_started = False
    VAD_CHUNK_SIZE = 512
    INTERIM_INTERVAL_MS = 300

    current_item_id = None
    last_interim_time = 0
    accumulated_text = ""

    logger.debug("[WS-{}] VAD config: sensitivity={}, silence_threshold={} samples ({}ms)", ws_id, VAD_SENSITIVITY, silence_threshold, VAD_SILENCE_MS)

    try:
        while True:
            data = await websocket.receive_bytes()
            chunk_len = len(data)

            # Convert PCM16 to float32
            audio_chunk = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
            audio_buffer.extend(audio_chunk.tolist())
            vad_buffer.extend(audio_chunk.tolist())

            logger.debug("[WS-{}] Received {} bytes ({} samples), buffer: {} samples", ws_id, chunk_len, len(audio_chunk), len(audio_buffer))

            # Process VAD in 512-sample chunks
            while len(vad_buffer) >= VAD_CHUNK_SIZE:
                vad_chunk = torch.tensor(vad_buffer[:VAD_CHUNK_SIZE])
                vad_buffer = vad_buffer[VAD_CHUNK_SIZE:]

                speech_dict = vad_iterator(vad_chunk)

                if speech_dict:
                    if 'start' in speech_dict:
                        is_speaking = True
                        silence_samples = 0
                        if not speech_started:
                            speech_started = True
                            current_item_id = f"finn_voice_{uuid.uuid4().hex[:8]}"
                            accumulated_text = ""
                            logger.info("[WS-{}] Speech started, item_id: {}", ws_id, current_item_id)
                            await websocket.send_json({
                                "type": "speech_start",
                                "item_id": current_item_id
                            })
                    elif 'end' in speech_dict:
                        is_speaking = False
                        logger.info("[WS-{}] VAD speech end detected", ws_id)

                # Count silence after speech
                if not is_speaking:
                    silence_samples += VAD_CHUNK_SIZE

            # Non-streaming interim: transcribe during speech periodically
            current_time = time.time() * 1000
            if speech_started and is_speaking and (current_time - last_interim_time) >= INTERIM_INTERVAL_MS:
                if len(audio_buffer) > 8000:  # At least 0.5s
                    logger.debug("[WS-{}] Running interim transcription ({} samples)", ws_id, len(audio_buffer))
                    audio_np = np.array(audio_buffer, dtype=np.float32)
                    interim_text, language = transcribe_audio(audio_np, "D:/finn/finn-voice/temp/interim_audio.wav")

                    if interim_text:
                        accumulated_text = interim_text
                        logger.info("[WS-{}] Interim: '{}'", ws_id, interim_text[:80])
                        await websocket.send_json({
                            "type": "interim",
                            "text": interim_text,
                            "item_id": current_item_id,
                            "language": language
                        })
                        last_interim_time = current_time

            # If enough silence after speech, send FINAL transcription
            if speech_started and not is_speaking and silence_samples >= silence_threshold:
                if len(audio_buffer) > silence_threshold:
                    logger.info("[WS-{}] Silence threshold reached, running final transcription ({} samples)", ws_id, len(audio_buffer))
                    audio_np = np.array(audio_buffer, dtype=np.float32)
                    final_text, language = transcribe_audio(audio_np, "D:/finn/finn-voice/temp/vad_audio.wav")

                    if final_text:
                        logger.info("[WS-{}] FINAL: '{}' (item_id: {})", ws_id, final_text[:100], current_item_id)
                        await websocket.send_json({
                            "type": "final",
                            "text": final_text,
                            "item_id": current_item_id,
                            "language": language
                        })

                # Reset for next utterance
                logger.debug("[WS-{}] Resetting for next utterance", ws_id)
                audio_buffer = []
                vad_buffer = []
                silence_samples = 0
                speech_started = False
                current_item_id = None
                accumulated_text = ""
                last_interim_time = 0
                vad_iterator.reset_states()

    except WebSocketDisconnect:
        logger.info("[WS-{}] Client disconnected from /listen (clean)", ws_id)
    except Exception as e:
        logger.error("[WS-{}] Error in /listen: {}", ws_id, e)
        logger.error(traceback.format_exc())
    finally:
        logger.info("[WS-{}] Cleaning up /listen connection", ws_id)
        vad_iterator.reset_states()


# ============================================
# WS /dictate - Continuous dictation, no auto-send
# ============================================
@app.websocket("/dictate")
async def dictate_websocket(websocket: WebSocket):
    """
    WebSocket: continuous transcription to input bar.
    NO VAD silence auto-send - user decides when to send via GUI.
    """
    global ws_counter
    ws_counter += 1
    ws_id = ws_counter

    await websocket.accept()
    logger.info("[DICTATE-{}] Client connected to /dictate", ws_id)

    audio_buffer = []
    VAD_CHUNK_SIZE = 512
    vad_buffer = []
    vad_iterator = VADIterator(vad_model, threshold=VAD_SENSITIVITY)
    is_speaking = False
    last_transcribe_time = 0
    TRANSCRIBE_INTERVAL_MS = 300

    try:
        while True:
            data = await websocket.receive_bytes()

            audio_chunk = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
            audio_buffer.extend(audio_chunk.tolist())
            vad_buffer.extend(audio_chunk.tolist())

            logger.debug("[DICTATE-{}] Received {} bytes, buffer: {} samples", ws_id, len(data), len(audio_buffer))

            # Process VAD to detect speech (for transcription timing only, NOT for auto-send)
            while len(vad_buffer) >= VAD_CHUNK_SIZE:
                vad_chunk = torch.tensor(vad_buffer[:VAD_CHUNK_SIZE])
                vad_buffer = vad_buffer[VAD_CHUNK_SIZE:]

                speech_dict = vad_iterator(vad_chunk)
                if speech_dict:
                    if 'start' in speech_dict:
                        is_speaking = True
                        logger.info("[DICTATE-{}] VAD speech start", ws_id)
                    elif 'end' in speech_dict:
                        is_speaking = False
                        logger.info("[DICTATE-{}] VAD speech end, buffer: {} samples", ws_id, len(audio_buffer))
                        # On speech end, transcribe what we have
                        if len(audio_buffer) > 8000:  # At least 0.5s
                            audio_np = np.array(audio_buffer, dtype=np.float32)
                            text, language = transcribe_audio(audio_np, "D:/finn/finn-voice/temp/dictate_audio.wav")

                            if text:
                                logger.info("[DICTATE-{}] Transcribed: '{}'", ws_id, text[:80])
                                await websocket.send_json({
                                    "type": "dictate",
                                    "text": text,
                                    "language": language
                                })

                            audio_buffer = []
                            last_transcribe_time = time.time() * 1000

            # Periodic transcription during long speech
            current_time = time.time() * 1000
            if is_speaking and (current_time - last_transcribe_time) >= TRANSCRIBE_INTERVAL_MS:
                if len(audio_buffer) > 12800:  # At least 0.8s
                    logger.debug("[DICTATE-{}] Periodic interim transcription ({} samples)", ws_id, len(audio_buffer))
                    audio_np = np.array(audio_buffer, dtype=np.float32)
                    text, language = transcribe_audio(audio_np, "D:/finn/finn-voice/temp/dictate_interim.wav")

                    if text:
                        logger.info("[DICTATE-{}] Interim: '{}'", ws_id, text[:80])
                        await websocket.send_json({
                            "type": "dictate_interim",
                            "text": text,
                            "language": language
                        })

                    last_transcribe_time = current_time

    except WebSocketDisconnect:
        logger.info("[DICTATE-{}] Client disconnected (clean)", ws_id)
    except Exception as e:
        logger.error("[DICTATE-{}] Error: {}", ws_id, e)
        logger.error(traceback.format_exc())
    finally:
        logger.info("[DICTATE-{}] Cleaning up connection", ws_id)
        vad_iterator.reset_states()


# ============================================
# WS /wakeword - Wake word detection + dictation
# ============================================
@app.websocket("/wakeword")
async def wakeword_websocket(websocket: WebSocket):
    """
    WebSocket: wake word detection + dictation to input bar.
    - Always-on OpenWakeWord detection
    - Wake word clears input bar and starts typing (like dictate mode)
    - Handles force_dictate / stop_dictate commands
    - Configurable via initial JSON message from client
    """
    global ws_counter
    ws_counter += 1
    ws_id = ws_counter

    await websocket.accept()
    logger.info("[WW-{}] ============================================", ws_id)
    logger.info("[WW-{}] NEW CLIENT CONNECTED to /wakeword", ws_id)
    logger.info("[WW-{}] VAD sensitivity: {}, silence: {}ms", ws_id, VAD_SENSITIVITY, VAD_SILENCE_MS)
    logger.info("[WW-{}] ============================================", ws_id)

    # Wait for config message first (handle both text and binary)
    logger.debug("[WW-{}] Waiting for config message...", ws_id)
    threshold = OWW_THRESHOLD
    first_audio_data = None

    try:
        first_msg = await websocket.receive()
        if "text" in first_msg:
            config = json.loads(first_msg["text"])
            threshold = float(config.get('threshold', OWW_THRESHOLD))
            logger.info("[WW-{}] Config received: threshold={}", ws_id, threshold)
        elif "bytes" in first_msg:
            logger.info("[WW-{}] Got binary before config, using defaults", ws_id)
            first_audio_data = first_msg["bytes"]
    except Exception as e:
        logger.warning("[WW-{}] Config receive error: {}, using defaults", ws_id, e)

    logger.info("[WW-{}] Start wake word: '{}', threshold: {}", ws_id, OWW_WAKE_MODEL, threshold)

    audio_buffer = []
    vad_buffer = []
    vad_iterator = VADIterator(vad_model, threshold=VAD_SENSITIVITY)
    is_speaking = False
    is_dictating = False
    VAD_CHUNK_SIZE = 512
    last_transcribe_time = 0
    TRANSCRIBE_INTERVAL_MS = 300
    accumulated_text = ""

    # Reset OpenWakeWord state
    oww_model.reset()
    logger.debug("[WW-{}] OpenWakeWord state reset", ws_id)

    try:
        data = first_audio_data

        while True:
            # If we don't have data from first chunk, receive new message
            if data is None:
                try:
                    message = await websocket.receive()

                    # Check if it's a text message (command)
                    if "text" in message:
                        text_data = message["text"]
                        try:
                            command_data = json.loads(text_data)
                            if isinstance(command_data, dict) and 'command' in command_data:
                                command = command_data['command']

                                if command == 'force_dictate':
                                    logger.info("[WW-{}] Force dictate command received", ws_id)
                                    is_dictating = True
                                    accumulated_text = ""
                                    audio_buffer = []
                                    vad_buffer = []
                                    last_transcribe_time = time.time() * 1000
                                    vad_iterator.reset_states()
                                    oww_model.reset()
                                    await websocket.send_json({
                                        "type": "dictate_started",
                                        "message": "Dictation mode activated"
                                    })
                                    continue

                                elif command == 'stop_dictate':
                                    logger.info("[WW-{}] Stop dictate command received", ws_id)
                                    is_dictating = False
                                    accumulated_text = ""
                                    audio_buffer = []
                                    vad_buffer = []
                                    vad_iterator.reset_states()
                                    oww_model.reset()
                                    await websocket.send_json({
                                        "type": "dictate_stopped",
                                        "message": "Dictation mode deactivated"
                                    })
                                    continue

                                else:
                                    logger.warning("[WW-{}] Unknown command: {}", ws_id, command)

                        except json.JSONDecodeError:
                            logger.warning("[WW-{}] Received non-JSON text, ignoring", ws_id)
                            continue
                        continue

                    elif "bytes" in message:
                        data = message["bytes"]
                    else:
                        continue

                except WebSocketDisconnect:
                    logger.info("[WW-{}] Client disconnected (clean)", ws_id)
                    break
                except Exception as e:
                    logger.error("[WW-{}] WebSocket error: {}", ws_id, e)
                    logger.error(traceback.format_exc())
                    break

            if data is None:
                continue

            # Convert PCM16 to int16 for OpenWakeWord
            audio_int16 = np.frombuffer(data, dtype=np.int16)
            data = None  # Clear for next iteration

            logger.debug("[WW-{}] Processing {} int16 samples, dictating={}", ws_id, len(audio_int16), is_dictating)

            # Run OpenWakeWord on every chunk
            oww_predictions = oww_model.predict(audio_int16)

            # Log scores when anything is warm
            if any(v > 0.1 for v in oww_predictions.values()):
                scores_str = ", ".join([f"{k}={v:.3f}" for k, v in oww_predictions.items()])
                logger.debug("[WW-{}] OWW scores: {}", ws_id, scores_str)

            # Check for HALT acoustic wake word FIRST (always active)
            if OWW_HALT_MODEL and OWW_HALT_MODEL.strip():
                halt_score = oww_predictions.get(OWW_HALT_MODEL, 0)
                if halt_score >= threshold:
                    logger.warning("[WW-{}] *** HALT WAKE WORD DETECTED *** word='{}' score={:.3f} threshold={}", ws_id, OWW_HALT_MODEL, halt_score, threshold)

                    await websocket.send_json({
                        "type": "wake_halt",
                        "wake_word": OWW_HALT_MODEL,
                        "score": float(halt_score)
                    })
                    is_dictating = False
                    accumulated_text = ""
                    audio_buffer = []
                    vad_buffer = []
                    vad_iterator.reset_states()
                    oww_model.reset()
                    continue

            # Not dictating: check for wake word to START dictation
            if not is_dictating:
                wake_score = oww_predictions.get(OWW_WAKE_MODEL, 0)
                if wake_score >= threshold:
                    logger.info("[WW-{}] *** WAKE WORD DETECTED *** word='{}' score={:.3f} threshold={}", ws_id, OWW_WAKE_MODEL, wake_score, threshold)

                    is_dictating = True
                    accumulated_text = ""
                    await websocket.send_json({
                        "type": "wake_start",
                        "wake_word": OWW_WAKE_MODEL,
                        "score": float(wake_score)
                    })
                    audio_buffer = []
                    vad_buffer = []
                    last_transcribe_time = time.time() * 1000
                    vad_iterator.reset_states()
                    oww_model.reset()
                    continue

            # Dictating: transcribe audio and send to frontend
            if is_dictating:
                audio_float = audio_int16.astype(np.float32) / 32768.0
                audio_buffer.extend(audio_float.tolist())
                vad_buffer.extend(audio_float.tolist())

                # Process VAD
                while len(vad_buffer) >= VAD_CHUNK_SIZE:
                    vad_chunk = torch.tensor(vad_buffer[:VAD_CHUNK_SIZE])
                    vad_buffer = vad_buffer[VAD_CHUNK_SIZE:]

                    speech_dict = vad_iterator(vad_chunk)
                    if speech_dict:
                        if 'start' in speech_dict:
                            is_speaking = True
                            logger.info("[WW-{}] VAD speech START (buffer: {} samples)", ws_id, len(audio_buffer))
                        elif 'end' in speech_dict:
                            is_speaking = False
                            logger.info("[WW-{}] VAD speech END (buffer: {} samples)", ws_id, len(audio_buffer))
                            # On speech end, transcribe
                            if len(audio_buffer) > 8000:
                                t0 = time.time()
                                audio_np = np.array(audio_buffer, dtype=np.float32)
                                text, language = transcribe_audio(audio_np, "D:/finn/finn-voice/temp/ww_audio.wav")

                                if text:
                                    accumulated_text = text
                                    logger.info("[WW-{}] Dictate text: '{}'", ws_id, text[:80])
                                    await websocket.send_json({
                                        "type": "dictate",
                                        "text": text
                                    })

                                audio_buffer = []
                                last_transcribe_time = time.time() * 1000

                # Periodic transcription during long speech
                current_time = time.time() * 1000
                if is_speaking and (current_time - last_transcribe_time) >= TRANSCRIBE_INTERVAL_MS:
                    if len(audio_buffer) > 12800:
                        logger.debug("[WW-{}] Periodic interim ({} samples)", ws_id, len(audio_buffer))
                        audio_np = np.array(audio_buffer, dtype=np.float32)
                        text, language = transcribe_audio(audio_np, "D:/finn/finn-voice/temp/ww_interim.wav")

                        if text:
                            accumulated_text = text
                            logger.info("[WW-{}] Dictate interim: '{}'", ws_id, text[:80])
                            await websocket.send_json({
                                "type": "dictate_interim",
                                "text": text
                            })

                        last_transcribe_time = current_time

    except WebSocketDisconnect:
        logger.info("[WW-{}] Client disconnected (clean)", ws_id)
    except Exception as e:
        logger.error("[WW-{}] Error: {}", ws_id, e)
        logger.error(traceback.format_exc())
    finally:
        logger.info("[WW-{}] Cleaning up /wakeword connection", ws_id)
        vad_iterator.reset_states()
        oww_model.reset()


# ============================================
# MAIN
# ============================================
if __name__ == "__main__":
    logger.info("Starting Finn Audio Sidecar on 0.0.0.0:{}", SIDECAR_PORT)
    uvicorn.run(app, host="0.0.0.0", port=SIDECAR_PORT, log_level="info")
