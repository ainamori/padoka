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

"""
Padoka - IRC Client Server program.

Padoka connects to a real IRC server and acts as a proxy client-server,
so regular IRC clients (e.g. irssi, weechat) can attach to it.
It also hosts a plugin "bot" framework.

Lineage
-------
Padoka is a Python re-implementation of *nadoka*
(https://github.com/nadoka/nadoka, Ruby), which was itself modeled on
*madoka* (https://www.madoka.org/), the original IRC client-server
program by The madoka project.  The "one resident nick shared by many
clients" concept, the flood control, the automatic re-join and the bot
plugin design all originate from madoka.

License
-------
Padoka is released under the GNU Affero General Public License v3 or
later (AGPL-3.0-or-later).  See the LICENSE file for the full text and
the NOTICE file for the upstream attribution.
"""

VERSION = "0.10.3-py"

__all__ = ["VERSION"]
