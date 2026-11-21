"""Run a loopback-only browser acceptance on the fixed PDF. No paid SDK is constructed."""

import argparse
import hashlib
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time

import uvicorn
from fastapi.staticfiles import StaticFiles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    pdf = args.pdf.resolve()
    if (
        hashlib.sha256(pdf.read_bytes()).hexdigest()
        != "a996da85e0abf6bb1a16cebbb82137af5ca8c16cadc81bc41358829a3687a6cf"
    ):
        parser.error("Expected the frozen Kim 2014 PDF")
    root = Path(__file__).resolve().parents[2]
    output = args.output.resolve()
    output.mkdir(exist_ok=False, parents=True)
    with tempfile.TemporaryDirectory() as private:
        os.environ.update(
            PAPER_REVIEW_DATA_DIR=private,
            OPENAI_MODEL="offline-fixture",
            OPENAI_API_KEY="fake-local",
            OPENAI_BASE_URL="https://fixture.invalid/v1",
        )
        from backend import app as module
        from backend.tests.compact_fake_sdk import CompactFakeSDK

        module.OpenAI = CompactFakeSDK
        module.MODEL_ENVIRONMENT_FILE = Path(private) / "offline.env"
        module.app.mount("/", StaticFiles(directory=root / "frontend/dist", html=True))
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            server = uvicorn.Server(uvicorn.Config(module.app, log_level="warning"))
            worker = threading.Thread(
                target=server.run, kwargs={"sockets": [sock]}, daemon=True
            )
            worker.start()
            try:
                for _ in range(200):
                    if server.started:
                        break
                    time.sleep(0.05)
                assert server.started
                subprocess.run(
                    [
                        "node",
                        str(root / "frontend/scripts/compact_demo.mjs"),
                        f"http://127.0.0.1:{sock.getsockname()[1]}",
                        str(pdf),
                        str(output),
                    ],
                    check=True,
                    timeout=120,
                )
                assert len(CompactFakeSDK.calls) == 2
            finally:
                server.should_exit = True
                worker.join(10)


if __name__ == "__main__":
    main()
