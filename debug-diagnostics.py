"""
Finn Voice - Windows Diagnostic Tool
Checks GPU, services, models, firewall, network, disk, mDNS.
Reports PASS/FAIL for each with fix suggestions.

Usage: python debug-diagnostics.py
"""

import os
import sys
import json
import time
import socket
import subprocess
import shutil

# Simple colored output (no loguru needed for diagnostic tool)
def PASS(msg):
    print(f"  [PASS] {msg}")

def FAIL(msg, fix=""):
    print(f"  [FAIL] {msg}")
    if fix:
        print(f"         FIX: {fix}")

def WARN(msg, note=""):
    print(f"  [WARN] {msg}")
    if note:
        print(f"         NOTE: {note}")

def INFO(msg):
    print(f"  [INFO] {msg}")


def check_gpu():
    print("\n--- GPU ---")
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.used,memory.total,driver_version",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                PASS(f"GPU: {line.strip()}")
        else:
            FAIL("nvidia-smi failed", "Install NVIDIA drivers")
    except FileNotFoundError:
        FAIL("nvidia-smi not found", "Install NVIDIA drivers or add to PATH")
    except Exception as e:
        FAIL(f"GPU check error: {e}")

    # Check CUDA via torch
    try:
        import torch
        if torch.cuda.is_available():
            count = torch.cuda.device_count()
            PASS(f"PyTorch CUDA available, {count} device(s)")
            for i in range(count):
                name = torch.cuda.get_device_name(i)
                mem = torch.cuda.get_device_properties(i).total_mem / (1024**3)
                PASS(f"  cuda:{i} = {name} ({mem:.0f}GB)")
        else:
            FAIL("PyTorch CUDA not available", "pip install torch --index-url https://download.pytorch.org/whl/cu121")
    except ImportError:
        FAIL("PyTorch not installed", "pip install torch --index-url https://download.pytorch.org/whl/cu121")


def check_python_deps():
    print("\n--- Python Dependencies ---")
    deps = [
        "torch", "faster_whisper", "openwakeword", "fastapi", "uvicorn",
        "loguru", "httpx", "soundfile", "numpy", "pydantic"
    ]
    for dep in deps:
        try:
            __import__(dep)
            PASS(f"{dep} installed")
        except ImportError:
            FAIL(f"{dep} not installed", f"pip install {dep.replace('_', '-')}")

    # Check zeroconf separately (for mDNS)
    try:
        import zeroconf
        PASS(f"zeroconf installed (v{zeroconf.__version__})")
    except ImportError:
        WARN("zeroconf not installed", "pip install zeroconf (needed for mDNS auto-discovery)")


def check_services():
    print("\n--- Services ---")
    import urllib.request

    # Sidecar health
    try:
        req = urllib.request.Request("http://localhost:8082/health", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            PASS(f"Audio Sidecar (port 8082): status={data.get('status', '?')}")
            if data.get("gpu_models", {}).get("stt"):
                PASS(f"  Whisper model loaded: {data['gpu_models']['stt']}")
            else:
                WARN("  Whisper model not reported in /health")
    except Exception as e:
        FAIL(f"Audio Sidecar (port 8082): not reachable", "Run: python finn-audio-sidecar.py")

    # Chatterbox health
    try:
        req = urllib.request.Request("http://localhost:8004/health", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            PASS(f"Chatterbox TTS (port 8004): status={data.get('status', '?')}")
            if data.get("model_loaded"):
                PASS(f"  TTS model loaded on {data.get('gpu', '?')}")
            else:
                WARN("  TTS model not loaded yet (may still be starting)")
    except Exception as e:
        FAIL(f"Chatterbox TTS (port 8004): not reachable", "Run: python windows-startup/chatterbox-server.py")


def check_firewall():
    print("\n--- Firewall ---")
    try:
        result = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", "name=Finn-Audio-Sidecar"],
            capture_output=True, text=True, timeout=10
        )
        if "Finn-Audio-Sidecar" in result.stdout:
            PASS("Firewall rule for port 8082 (sidecar) exists")
        else:
            FAIL("No firewall rule for port 8082", "Run windows-startup/setup-firewall.bat as Administrator")
    except Exception as e:
        WARN(f"Could not check firewall: {e}")

    try:
        result = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", "name=Finn-Chatterbox-TTS"],
            capture_output=True, text=True, timeout=10
        )
        if "Finn-Chatterbox-TTS" in result.stdout:
            PASS("Firewall rule for port 8004 (chatterbox) exists")
        else:
            FAIL("No firewall rule for port 8004", "Run windows-startup/setup-firewall.bat as Administrator")
    except Exception as e:
        WARN(f"Could not check firewall: {e}")


