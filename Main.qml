import QtQuick
import Quickshell
import Quickshell.Io
import Caelestia.Plugins
import dcqwqc.gestures.services as GesturesPlugin

Item {
    id: root

    width: 0
    height: 0
    visible: false

    property SettingsObject settings: null
    readonly property bool daemonAlive: GesturesPlugin.Gestures.active
    readonly property string bin: `${Quickshell.env("HOME")}/.local/share/caelestia/plugins/gestures/daemon/mirai_gestures.py`
    property string lastWrittenValue: ""
    property string pendingValue: ""

    function applySettings(force = false): void {
        if (!root.settings)
            return;
        const value = root.settings.doubleClickFullscreen ? "1" : "0";
        if (!force && value === root.lastWrittenValue)
            return;
        root.pendingValue = value;
        if (!settingsProc.running) {
            settingsProc.command = [root.bin, "set-double-click-fullscreen", value];
            settingsProc.running = true;
        }
    }

    onSettingsChanged: root.applySettings(true)

    Component.onCompleted: {
        GesturesPlugin.Gestures.ensureStarted();
        root.applySettings(true);
    }

    Connections {
        target: root.settings
        function onChanged(): void { root.applySettings(true); }
    }

    Process {
        id: settingsProc
        running: false
        onExited: code => {
            if (code === 0)
                root.lastWrittenValue = settingsProc.command[2];
            if (root.pendingValue !== root.lastWrittenValue) {
                settingsProc.command = [root.bin, "set-double-click-fullscreen", root.pendingValue];
                settingsProc.running = true;
            }
        }
    }
}
