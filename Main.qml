import QtQuick
import dcqwqc.gestures.services as GesturesPlugin

Item {
    width: 0
    height: 0
    visible: false
    readonly property bool daemonAlive: GesturesPlugin.Gestures.active
    Component.onCompleted: GesturesPlugin.Gestures.ensureStarted()
}