def check_network():
    print("\n--- Network ---")
    # Get LAN IP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        PASS(f"LAN IP: {ip}")
        INFO(f"MacBook should use: http://{ip}:8082 (sidecar) and http://{ip}:8004 (chatterbox)")
    except Exception as e:
        FAIL(f"Could not determine LAN IP: {e}")

    # Check binding on 0.0.0.0
    for port, name in [(8082, "sidecar"), (8004, "chatterbox")]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            result = s.connect_ex(("0.0.0.0", port))
            s.close()
            if result == 0:
                PASS(f"Port {port} ({name}) is listening")
            else:
                WARN(f"Port {port} ({name}) not listening on 0.0.0.0")
        except Exception:
            pass


def check_mdns():
    print("\n--- mDNS (Bonjour) ---")
    try:
        from zeroconf import Zeroconf, ServiceBrowser

        found_services = []

        class Listener:
            def add_service(self, zc, type_, name):
                found_services.append((type_, name))
            def remove_service(self, zc, type_, name):
                pass
            def update_service(self, zc, type_, name):
                pass

        zc = Zeroconf()
        listener = Listener()

        # Browse for our service types
        for svc_type in ["_finn-sidecar._tcp.local.", "_finn-chatterbox._tcp.local."]:
            ServiceBrowser(zc, svc_type, listener)

        time.sleep(3)  # Wait for discovery
        zc.close()

        if found_services:
            for stype, sname in found_services:
                PASS(f"mDNS service found: {sname}")
        else:
            WARN("No finn mDNS services found on LAN", "Run: python advertise.py")

    except ImportError:
        WARN("zeroconf not installed, skipping mDNS check", "pip install zeroconf")
    except Exception as e:
        WARN(f"mDNS check error: {e}")


def check_config():
    print("\n--- Config ---")
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "finn-voice-config.json")
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
        PASS(f"Config loaded: {config_path}")

        # Check critical keys
        for key in ["cuda_device", "audio_sidecar_port", "chatterbox_url", "stt_model", "tts_model"]:
            if key in config:
                PASS(f"  {key} = {config[key]}")
            else:
                WARN(f"  {key} not set in config")

        if not config.get("xai_api_key"):
            INFO("  xai_api_key not set (OK if not using Grok voice)")
    except FileNotFoundError:
        FAIL(f"Config not found: {config_path}", "Create config/finn-voice-config.json")
    except json.JSONDecodeError as e:
        FAIL(f"Config invalid JSON: {e}", "Check config/finn-voice-config.json syntax")


def check_disk():
    print("\n--- Disk ---")
    # Check logs dir
    logs_dir = "D:/finn/finn-voice/logs"
    if os.path.isdir(logs_dir):
        PASS(f"Logs directory exists: {logs_dir}")
        log_files = [f for f in os.listdir(logs_dir) if f.endswith(".log")]
        INFO(f"  {len(log_files)} log file(s)")
    else:
        WARN(f"Logs directory missing: {logs_dir}", "Will be created on first run")

    # Check temp dir
    temp_dir = "D:/finn/finn-voice/temp"
    if os.path.isdir(temp_dir):
        PASS(f"Temp directory exists: {temp_dir}")
    else:
        WARN(f"Temp directory missing: {temp_dir}", "Will be created on first run")

    # Disk space
    total, used, free = shutil.disk_usage("D:/")
    free_gb = free / (1024**3)
    if free_gb > 10:
        PASS(f"Disk space: {free_gb:.1f}GB free")
    else:
        WARN(f"Low disk space: {free_gb:.1f}GB free")


def main():
    print("=" * 60)
    print("  Finn Voice - Windows Diagnostic Report")
    print("=" * 60)

    check_gpu()
    check_python_deps()
    check_config()
    check_services()
    check_firewall()
    check_network()
    check_mdns()
    check_disk()

    print("\n" + "=" * 60)
    print("  Diagnostic complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()
