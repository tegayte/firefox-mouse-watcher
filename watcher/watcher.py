#!/usr/bin/env python3

import argparse
import json
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
import os
import socket
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


# ============================================================
# PATHS
# ============================================================

HOME = Path.home()

HUBSTAFF_DATA = (
    HOME / ".local/share/Hubstaff/data/hubstaff.com"
)

HUBSTAFF_LOG = (
    HOME / ".local/share/Hubstaff/logs/audit.log"
)

EVENTS_DIR = (
    HOME / ".local/share/hubstaff-watcher"
)

EVENTS_FILE = EVENTS_DIR / "events.jsonl"

# ============================================================
# FIREFOX
# ============================================================

FIREFOX_SOCKET = os.path.join(
    tempfile.gettempdir(),
    "nm_tab_switcher.sock",
)



# Hubstaff can emit duplicate CLOSE_WRITE events.
DEDUP_WINDOW_SEC = 3.0

# Number of recent event lines checked by append_event().
DEDUP_FILE_CHECK_LINES = 100


# ============================================================
# AUDIT STATE
# ============================================================

@dataclass
class AuditState:
    session_id: int | None = None
    tracking_active: bool = False
    active_run_id: int = 0

    last_event_time: datetime | None = None

    # These are counters for IDs assigned by this watcher.
    next_session_id: int = 1
    next_run_id: int = 1

    lock: threading.Lock | None = None

    def __post_init__(self):
        if self.lock is None:
            self.lock = threading.Lock()


# ============================================================
# BASIC UTILITIES
# ============================================================

def ensure_dirs():
    EVENTS_DIR.mkdir(parents=True, exist_ok=True)


def parse_audit_datetime(line: str):
    """
    Parse complete Hubstaff audit timestamp.

    Example:
    2026-08-12 21:26:14.524368+0500
    """

    match = re.match(
        r"^(\d{4}-\d{2}-\d{2} "
        r"\d{2}:\d{2}:\d{2}(?:\.\d+)?[+-]\d{4})",
        line,
    )

    if not match:
        return None

    value = match.group(1)

    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f%z",
        "%Y-%m-%d %H:%M:%S%z",
    ):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass

    return None


def event_type(line: str):
    if "(START_TRACKING)" in line:
        return "START_TRACKING"

    if "(STOP_TRACKING)" in line:
        return "STOP_TRACKING"

    if "(SHUTDOWN)" in line:
        return "SHUTDOWN"

    return None


# ============================================================
# SCREENSHOT DIRECTORY
# ============================================================

