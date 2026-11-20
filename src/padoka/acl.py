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

"""Client access-control list.

Rules are evaluated top-to-bottom; the first matching rule decides the
outcome.  With no rules, everything is allowed.

Example:
    deny all
    allow 192.168.0.0/24
"""

from __future__ import annotations

import ipaddress
from typing import List, Optional, Tuple, Union

__all__ = ["ACL"]

# The value stored per rule, depending on the rule kind:
#   "all"  -> None
#   "cidr" -> IPv4Network / IPv6Network
#   "ip"   -> IPv4Address / IPv6Address
#   "host" -> str (literal peer address, matched case-insensitively)
_Val = Union[
    None,
    "ipaddress.IPv4Network",
    "ipaddress.IPv6Network",
    "ipaddress.IPv4Address",
    "ipaddress.IPv6Address",
    str,
]


def _parse(target: str) -> Tuple[str, _Val]:
    target = target.strip()
    if target in ("all", "*"):
        return ("all", None)
    if "/" in target:
        return ("cidr", ipaddress.ip_network(target, strict=False))
    if target.count(".") == 3 or ":" in target:
        try:
            return ("ip", ipaddress.ip_address(target))
        except ValueError:
            return ("host", target.lower())
    return ("host", target.lower())


class ACL:
    def __init__(self, source: Optional[str] = None):
        self.rules: List[Tuple[str, str, _Val]] = []
        if source:
            for line in source.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(None, 1)
                if len(parts) != 2:
                    continue
                action, target = parts[0].lower(), parts[1]
                try:
                    kind, val = _parse(target)
                except ValueError:
                    continue
                self.rules.append((action, kind, val))

    def allow_socket(self, peer: Tuple[str, int]) -> bool:
        addr = peer[0]
        try:
            ip: Optional[Union[ipaddress.IPv4Address, ipaddress.IPv6Address]] = \
                ipaddress.ip_address(addr)
        except ValueError:
            ip = None

        for action, kind, val in self.rules:
            matched = False
            if kind == "all":
                matched = True
            elif kind == "cidr" and ip is not None and isinstance(
                    val, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
                matched = ip in val
            elif kind == "ip" and ip is not None and val is not None:
                matched = ip == val
            elif kind == "host" and val is not None:
                matched = addr.lower() == val
            if matched:
                return action == "allow"
        return True  # no matching rule -> allow
