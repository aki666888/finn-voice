voice-modus-operandi.md
  Core architecture, design decisions, cross-machine workflow, and overall pipeline.
  Read this first for full understanding of how finn-voice works.

know-how-local-pipeline.md
  Step-by-step how the local voice pipeline works end to end.
  MacBook mic -> Windows sidecar STT -> provider -> sidecar TTS -> MacBook speaker.
  Read when implementing or debugging the round-trip flow.

know-about-sidecar-api.md
  Complete API reference for finn-audio-sidecar.py endpoints.
  Every endpoint, request format, response format, WebSocket message types.
  Read when connecting to or debugging the sidecar.

know-about-finn-voice-arch.md
  Architecture of finn-voice folder, what each file does, how they connect.
  File inventory, dependency map, startup order.
  Read when onboarding or understanding the codebase.

know-about-swift-patches.md
  What each Swift patch file does and how to apply it to the Finn app.
  Covers VoiceProvider protocol, LocalVoiceProvider, PTT extension, TTS extension, ConfigReader extension.
  Read when integrating patches into the Finn Xcode project.
