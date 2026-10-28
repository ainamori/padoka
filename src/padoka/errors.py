"""Exception hierarchy."""


class PDKError(Exception):
    """Base class for all padoka errors."""


class PDKQuitClient(PDKError):
    """Raised internally when a client sends QUIT."""


class PDKBotBreak(PDKError):
    """A bot asked the dispatch loop to stop (break)."""


class PDKBotSendCancel(PDKError):
    """A bot asked the server to cancel sending a message to other clients."""


class PDKQuitProgram(PDKError):
    """Raised to shut the whole program down."""


class PDKRestartProgram(PDKError):
    """Raised to restart the program."""


class PDKReconnectToServer(PDKError):
    """Raised to force a reconnect to the IRC server."""


class PDKInvalidMessage(PDKError):
    """A message that cannot be forwarded (contains CR/LF)."""


# --- Filter exceptions -------------------------------------------------
# These are raised by user-supplied filter callables to control how an
# incoming server message is handled.


class PDKFilterMessageSendCancel(PDKError):
    """Drop the message entirely (do not send to clients)."""


class PDKFilterMessageReplace(PDKError):
    """Replace the message with the one carried in ``.msg``."""

    def __init__(self, msg):
        super().__init__(msg)
        self.msg = msg


class PDKFilterMessageOnlyBot(PDKError):
    """Route the message to bots only (not to clients)."""


class PDKFilterMessageOnlyLog(PDKError):
    """Log the message only (not to clients or bots)."""


class PDKFilterMessageBotAndLog(PDKError):
    """Send to bots and log, but not to clients."""