def discover_screens_dir():
    candidates = []

    if HUBSTAFF_DATA.exists():
        candidates = list(HUBSTAFF_DATA.glob("*/screens"))

    if not candidates:
        raise RuntimeError(
            "Не найден каталог Hubstaff screenshots.\n"
            f"Искал: {HUBSTAFF_DATA}/*/screens"
        )

    # Prefer the directory modified most recently.
    candidates.sort(
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    return candidates[0]


def is_primary_screenshot(filename: str):
    """
    Accept only Hubstaff primary screenshots.

    Expected:
        2026-08-12T163706-0.jpg

    Ignore:
        2026-08-12T163706-0-thumb.jpg
    """

    if not filename.lower().endswith(".jpg"):
        return False

    if filename.lower().endswith("-thumb.jpg"):
        return False

    return bool(
        re.match(
            r"^\d{4}-\d{2}-\d{2}T\d{6}-\d+\.jpg$",
            filename,
        )
    )


def screenshot_timestamp(filename: str):
    match = re.match(
        r"^(\d{4}-\d{2}-\d{2}T\d{6})-\d+\.jpg$",
        filename,
    )

    if not match:
        return None

    try:
        # Hubstaff stores screenshot filename timestamps in UTC.
        # Convert UTC -> local timezone when displaying later.
        dt = datetime.strptime(
            match.group(1),
            "%Y-%m-%dT%H%M%S",
        ).replace(tzinfo=timezone.utc)

        return dt.timestamp()

    except ValueError:
        return None


# ============================================================
# EVENTS.JSONL
# ============================================================

def append_event(ts: float, extra=None):
    """
    Append one screenshot event.

    A timestamp is considered the identity of a screenshot event.
    Duplicate timestamps are rejected, including duplicates already
    present from older watcher versions.
    """

    ensure_dirs()

    if EVENTS_FILE.exists():
        try:
            with EVENTS_FILE.open(
                "r",
                encoding="utf-8",
                errors="replace",
            ) as f:
                recent = f.readlines()[-DEDUP_FILE_CHECK_LINES:]

            for line in reversed(recent):
                if not line.strip():
                    continue

                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if record.get("ts") == ts:
                    print(
                        f"[watcher] duplicate screenshot ignored: {ts}"
                    )
                    return False

        except OSError:
            pass

    record = {
        "ts": ts,
        "iso": datetime.fromtimestamp(ts).isoformat(),
    }

    if extra:
        record.update(extra)

    try:
        with EVENTS_FILE.open(
            "a",
            encoding="utf-8",
        ) as f:
            f.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                )
                + "\n"
            )
    except OSError as exc:
        print(
            f"[watcher] не удалось записать событие: {exc}",
            file=sys.stderr,
        )
        return False

    return True


def load_events():
    ensure_dirs()

    if not EVENTS_FILE.exists():
        return []

    result = []

    try:
        with EVENTS_FILE.open(
            "r",
            encoding="utf-8",
            errors="replace",
        ) as f:

            for line in f:
                if not line.strip():
                    continue

                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if record.get("ts") is None:
                    continue

                result.append(record)

    except OSError:
        return []

    return result


# ============================================================
# EVENT CLEANING / INTERVALS
# ============================================================

def clean_events(events):
    """
    Return events sorted by timestamp with exact duplicate screenshot
    timestamps removed.

    Old events without session/run fields remain compatible.
    """

    cleaned = []
    seen_timestamps = set()

    for event in events:
        ts = event.get("ts")

        if ts is None:
            continue

        try:
            ts = float(ts)
        except (TypeError, ValueError):
            continue

        # Screenshot timestamp is the real event identity.
        if ts in seen_timestamps:
            continue

        seen_timestamps.add(ts)

        item = dict(event)
        item["ts"] = ts

        cleaned.append(item)

    cleaned.sort(key=lambda x: x["ts"])

    return cleaned


def valid_intervals(events):
    """
    Return only intervals that belong to the same continuous tracking run.

    The critical rule is:

        same session_id AND same active_run_id

    If old events do not contain these fields, they remain compatible:
    the interval can still be used unless the available metadata explicitly
    says that the events belong to different runs/sessions or outside tracking.
    """

    cleaned = clean_events(events)
    intervals = []

    for previous, current in zip(cleaned, cleaned[1:]):

        previous_ts = previous["ts"]
        current_ts = current["ts"]

        if current_ts <= previous_ts:
            continue

        previous_run = previous.get("active_run_id")
        current_run = current.get("active_run_id")

        previous_session = previous.get("session_id")
        current_session = current.get("session_id")

        # New-format events: different runs mean a pause/resume boundary.
        if (
            previous_run is not None
            and current_run is not None
            and previous_run != current_run
        ):
            continue

        # New-format events: different application sessions are never
        # continuous, even if the run IDs happen to look similar.
        if (
            previous_session is not None
            and current_session is not None
            and previous_session != current_session
        ):
            continue

        # Explicitly inactive screenshots cannot be used as endpoints.
        if (
            previous.get("tracking_active") is False
            or current.get("tracking_active") is False
        ):
            continue

        intervals.append(
            {
                "start_ts": previous_ts,
                "end_ts": current_ts,
                "minutes": (current_ts - previous_ts) / 60.0,
                "previous": previous,
                "current": current,
            }
        )

    return intervals


