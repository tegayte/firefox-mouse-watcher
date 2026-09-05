#!/usr/bin/env python3
"""
nm_host.py — Native Messaging Host for the Native Tab Switcher POC.

This process is spawned automatically by Firefox when the extension calls
browser.runtime.connectNative(). It must speak the standard Firefox Native
Messaging wire protocol on stdin/stdout: each message is a 4-byte
length-prefixed UTF-8 JSON blob (see MDN "Native messaging" docs).

Since Firefox is the only process allowed to talk to this host over stdio,
this host additionally opens a local Unix domain socket
(/tmp/nm_tab_switcher.sock) so that an independent script (test_host.py)
can trigger a command without violating the Native Messaging protocol.

Flow for one test command:
    test_host.py --(unix socket)--> nm_host.py --(stdout, NM protocol)--> Firefox
    Firefox --(extension logic)--> nm_host.py --(stdin, NM protocol)--> back to socket client
"""

import json
import logging
import os
import queue
import socket
import struct
import sys
import tempfile
import threading

SOCKET_PATH = os.path.join(tempfile.gettempdir(), "nm_tab_switcher.sock")
LOG_PATH = os.path.join(tempfile.gettempdir(), "nm_host.log")

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.DEBUG,
    format="%(asctime)s [%(threadName)s] %(message)s",
)

# Queue of messages received from the extension (over stdin), consumed by
# whichever socket client is currently waiting for a response.
response_queue = queue.Queue()

# Native Messaging requires all writes to stdout to be framed correctly;
# guard against interleaved writes from multiple socket clients.
stdout_lock = threading.Lock()


def read_message():
    """Read one Native Messaging message from stdin. Returns dict, or None on EOF."""
    raw_length = sys.stdin.buffer.read(4)
    if len(raw_length) == 0:
        return None
    if len(raw_length) < 4:
        raise IOError("Truncated length header while reading from Firefox.")

    message_length = struct.unpack("@I", raw_length)[0]
    raw_message = sys.stdin.buffer.read(message_length)
    if len(raw_message) < message_length:
        raise IOError("Truncated message body while reading from Firefox.")

    return json.loads(raw_message.decode("utf-8"))


def send_message(message_dict):
    """Write one Native Messaging message to stdout."""
    encoded_content = json.dumps(message_dict).encode("utf-8")
    encoded_length = struct.pack("@I", len(encoded_content))
    with stdout_lock:
        sys.stdout.buffer.write(encoded_length)
        sys.stdout.buffer.write(encoded_content)
        sys.stdout.buffer.flush()


def cleanup_socket():
    try:
        if os.path.exists(SOCKET_PATH):
            os.remove(SOCKET_PATH)
    except OSError:
        logging.exception("Failed to remove socket file on shutdown.")


def stdin_reader_loop():
    """Continuously read responses/messages coming back from the extension."""
    logging.info("stdin reader thread started.")
    while True:
        try:
            message = read_message()
        except Exception:
            logging.exception("Error reading from stdin, shutting down.")
            break

        if message is None:
            logging.info("Firefox closed the connection (EOF on stdin).")
            break

        logging.debug("Received from extension: %s", message)
        response_queue.put(message)

    # Unblock any socket client still waiting, then terminate the process.
    response_queue.put(None)
    cleanup_socket()
    os._exit(0)


def handle_socket_client(conn):
    try:
        conn.settimeout(5)
        data = conn.recv(65536)
        if not data:
            return

        try:
            command = json.loads(data.decode("utf-8"))
        except json.JSONDecodeError as exc:
            error = {"status": "error", "message": f"Invalid JSON from test client: {exc}"}
            conn.sendall(json.dumps(error).encode("utf-8"))
            return

        # Drain any stale responses so we don't accidentally hand back a
        # response meant for a previous command.
        while not response_queue.empty():
            try:
                response_queue.get_nowait()
            except queue.Empty:
                break

        logging.debug("Forwarding command to extension: %s", command)
        send_message(command)

        try:
            response = response_queue.get(timeout=5)
        except queue.Empty:
            response = {"status": "error", "message": "Timed out waiting for extension response."}

        if response is None:
            response = {"status": "error", "message": "Native messaging channel closed by Firefox."}

        conn.sendall(json.dumps(response).encode("utf-8"))

    except Exception as exc:
        logging.exception("Error handling socket client.")
        try:
            conn.sendall(json.dumps({"status": "error", "message": str(exc)}).encode("utf-8"))
        except Exception:
            pass
    finally:
        conn.close()


def socket_server_loop():
    cleanup_socket()

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET_PATH)
    os.chmod(SOCKET_PATH, 0o600)
    server.listen(5)
    logging.info("Listening for test_host.py connections on %s", SOCKET_PATH)

    while True:
        try:
            conn, _ = server.accept()
        except OSError:
            logging.info("Socket server stopped.")
            break
        threading.Thread(target=handle_socket_client, args=(conn,), daemon=True).start()


def main():
    logging.info("nm_host.py started, pid=%s", os.getpid())
    reader_thread = threading.Thread(target=stdin_reader_loop, name="stdin-reader", daemon=True)
    reader_thread.start()
    try:
        socket_server_loop()
    finally:
        cleanup_socket()


if __name__ == "__main__":
    main()
