"""
run_server.py
Windows-safe launcher. The uvicorn CLI's --reload mode resets the asyncio
event loop policy on Windows (needed for watchfiles), which breaks
Playwright's subprocess support. Running uvicorn programmatically like this,
without --reload, keeps our WindowsProactorEventLoopPolicy in effect so
Playwright can launch the browser correctly.

Run:
    python run_server.py
Then open http://127.0.0.1:8000
"""

import asyncio
import os
import socket
import sys

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import uvicorn


def _port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((host, port)) != 0


def _select_port(host: str, preferred_port: int) -> int:
    if "PORT" in os.environ:
        if not _port_is_free(host, preferred_port):
            raise SystemExit(
                f"Port {preferred_port} is already in use on {host}. "
                "Stop the existing server or set PORT to another value."
            )
        return preferred_port

    for port in range(preferred_port, preferred_port + 25):
        if _port_is_free(host, port):
            return port
    raise SystemExit(f"No free port found on {host} from {preferred_port} to {preferred_port + 24}.")


if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = _select_port(host, int(os.getenv("PORT", "8000")))
    display_host = "127.0.0.1" if host == "0.0.0.0" else host
    print(f"Dashboard: http://{display_host}:{port}")
    uvicorn.run("server:app", host=host, port=port, reload=False, log_level="info")