# ============================================================
# STATS
# ============================================================

def compute_stats(events):
    """
    Backward-looking screenshot statistics.

    Pauses are excluded by active_run_id/session_id filtering.
    Exact duplicate screenshot timestamps are excluded.
    """

    cleaned = clean_events(events)
    intervals = valid_intervals(events)
    values = [item["minutes"] for item in intervals]

    if not values:
        return {
            "count": len(cleaned),
            "valid_intervals": 0,
            "last_interval_min": None,
            "mean_min": None,
            "median_min": None,
            "min_min": None,
            "max_min": None,
            "stdev_min": None,
        }

    if len(values) >= 2:
        stdev = statistics.stdev(values)
    else:
        stdev = 0.0

    return {
        "count": len(cleaned),
        "valid_intervals": len(values),
        "last_interval_min": values[-1],
        "mean_min": statistics.mean(values),
        "median_min": statistics.median(values),
        "min_min": min(values),
        "max_min": max(values),
        "stdev_min": stdev,
    }


def print_stats():
    events = load_events()
    stats = compute_stats(events)

    print(
        json.dumps(
            {
                "count": stats["count"],
                "stats": stats,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


def print_dump():
    """
    Print only valid screenshot-to-screenshot intervals.

    A->B is shown only when both screenshots belong to the same
    continuous tracking run.
    """

    intervals = valid_intervals(load_events())

    for number, item in enumerate(intervals, start=1):
        print(
            f"{number}\t{item['minutes']:.2f}"
        )


# ============================================================
# PREDICTION
# ============================================================

def predict_next(last_ts: float, stats: dict):
    """
    Original simple prediction model:

        expected = last + mean

        window = mean ± stdev

    The lower bound is never earlier than the observed minimum
    interval.
    """

    mean = stats["mean_min"]
    stdev = stats["stdev_min"] or 1.0

    expected_ts = last_ts + mean * 60.0

    window_low_ts = (
        last_ts
        + max(mean - stdev, stats["min_min"]) * 60.0
    )

    window_high_ts = (
        last_ts
        + (mean + stdev) * 60.0
    )

    return (
        expected_ts,
        window_low_ts,
        window_high_ts,
    )


# ============================================================
# NOTIFICATION
# ============================================================

def send_notification(ts: float, events: list):
    stats = compute_stats(events)

    # Current local time at the moment the notification is created.
    current_str = datetime.now().astimezone().strftime(
        "%H:%M:%S"
    )

    # IMPORTANT:
    # The previous screenshot must be taken directly from the event list.
    # stats["last_interval_min"] is a historical statistic and is NOT
    # guaranteed to be the interval immediately preceding this screenshot.
    cleaned = clean_events(events)

    previous_event = None

    for index, event in enumerate(cleaned):
        if event["ts"] == ts:
            if index > 0:
                previous_event = cleaned[index - 1]
            break

    if previous_event is None:
        body = (
            f"Текущее время: {current_str}\n"
            "(предыдущего скриншота пока нет)\n"
            "(недостаточно данных для статистики)"
        )
    else:
        previous_screenshot_ts = previous_event["ts"]

        previous_str = datetime.fromtimestamp(
            previous_screenshot_ts
        ).astimezone().strftime("%H:%M:%S")

        # This is the REAL interval between the current screenshot
        # and the screenshot immediately preceding it.
        previous_interval_min = (
            ts - previous_screenshot_ts
        ) / 60.0

        expected_ts, low_ts, high_ts = predict_next(
            ts,
            stats,
        )

        expected_str = datetime.fromtimestamp(
            expected_ts
        ).astimezone().strftime("%H:%M")

        low_str = datetime.fromtimestamp(
            low_ts
        ).astimezone().strftime("%H:%M")

        high_str = datetime.fromtimestamp(
            high_ts
        ).astimezone().strftime("%H:%M")

        body = (
            f"Текущее время: {current_str}\n"
            f"Предыдущий скриншот: {previous_str}\n"
            f"Предыдущий интервал: "
            f"{previous_interval_min:.1f} мин\n"
            f"Средний интервал: "
            f"{stats['mean_min']:.1f} мин "
            f"(σ={stats['stdev_min']:.1f}, "
            f"n={stats['count']})\n\n"
            f"Следующий ожидаемый: ~{expected_str}\n"
            f"Вероятное окно: "
            f"{low_str}–{high_str}"
        )

    try:
        subprocess.run(
            [
                "notify-send",
                "-a",
                "Hubstaff Watcher",
                "📸 Hubstaff Screenshot",
                body,
            ],
            check=False,
        )
    except FileNotFoundError:
        print(
            "[!] notify-send не найден "
            "(sudo dnf install libnotify) — "
            "уведомление пропущено."
        )

    print(body)
    print("-" * 40)


# ============================================================
# AUDIT STATE MACHINE
# ============================================================

def apply_audit_event(
    state: AuditState,
    kind,
    dt,
    verbose=True,
):
    """
    START_TRACKING
        - new application session if none exists
        - new active_run if resuming after STOP

    STOP_TRACKING
        - pause only
        - same application session
        - same run ID until resume

    SHUTDOWN
        - application session ends
        - tracking becomes inactive
        - session_id becomes None
    """

    with state.lock:

        if kind == "START_TRACKING":

            if state.session_id is None:
                state.session_id = state.next_session_id
                state.next_session_id += 1

                state.active_run_id = state.next_run_id
                state.next_run_id += 1

            elif not state.tracking_active:
                # Resume inside the SAME application session.
                state.active_run_id = state.next_run_id
                state.next_run_id += 1

            state.tracking_active = True

            if verbose:
                print(
                    f"[audit] START_TRACKING @ "
                    f"{dt.isoformat()} -> "
                    f"session={state.session_id}, "
                    f"run={state.active_run_id}"
                )

        elif kind == "STOP_TRACKING":

            if state.session_id is not None:
                state.tracking_active = False

            if verbose:
                print(
                    f"[audit] STOP_TRACKING @ "
                    f"{dt.isoformat()} -> "
                    f"session={state.session_id}, "
                    f"run={state.active_run_id}"
                )

        elif kind == "SHUTDOWN":

            if verbose:
                print(
                    f"[audit] SHUTDOWN @ "
                    f"{dt.isoformat()} -> "
                    f"session={state.session_id}"
                )

            state.tracking_active = False
            state.session_id = None

        state.last_event_time = dt


# ============================================================
# AUDIT LOG PARSING
# ============================================================

def parse_audit_events():
    """
    Read the complete audit.log.

    Full datetimes are retained and used for sorting.
    """

    if not HUBSTAFF_LOG.exists():
        return []

    events = []

    try:
        with HUBSTAFF_LOG.open(
            "r",
            encoding="utf-8",
            errors="replace",
        ) as f:

            for line in f:

                kind = event_type(line)

                if kind is None:
                    continue

                dt = parse_audit_datetime(line)

                if dt is None:
                    continue

                events.append((dt, kind))

    except OSError as exc:
        print(
            f"[audit] error reading log: {exc}",
            file=sys.stderr,
        )

    events.sort(key=lambda x: x[0])

    return events


def bootstrap_audit_state(state: AuditState):
    """
    Restore the current Hubstaff state from the whole audit history.

    Returns the current file size so the live watcher can continue from
    exactly this point.
    """

    events = parse_audit_events()

    for dt, kind in events:
        apply_audit_event(
            state,
            kind,
            dt,
            verbose=False,
        )

    try:
        offset = HUBSTAFF_LOG.stat().st_size
    except OSError:
        offset = 0

    if events:
        print(
            "[audit] стартовое состояние восстановлено: "
            f"session={state.session_id}, "
            f"tracking={state.tracking_active}, "
            f"run={state.active_run_id}"
        )
    else:
        print(
            "[audit] audit.log не содержит известных "
            "START/STOP/SHUTDOWN событий."
        )

    return offset


# ============================================================
# BUILD APPLICATION SESSIONS
# ============================================================

def build_sessions():
    """
    Build application sessions independently from events.jsonl.

    START_TRACKING -> starts a new application session if none exists.
    STOP_TRACKING  -> creates a pause inside that session.
    START after STOP -> resumes the same application session.
    SHUTDOWN       -> ends the application session.

    All timestamps remain full datetime objects, so crossing midnight
    cannot produce negative-looking periods.
    """

    events = parse_audit_events()

    sessions = []

    current = None
    active_start = None
    pause_start = None

    session_number = 0

    for dt, kind in events:

        if kind == "START_TRACKING":

            if current is None:
                session_number += 1

                current = {
                    "id": session_number,
                    "start": dt,
                    "active": [],
                    "pauses": [],
                    "shutdown": None,
                }

                active_start = dt
                pause_start = None

            elif active_start is None:
                # Resume same application session.
                if pause_start is not None:
                    current["pauses"].append(
                        (pause_start, dt)
                    )
                    pause_start = None

                active_start = dt

        elif kind == "STOP_TRACKING":

            if (
                current is not None
                and active_start is not None
            ):
                current["active"].append(
                    (active_start, dt)
                )

                active_start = None
                pause_start = dt

        elif kind == "SHUTDOWN":

            if current is None:
                continue

            if active_start is not None:
                current["active"].append(
                    (active_start, dt)
                )
                active_start = None

            elif pause_start is not None:
                current["pauses"].append(
                    (pause_start, dt)
                )
                pause_start = None

            current["shutdown"] = dt

            sessions.append(current)

            current = None

    # Current application may still be running.
    if current is not None:
        sessions.append(current)

    return sessions


def format_dt(dt):
    if dt is None:
        return "—"

    return dt.astimezone().strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def duration_minutes(start, end):
    if start is None or end is None:
        return 0.0

    seconds = (
        end - start
    ).total_seconds()

    return max(0.0, seconds / 60.0)


def print_sessions():
    sessions = build_sessions()

    if not sessions:
        print("Сессий не найдено.")
        return

    for session in sessions:

        print()
        print(
            f"Session #{session['id']}"
        )

        print(
            f"  Start:    "
            f"{format_dt(session['start'])}"
        )

        total_active = 0.0
        total_pause = 0.0

        for start, end in session["active"]:

            total_active += duration_minutes(
                start,
                end,
            )

            print(
                f"  Active : "
                f"{format_dt(start)} → "
                f"{format_dt(end)}"
            )

        for start, end in session["pauses"]:

            total_pause += duration_minutes(
                start,
                end,
            )

            print(
                f"  Pause  : "
                f"{format_dt(start)} → "
                f"{format_dt(end)}"
            )

        shutdown = session["shutdown"]

        if shutdown:
            print(
                f"  Shutdown: "
                f"{format_dt(shutdown)}"
            )
        else:
            print(
                "  Shutdown: "
                "application still running"
            )

        print(
            f"  Total active time: "
            f"{total_active:.1f} min"
        )

        print(
            f"  Total pause time:  "
            f"{total_pause:.1f} min"
        )

        print(
            f"  Pause/resume count: "
            f"{len(session['pauses'])}"
        )


# ============================================================
# LIVE AUDIT WATCHER
# ============================================================

def watch_audit_log(
    state: AuditState,
    stop_event: threading.Event,
    initial_offset: int,
):
    """
    Continue watching audit.log after synchronous bootstrap.

    This prevents the screenshot watcher from starting before the initial
    Hubstaff state has been restored.
    """

    if not HUBSTAFF_LOG.exists():
        print(
            f"[audit] log не найден: {HUBSTAFF_LOG}",
            file=sys.stderr,
        )
        return

    offset = initial_offset

    while not stop_event.is_set():

        try:
            size = HUBSTAFF_LOG.stat().st_size

            if size < offset:
                # Log rotation/truncation.
                offset = 0

            if size > offset:

                with HUBSTAFF_LOG.open(
                    "r",
                    encoding="utf-8",
                    errors="replace",
                ) as f:

                    f.seek(offset)

                    for line in f:

                        kind = event_type(line)

                        if kind is None:
                            continue

                        dt = parse_audit_datetime(line)

                        if dt is None:
                            continue

                        apply_audit_event(
                            state,
                            kind,
                            dt,
                            verbose=True,
                        )

                    offset = f.tell()

        except OSError as exc:
            print(
                f"[audit] watcher error: {exc}",
                file=sys.stderr,
            )

        stop_event.wait(0.5)


# ============================================================
# SCREENSHOT HANDLING
# ============================================================

# ============================================================
# FIREFOX COMMAND
# ============================================================

def send_firefox_command():
    """
    Send a command to Firefox to activate the next tab.

    The Firefox extension itself determines:
    - which tab is currently active;
    - which tab is next;
    - when to wrap from the last tab to tab 0.

    This function does NOT detect screenshot events.
    It only sends a command after an event has already
    been detected by the watcher.
    """

    command = {
        "command": "next_tab",
    }

    if not os.path.exists(FIREFOX_SOCKET):
        print(
            f"[firefox] socket not found: "
            f"{FIREFOX_SOCKET}"
        )

        print(
            "[firefox] command skipped; "
            "Firefox extension is not connected."
        )

        return False

    try:
        with socket.socket(
            socket.AF_UNIX,
            socket.SOCK_STREAM,
        ) as client:

            client.settimeout(5)
            client.connect(FIREFOX_SOCKET)

            client.sendall(
                json.dumps(command).encode("utf-8")
            )

            response_data = client.recv(65536)

        if not response_data:
            print(
                "[firefox] no response from native host"
            )

            return False

        response = json.loads(
            response_data.decode("utf-8")
        )

        if response.get("status") == "ok":

            print(
                "[firefox] switched "
                f"{response.get('from')} → "
                f"{response.get('to')} "
                f"(tabs: "
                f"{response.get('total_tabs')})"
            )

            return True

        print(
            f"[firefox] command failed: "
            f"{response}"
        )

        return False

    except (
        ConnectionRefusedError,
        FileNotFoundError,
    ):
        print(
            "[firefox] native host socket unavailable"
        )

        return False

    except socket.timeout:
        print(
            "[firefox] timeout waiting for response"
        )

        return False

    except (
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(
            f"[firefox] communication error: {exc}"
        )

        return False
        
def handle_detection(
    filename,
    audit_state=None,
):
    ts = screenshot_timestamp(filename)

    if ts is None:
        print(
            f"[watcher] не смог определить timestamp: "
            f"{filename}"
        )
        return

    extra = {}

    if audit_state is not None:

        with audit_state.lock:
            extra = {
                "session_id": audit_state.session_id,
                "tracking_active": audit_state.tracking_active,
                "active_run_id": audit_state.active_run_id,
            }

    if not append_event(ts, extra):
        return
        
    send_firefox_command()

    local_dt = datetime.fromtimestamp(ts)

    print(
        f"[watcher] обнаружен screenshot: "
        f"{filename}"
    )

    print(
        f"Время: "
        f"{local_dt.strftime('%Y-%m-%d %H:%M:%S')}"
    )

    if extra:
        print(
            f"Session: {extra['session_id']}"
        )
        print(
            f"Tracking: {extra['tracking_active']}"
        )
        print(
            f"Run: {extra['active_run_id']}"
        )

    print("-" * 40)

    # Keep the original prediction + notification pipeline.
    events = load_events()
    send_notification(ts, events)


# ============================================================
# SCREENSHOT WATCHER
# ============================================================

def watch_loop(
    audit_state=None,
    stop_event=None,
):
    if shutil.which("inotifywait") is None:
        raise RuntimeError(
            "inotifywait не найден.\n"
            "Установи:\n"
            "sudo dnf install inotify-tools"
        )

    if stop_event is None:
        stop_event = threading.Event()

    screens_dir = discover_screens_dir()

    print(
        f"[watcher] слежу за: {screens_dir}"
    )

    print(
        "[watcher] жду CLOSE_WRITE "
        "на *.jpg (кроме *-thumb.jpg)..."
    )

    proc = subprocess.Popen(
        [
            "inotifywait",
            "-m",
            "-e",
            "close_write",
            "--format",
            "%f",
            str(screens_dir),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    last_seen_primary = {}

    try:

        for line in proc.stdout:

            if stop_event.is_set():
                break

            filename = line.strip()

            if not filename:
                continue

            if not is_primary_screenshot(filename):
                print(
                    f"[watcher] игнорирую: {filename}"
                )
                continue

            now = time.monotonic()

            previous = last_seen_primary.get(
                filename
            )

            if (
                previous is not None
                and now - previous < DEDUP_WINDOW_SEC
            ):
                print(
                    "[watcher] повторный CLOSE_WRITE "
                    f"проигнорирован (дубль): "
                    f"{filename}"
                )
                continue

            last_seen_primary[filename] = now

            handle_detection(
                filename,
                audit_state=audit_state,
            )

            cutoff = (
                now - DEDUP_WINDOW_SEC * 10
            )

            last_seen_primary = {
                name: seen
                for name, seen
                in last_seen_primary.items()
                if seen >= cutoff
            }

    except KeyboardInterrupt:
        pass

    finally:
        proc.terminate()

        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


# ============================================================
# MANUAL EVENT
# ============================================================

def manual_event():
    """
    Add a manual test event.

    It deliberately has None for tracking metadata so old behavior
    remains available for testing notification/statistics.
    """

    ts = time.time()

    if not append_event(
        ts,
        {
            "session_id": None,
            "tracking_active": None,
            "active_run_id": None,
            "manual": True,
        },
    ):
        return

    print(
        f"[manual] event added: "
        f"{datetime.fromtimestamp(ts)}"
    )

    events = load_events()
    send_notification(ts, events)


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Hubstaff screenshot/session "
            "bookkeeping watcher"
        )
    )

    parser.add_argument(
        "--manual",
        action="store_true",
        help="добавить тестовое событие",
    )

    parser.add_argument(
        "--stats",
        action="store_true",
        help="показать статистику",
    )

    parser.add_argument(
        "--dump",
        action="store_true",
        help="показать валидные интервалы",
    )

    parser.add_argument(
        "--sessions",
        action="store_true",
        help="показать application sessions",
    )

    args = parser.parse_args()

    ensure_dirs()

    if args.manual:
        manual_event()
        return

    if args.stats:
        print_stats()
        return

    if args.dump:
        print_dump()
        return

    if args.sessions:
        print_sessions()
        return

    # --------------------------------------------------------
    # REAL WATCHER
    # --------------------------------------------------------

    state = AuditState()

    # IMPORTANT:
    # Restore Hubstaff state BEFORE starting screenshot watcher.
    audit_offset = bootstrap_audit_state(state)

    stop_event = threading.Event()

    audit_thread = threading.Thread(
        target=watch_audit_log,
        args=(
            state,
            stop_event,
            audit_offset,
        ),
        daemon=True,
        name="audit-watcher",
    )

    audit_thread.start()

    try:
        watch_loop(
            audit_state=state,
            stop_event=stop_event,
        )

    except KeyboardInterrupt:
        pass

    finally:
        stop_event.set()

        audit_thread.join(timeout=2)


if __name__ == "__main__":
    main()
