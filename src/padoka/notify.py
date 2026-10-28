"""systemd sd_notify implementation (optional).

Reports READY/WATCHDOG/RELOADING/STOPPING to the systemd supervisor via
``NOTIFY_SOCKET``.  No-op when the environment variable is not set
(e.g. under Docker).
"""

from __future__ import annotations

import os
import socket
from typing import Optional


class NotifySocket:
    def __init__(self):
        self.path = os.environ.get("NOTIFY_SOCKET")
        self.status = None
        self._sock: Optional[socket.socket] = None

    def _send(self, payload: str) -> None:
        if not self.path:
            return
        try:
            if self.path.startswith("@"):
                self.path = "\0" + self.path[1:]
            s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            s.sendto(payload.encode("utf-8"), self.path)
            s.close()
        except OSError:
            pass

    def ready(self) -> None:
        self._send("READY=1")

    def watchdog(self) -> None:
        self._send("WATCHDOG=1")

    def reloading(self) -> None:
        self._send("RELOADING=1")

    def stopping(self) -> None:
        self._send("STOPPING=1")

    def set_status(self, text: str) -> None:
        self.status = text
        self._send("STATUS=" + text)
