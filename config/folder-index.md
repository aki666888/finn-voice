config - Folder Index

finn-voice-config.json
  Master configuration for sidecar and relay processes.
  Keys: xai_api_key, openrouter_api_key, tts_voice, stt_model, tts_model, cuda_device,
        audio_sidecar_port, grok_relay_port, chatterbox_url, log_level, log_dir, etc.
  Read by: finn-audio-sidecar.py, finn-voice-relay.js

voice-provider.md
  Single word: deepgram, local, or disabled
  Read by: Finn Swift app via ConfigReader+Voice.swift
  Controls which voice pipeline is active

voice-sidecar-url.md
  Single URL: http://IP:PORT (e.g. http://192.168.1.100:8082)
  Read by: Finn Swift app via ConfigReader+Voice.swift
  Points to the Windows PC running finn-audio-sidecar.py
