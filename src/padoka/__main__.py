"""
Command-line entry point.

Usage:
    python -m padoka -c config/padoka.yml [--debug]
    padoka -c config/padoka.yml [--debug]

Environment:
    PADOKA_CONFIG    default config file path
    NOTIFY_SOCKET    systemd sd_notify socket (optional; no-op if unset)

Signals (when run as PID 1 / main thread):
    SIGINT/SIGTERM -> quit
    SIGHUP         -> reload config
    SIGUSR1/USR2   -> invoke bots
"""

from __future__ import annotations

import argparse
import sys
import os
from typing import List, Optional

from . import VERSION
from . import errors as E
from .server import PDKServer
from .notify import NotifySocket


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="padoka", description="Padoka: IRC Client Server program (Python)")
    p.add_argument("-c", "--rc", required=False,
                   help="YAML config file (required; or $PADOKA_CONFIG)")
    p.add_argument("-d", "--debug", action="store_true",
                   help="run in debug mode (loglevel 3)")
    p.add_argument("--pid", metavar="PIDFILE",
                   help="write process pid to PIDFILE")
    p.add_argument("-v", "--version", action="store_true",
                   help="print version and exit")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(VERSION)
        return 0

    rcfile = args.rc or os.environ.get("PADOKA_CONFIG")
    if not rcfile:
        print(VERSION, file=sys.stderr)
        parser.print_help(sys.stderr)
        return 2

    if args.debug:
        os.environ["PADOKA_DEBUG"] = "1"

    if args.pid:
        with open(args.pid, "w") as f:
            f.write("%d\n" % os.getpid())

    notify = NotifySocket()
    server = None
    try:
        server = PDKServer(rcfile)
        server.start()
    except E.PDKQuitProgram:
        notify.stopping()
    except E.PDKRestartProgram:
        notify.reloading()
        # simple in-process restart
        try:
            server = PDKServer(rcfile)
            server.start()
        except E.PDKQuitProgram:
            notify.stopping()
    except Exception as e:
        notify.stopping()
        try:
            with open("padoka_fatal_error", "w") as f:
                f.write(str(e) + "\n")
                import traceback
                f.write("".join(traceback.format_stack()))
        except OSError:
            pass
        print("fatal error: %s" % e, file=sys.stderr)
        return 1
    finally:
        if args.pid and os.path.exists(args.pid):
            try:
                os.unlink(args.pid)
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
