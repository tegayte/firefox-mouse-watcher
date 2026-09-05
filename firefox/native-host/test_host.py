#!/usr/bin/env python3

import json
import socket
import sys
import tempfile


SOCKET_PATH = f"{tempfile.gettempdir()}/nm_tab_switcher.sock"


def main():
    command = {
        "command": "next_tab"
    }

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5)
            sock.connect(SOCKET_PATH)

            payload = json.dumps(command).encode("utf-8")
            sock.sendall(payload)

            response = sock.recv(65536)

        print(f"Sent command:    {json.dumps(command)}")
        print(f"Received response: {response.decode('utf-8')}")

    except FileNotFoundError:
        print(f"ERROR: socket not found: {SOCKET_PATH}")
        print("Is the watcher running?")
        sys.exit(1)

    except ConnectionRefusedError:
        print(f"ERROR: connection refused: {SOCKET_PATH}")
        sys.exit(1)

    except TimeoutError:
        print("ERROR: timed out waiting for response")
        sys.exit(1)

    except Exception as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
