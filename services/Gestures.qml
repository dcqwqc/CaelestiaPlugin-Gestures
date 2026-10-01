pragma Singleton

import QtQuick
import Quickshell
import Quickshell.Io

Singleton {
    id: root

    readonly property string bin: `${Quickshell.env("HOME")}/.local/share/caelestia/plugins/gestures/daemon/mirai_gestures.py`
    property bool desiredActive: true
    readonly property bool available: true
    readonly property bool active: daemon.running
    readonly property bool busy: false

    function ensureStarted(): void {
        desiredActive = true;
    }

    function toggle(): void {
        desiredActive = !desiredActive;
    }

    Process {
        id: daemon
        command: [root.bin, "daemon"]
        running: root.desiredActive
        onExited: code => {
            if (root.desiredActive)
                restartTimer.restart();
        }
    }

    Timer {
        id: restartTimer
        interval: 1200
        repeat: false
        onTriggered: {
            if (root.desiredActive && !daemon.running) {
                root.desiredActive = false;
                root.desiredActive = true;
            }
        }
    }
}
