# Copyright (c) 2026 Akira Inamori
#
# Padoka is a Python re-implementation of nadoka
# (https://github.com/nadoka/nadoka), which was itself modeled on
# madoka (https://www.madoka.org/), the original IRC client-server
# program by The madoka project.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public
# License along with this program. If not, see
# <https://www.gnu.org/licenses/>.

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
