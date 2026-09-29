import QtQuick
import Caelestia.Config
import qs.components
import qs.services
import dcqwqc.gestures.services as Gest

StyledRect {
    id: root

    property bool fillWidth: true
    property bool shapeMorph: true
    property real shapeMorphExpansion: 0

    implicitWidth: implicitHeight
    implicitHeight: icon.implicitHeight + Tokens.padding.small * 2
    visible: Gest.Gestures.available
    enabled: !Gest.Gestures.busy

    readonly property bool on: Gest.Gestures.active
    radius: Math.min(width, height) / 2 * Math.min(1, Tokens.rounding.scale)
    color: on ? Colours.palette.m3primary : Colours.layer(Colours.palette.m3surfaceContainerHighest, 2)

    StateLayer {
        id: layer
        color: root.on ? Colours.palette.m3onPrimary : Colours.palette.m3onSurfaceVariant
        disabled: !root.enabled
        radius: root.radius
        onClicked: Gest.Gestures.toggle()
    }

    MaterialIcon {
        id: icon
        anchors.centerIn: parent
        anchors.verticalCenterOffset: 1
        text: "gesture"
        color: root.on ? Colours.palette.m3onPrimary : Colours.palette.m3onSurfaceVariant
        fill: root.on ? 1 : 0
        fontStyle: Tokens.font.icon.medium
    }

    onVisibleChanged: if (visible) Gest.Gestures.refresh()
}
