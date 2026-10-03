import Caelestia.Plugins

SettingsObject {
    property bool doubleClickFullscreen: true
    SettingMeta on doubleClickFullscreen {
        label: "Double-click to fullscreen"
        description: "Double-click any window to enter fullscreen. Double-click again to leave fullscreen."
        icon: "fullscreen"
        inputType: SettingMeta.Switch
    }
}
