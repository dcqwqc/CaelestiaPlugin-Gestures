pragma Singleton

import QtQuick
import Quickshell
import Quickshell.Io

Singleton {
    id: root

    readonly property string bin: `${Quickshell.env("HOME")}/.local/bin/mirai-gestures`
    property bool available: false
    property bool active: false
    property bool busy: action.running || statusProc.running

    function refresh(): void {
        if (!busy)
            statusProc.running = true;
    }

    function toggle(): void {
        if (busy)
            return;
        action.command = [bin, "toggle"];
        action.running = true;
    }

    Process {
        id: statusProc
        command: [root.bin, "status-json"]
        running: false
        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    const value = JSON.parse(text);
                    root.available = !!value.available;
                    root.active = !!value.active;
                } catch (e) {
                    root.available = false;
                    root.active = false;
                }
            }
        }
    }

    Process {
        id: action
        running: false
        onExited: code => root.refresh()
    }

    Timer {
        interval: 3000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: root.refresh()
    }
}
