"""
CNC Direct Editor — Entry point.

Works directly on original G-code files in-place.
No copying, no auto-renaming. Duplicate detection via scoring and notes.
"""

import sys
import os
import logging
import threading
import faulthandler
from collections import deque


def get_exe_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# In-memory error buffer — last 50 ERROR/CRITICAL entries for bug reports
# ---------------------------------------------------------------------------

class _ErrorBuffer(logging.Handler):
    """Keeps the last N error/critical log records in memory."""
    def __init__(self, maxlen: int = 50):
        super().__init__(level=logging.ERROR)
        self._buf: deque[str] = deque(maxlen=maxlen)

    def emit(self, record: logging.LogRecord):
        self._buf.append(self.format(record))

    def get_recent(self) -> str:
        return "\n".join(self._buf) if self._buf else ""

    def clear(self):
        self._buf.clear()


# Module-level singleton — imported by bug report dialog
error_buffer = _ErrorBuffer()


def setup_logging(exe_dir: str):
    log_path = os.path.join(exe_dir, "direct_editor_error.log")
    logging.basicConfig(
        filename=log_path,
        level=logging.DEBUG,
        format="%(asctime)s  %(levelname)s  %(message)s",
        encoding="utf-8",
    )

    # Attach in-memory buffer to root logger
    error_buffer.setFormatter(
        logging.Formatter("%(asctime)s  %(levelname)s  %(message)s"))
    logging.getLogger().addHandler(error_buffer)

    def handle_exception(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logging.critical("Unhandled exception", exc_info=(exc_type, exc_value, exc_tb))
    sys.excepthook = handle_exception

    def handle_thread_exception(args):
        logging.critical(
            "Unhandled exception in thread %s",
            getattr(args.thread, "name", args.thread),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )
    threading.excepthook = handle_thread_exception

    try:
        fault_log = open(log_path, "a", encoding="utf-8")
        faulthandler.enable(file=fault_log)
    except Exception:
        pass

    try:
        from PyQt6.QtCore import qInstallMessageHandler, QtMsgType
        def qt_message_handler(msg_type, context, message):
            if msg_type == QtMsgType.QtCriticalMsg:
                logging.critical("Qt: %s", message)
            elif msg_type == QtMsgType.QtFatalMsg:
                logging.critical("Qt FATAL: %s", message)
            elif msg_type == QtMsgType.QtWarningMsg:
                logging.warning("Qt: %s", message)
        qInstallMessageHandler(qt_message_handler)
    except Exception:
        pass


def main():
    exe_dir = get_exe_dir()
    setup_logging(exe_dir)
    logging.info("CNC Direct Editor starting")

    try:
        from PyQt6.QtWidgets import QApplication
        from ui.main_window import DirectMainWindow
        app = QApplication(sys.argv)
        app.setApplicationName("CNC Direct Editor")
        app.setOrganizationName("HAAS Tools")
        app.setStyle("Fusion")
        window = DirectMainWindow(exe_dir=exe_dir)
        window.show()
        sys.exit(app.exec())
    except Exception:
        logging.critical("Fatal error in main()", exc_info=True)
        raise


if __name__ == "__main__":
    main()
