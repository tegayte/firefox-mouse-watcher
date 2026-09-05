import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {QuickToggle, SystemIndicator} from 'resource:///org/gnome/shell/ui/quickSettings.js';

// ---------------------------------------------------------------------------
// Absolute paths only
// ---------------------------------------------------------------------------
const HOME = GLib.get_home_dir();
const WATCHER_DIR = `${HOME}/firefox-watcher`;
const START_SCRIPT = `${WATCHER_DIR}/start-watcher.sh`;
const STOP_SCRIPT = `${WATCHER_DIR}/stop-watcher.sh`;
const BASH = '/bin/bash';

// ---------------------------------------------------------------------------
// Mouse Jiggler settings
// ---------------------------------------------------------------------------

// Random interval between 1.100 and 1.300 seconds.
const JIGGLE_MIN_INTERVAL_MS = 1100;
const JIGGLE_MAX_INTERVAL_MS = 1300;

// Choose a new random interval every 3 minutes.
const JIGGLE_CHANGE_INTERVAL_MS = 3 * 60 * 1000;

// Maximum movement in pixels.
const JIGGLE_MAX_DELTA = 3;

// Debug tag.
const DEBUG_TAG = 'mouse-jiggler-debug-v3';


const MouseJigglerToggle = GObject.registerClass(
class MouseJigglerToggle extends QuickToggle {
    _init() {
        super._init({
            title: 'Mouse Jiggler',
            iconName: 'input-mouse-symbolic',
            toggleMode: true,
        });

        console.log(`[${DEBUG_TAG}] toggle constructed`);

        // --- jiggle state ---
        this._virtualPointer = null;
        this._jiggleTimeoutId = 0;
        this._jiggleChangeTimeoutId = 0;
        this._currentJiggleInterval = 1200;

        // --- watcher process state ---
        this._watcherPid = 0;
        this._watcherChildWatchId = 0;
        this._watcherActive = false;

        this.connect('clicked', () => {
            console.log(`[${DEBUG_TAG}] clicked, checked=${this.checked}`);

            if (this.checked)
                this._start();
            else
                this._stop();
        });
    }

    _start() {
        if (this._jiggleTimeoutId !== 0) {
            console.log(
                `[${DEBUG_TAG}] _start() called but already running — ignoring`
            );
            return;
        }

        this._startJiggle();
        this._startWatcher();
    }

    _stop() {
        this._stopJiggle();
        this._stopWatcher();
    }

    // ----------------------- mouse jiggle -----------------------

    _startJiggle() {
        this._virtualPointer = Clutter.get_default_backend()
            .get_default_seat()
            .create_virtual_device(Clutter.InputDeviceType.POINTER);

        // Pick the first random interval immediately.
        this._currentJiggleInterval =
            JIGGLE_MIN_INTERVAL_MS +
            Math.random() *
            (JIGGLE_MAX_INTERVAL_MS - JIGGLE_MIN_INTERVAL_MS);

        console.log(
            `[${DEBUG_TAG}] initial jiggle interval: ` +
            `${(this._currentJiggleInterval / 1000).toFixed(3)}s`
        );

        const scheduleNextJiggle = () => {
            this._jiggleTimeoutId = GLib.timeout_add(
                GLib.PRIORITY_DEFAULT,
                Math.round(this._currentJiggleInterval),
                () => {
                    if (!this._virtualPointer) {
                        this._jiggleTimeoutId = 0;
                        return GLib.SOURCE_REMOVE;
                    }

                    const dx = GLib.random_int_range(
                        -JIGGLE_MAX_DELTA,
                        JIGGLE_MAX_DELTA + 1
                    );

                    const dy = GLib.random_int_range(
                        -JIGGLE_MAX_DELTA,
                        JIGGLE_MAX_DELTA + 1
                    );

                    this._virtualPointer.notify_relative_motion(
                        GLib.get_monotonic_time() / 1000,
                        dx,
                        dy
                    );

                    // Schedule the next movement using the current interval.
                    scheduleNextJiggle();

                    return GLib.SOURCE_REMOVE;
                }
            );
        };

        // Start the first movement.
        scheduleNextJiggle();

        // Every 3 minutes choose a completely new random interval.
        this._jiggleChangeTimeoutId = GLib.timeout_add(
            GLib.PRIORITY_DEFAULT,
            JIGGLE_CHANGE_INTERVAL_MS,
            () => {
                this._currentJiggleInterval =
                    JIGGLE_MIN_INTERVAL_MS +
                    Math.random() *
                    (JIGGLE_MAX_INTERVAL_MS - JIGGLE_MIN_INTERVAL_MS);

                console.log(
                    `[${DEBUG_TAG}] new jiggle interval: ` +
                    `${(this._currentJiggleInterval / 1000).toFixed(3)}s`
                );

                return GLib.SOURCE_CONTINUE;
            }
        );
    }

    _stopJiggle() {
        if (this._jiggleTimeoutId !== 0) {
            GLib.source_remove(this._jiggleTimeoutId);
            this._jiggleTimeoutId = 0;
        }

        if (this._jiggleChangeTimeoutId !== 0) {
            GLib.source_remove(this._jiggleChangeTimeoutId);
            this._jiggleChangeTimeoutId = 0;
        }

        this._virtualPointer = null;
    }

    // ----------------------- watcher.py process lifecycle -----------------------

    _startWatcher() {
        if (this._watcherActive) {
            console.log(
                `[${DEBUG_TAG}] start requested but watcher already active — skipping`
            );
            return;
        }

        const argv = [BASH, START_SCRIPT];

        console.log(
            `[${DEBUG_TAG}] spawning: ${argv.join(' ')} (cwd=${WATCHER_DIR})`
        );

        try {
            const [ok, pid] = GLib.spawn_async(
                WATCHER_DIR,
                argv,
                null,
                GLib.SpawnFlags.DO_NOT_REAP_CHILD,
                null
            );

            if (!ok || pid <= 0)
                throw new Error(
                    `spawn_async returned ok=${ok} pid=${pid}`
                );

            console.log(
                `[${DEBUG_TAG}] start-watcher launcher pid=${pid}`
            );

            this._watcherPid = pid;
            this._watcherActive = true;
            this.subtitle = null;

            this._watcherChildWatchId = GLib.child_watch_add(
                GLib.PRIORITY_DEFAULT,
                pid,
                (_pid, status) => {
                    console.log(
                        `[${DEBUG_TAG}] start-watcher launcher ` +
                        `(pid=${pid}) exited, status=${status}`
                    );

                    this._watcherChildWatchId = 0;
                    this._watcherPid = 0;

                    GLib.spawn_close_pid(pid);

                    if (status !== 0 && this._watcherActive) {
                        this._watcherActive = false;
                        this.subtitle = 'Watcher error';

                        console.error(
                            `[${DEBUG_TAG}] start-watcher.sh exited ` +
                            `with non-zero status ${status}`
                        );
                    }
                }
            );

        } catch (e) {
            this._watcherActive = false;
            this._watcherPid = 0;
            this.subtitle = 'Watcher error';

            console.error(
                `[${DEBUG_TAG}] failed to launch start-watcher.sh: ` +
                `${e.message ?? e}`
            );

            logError(
                e,
                `[${DEBUG_TAG}] spawn_async exception`
            );
        }
    }

    _stopWatcher() {
        if (!this._watcherActive && this._watcherPid === 0) {
            console.log(
                `[${DEBUG_TAG}] stop requested but watcher not active — ` +
                `skipping stop-watcher.sh`
            );
            return;
        }

        this._watcherActive = false;

        if (this._watcherChildWatchId !== 0) {
            GLib.source_remove(this._watcherChildWatchId);
            this._watcherChildWatchId = 0;
        }

        this._watcherPid = 0;

        const argv = [BASH, STOP_SCRIPT];

        console.log(
            `[${DEBUG_TAG}] spawning: ${argv.join(' ')} (cwd=${WATCHER_DIR})`
        );

        try {
            const [ok, pid] = GLib.spawn_async(
                WATCHER_DIR,
                argv,
                null,
                GLib.SpawnFlags.DO_NOT_REAP_CHILD,
                null
            );

            if (!ok || pid <= 0)
                throw new Error(
                    `spawn_async returned ok=${ok} pid=${pid}`
                );

            console.log(
                `[${DEBUG_TAG}] stop-watcher launcher pid=${pid}`
            );

            GLib.child_watch_add(
                GLib.PRIORITY_DEFAULT,
                pid,
                (_pid, status) => {
                    console.log(
                        `[${DEBUG_TAG}] stop-watcher launcher ` +
                        `(pid=${pid}) exited, status=${status}`
                    );

                    GLib.spawn_close_pid(pid);

                    if (status !== 0) {
                        console.error(
                            `[${DEBUG_TAG}] stop-watcher.sh exited ` +
                            `with non-zero status ${status}`
                        );
                    }
                }
            );

        } catch (e) {
            console.error(
                `[${DEBUG_TAG}] failed to launch stop-watcher.sh: ` +
                `${e.message ?? e}`
            );

            logError(
                e,
                `[${DEBUG_TAG}] spawn_async exception`
            );
        }

        this.subtitle = null;
    }

    destroy() {
        this._stop();
        super.destroy();
    }
});


const MouseJigglerIndicator = GObject.registerClass(
class MouseJigglerIndicator extends SystemIndicator {
    _init() {
        super._init();

        this._toggle = new MouseJigglerToggle();
        this.quickSettingsItems.push(this._toggle);
    }

    destroy() {
        this.quickSettingsItems.forEach(item => item.destroy());
        super.destroy();
    }
});


export default class MouseJigglerExtension extends Extension {
    enable() {
        console.log(`[${DEBUG_TAG}] enable() called`);

        this._indicator = new MouseJigglerIndicator();

        Main.panel.statusArea.quickSettings
            .addExternalIndicator(this._indicator);
    }

    disable() {
        console.log(`[${DEBUG_TAG}] disable() called`);

        this._indicator?.destroy();
        this._indicator = null;
    }
}
