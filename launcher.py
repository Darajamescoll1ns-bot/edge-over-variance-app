"""
launcher.py — Local desktop launcher
=====================================

Starts the web app on a free local port and opens it in your default browser.
This is what the double-click `.command` file runs. It never touches the
network beyond 127.0.0.1 — the whole thing lives on your Mac.

Run directly with:  python3 launcher.py
(but normally you'd just double-click "Start Trading Trainer.command")
"""

from __future__ import annotations

import os
import socket
import threading
import time
import webbrowser


def _free_port() -> int:
    """Ask the OS for an unused local port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_then_open(port: int, attempts: int = 60) -> None:
    """Poll until the server accepts connections, then open the browser."""
    url = f"http://127.0.0.1:{port}"
    for _ in range(attempts):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                break
        except OSError:
            time.sleep(0.2)
    try:
        webbrowser.open(url)
    except Exception:
        # No browser available (e.g. headless) — the URL is printed anyway.
        pass


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    os.chdir(here)
    os.environ.setdefault("DQ_DATA_DIR", os.path.join(here, "data"))

    port = int(os.environ.get("PORT") or _free_port())
    url = f"http://127.0.0.1:{port}"

    print("=" * 52)
    print("  Edge Over Variance")
    print(f"  Running locally at:  {url}")
    print("  Your browser will open automatically.")
    print("  Close this window (or press Ctrl-C) to stop.")
    print("=" * 52)

    if os.environ.get("DQ_NO_BROWSER") != "1":
        threading.Thread(target=_wait_then_open, args=(port,), daemon=True).start()

    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
