"""
Finn Voice - mDNS Service Advertiser
Registers sidecar and chatterbox on LAN so MacBook discovers automatically.

Services:
  _finn-sidecar._tcp -> port 8082
  _finn-chatterbox._tcp -> port 8004

Usage:
  Standalone: python advertise.py
  Import: from advertise import start_advertising; start_advertising()
"""

import os
import sys
import time
import socket
import threading
from loguru import logger

logger.remove()
logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | <level>{message}</level>", level="DEBUG", colorize=True)
logger.add("D:/finn/finn-voice/logs/advertise_{time:YYYY-MM-DD}.log", rotation="1 day", retention="7 days", level="DEBUG")

try:
    from zeroconf import Zeroconf, ServiceInfo
except ImportError:
    logger.error("zeroconf package not installed. Run: pip install zeroconf")
    sys.exit(1)


def get_lan_ip():
    """Get local LAN IP address (not 127.0.0.1)"""
    try:
        # Connect to external address to determine local IP (doesn't actually send data)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        logger.debug(f"Detected LAN IP: {ip}")
        return ip
    except Exception as e:
        logger.warning(f"Failed to detect LAN IP: {e}, trying fallback")
        # Fallback: enumerate interfaces
        hostname = socket.gethostname()
        try:
            ip = socket.gethostbyname(hostname)
            if ip != "127.0.0.1":
                logger.debug(f"Fallback LAN IP: {ip}")
                return ip
        except Exception:
            pass
        logger.error("Could not determine LAN IP, using 0.0.0.0")
        return "0.0.0.0"


class FinnServiceAdvertiser:
    def __init__(self, sidecar_port=8082, chatterbox_port=8004):
        self.sidecar_port = sidecar_port
        self.chatterbox_port = chatterbox_port
        self.zeroconf = None
        self.services = []
        self.current_ip = None
        self._running = False
        self._monitor_thread = None

    def _create_service_info(self, service_type, name, port, ip):
        """Create a ServiceInfo object for registration"""
        import socket as sock
        ip_bytes = sock.inet_aton(ip)

        info = ServiceInfo(
            type_=f"{service_type}.local.",
            name=f"finn-voice-{name}.{service_type}.local.",
            addresses=[ip_bytes],
            port=port,
            properties={
                b"version": b"1.0",
                b"app": b"finn-voice",
                b"service": name.encode()
            },
            server=f"finn-windows.local."
        )
        return info

    def register(self):
        """Register all services on mDNS"""
        self.current_ip = get_lan_ip()
        logger.info(f"Registering mDNS services on {self.current_ip}")

        self.zeroconf = Zeroconf()

        # Register sidecar
        sidecar_info = self._create_service_info(
            "_finn-sidecar._tcp", "sidecar", self.sidecar_port, self.current_ip
        )
        self.zeroconf.register_service(sidecar_info)
        self.services.append(sidecar_info)
        logger.info(f"Registered: _finn-sidecar._tcp on {self.current_ip}:{self.sidecar_port}")

        # Register chatterbox
        chatterbox_info = self._create_service_info(
            "_finn-chatterbox._tcp", "chatterbox", self.chatterbox_port, self.current_ip
        )
        self.zeroconf.register_service(chatterbox_info)
        self.services.append(chatterbox_info)
        logger.info(f"Registered: _finn-chatterbox._tcp on {self.current_ip}:{self.chatterbox_port}")

    def unregister(self):
        """Unregister all services"""
        if self.zeroconf:
            for svc in self.services:
                try:
                    self.zeroconf.unregister_service(svc)
                    logger.info(f"Unregistered: {svc.name}")
                except Exception as e:
                    logger.warning(f"Failed to unregister {svc.name}: {e}")
            self.services.clear()
            self.zeroconf.close()
            self.zeroconf = None
            logger.info("All services unregistered, zeroconf closed")

    def _monitor_ip_changes(self):
        """Check every 30s if IP changed, re-register if so"""
        while self._running:
            time.sleep(30)
            if not self._running:
                break

            new_ip = get_lan_ip()
            if new_ip != self.current_ip and new_ip != "0.0.0.0":
                logger.warning(f"IP changed: {self.current_ip} -> {new_ip}, re-registering services")
                self.unregister()
                self.register()

    def start(self):
        """Start advertising and IP monitoring"""
        self._running = True
        self.register()

        self._monitor_thread = threading.Thread(target=self._monitor_ip_changes, daemon=True)
        self._monitor_thread.start()
        logger.info("mDNS advertiser started (monitoring IP changes every 30s)")

    def stop(self):
        """Stop advertising"""
        self._running = False
        self.unregister()
        logger.info("mDNS advertiser stopped")


# Global instance
_advertiser = None

def start_advertising(sidecar_port=8082, chatterbox_port=8004):
    """Start mDNS advertising (importable function)"""
    global _advertiser
    _advertiser = FinnServiceAdvertiser(sidecar_port, chatterbox_port)
    _advertiser.start()
    return _advertiser

def stop_advertising():
    """Stop mDNS advertising"""
    global _advertiser
    if _advertiser:
        _advertiser.stop()
        _advertiser = None


if __name__ == "__main__":
    import json

    # Read ports from config if available
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "finn-voice-config.json")
    sidecar_port = 8082
    chatterbox_port = 8004

    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
            sidecar_port = config.get("audio_sidecar_port", 8082)
            chatterbox_port = int(str(config.get("chatterbox_url", "http://localhost:8004")).split(":")[-1])
    except Exception as e:
        logger.warning(f"Could not read config: {e}, using default ports")

    logger.info("=" * 50)
    logger.info("Finn Voice - mDNS Service Advertiser")
    logger.info(f"Sidecar: port {sidecar_port}")
    logger.info(f"Chatterbox: port {chatterbox_port}")
    logger.info("=" * 50)

    advertiser = start_advertising(sidecar_port, chatterbox_port)

    try:
        logger.info("Press Ctrl+C to stop...")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        stop_advertising()
        logger.info("Done.")
