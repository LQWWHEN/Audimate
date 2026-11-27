import QtQuick
import QtQuick.Layouts
import QtQuick.Controls

Rectangle {
    id: titleBar
    required property var shell
    required property var model
    readonly property var v: model.view
    height: shell.portrait ? 48 : 56
    color: "#181b2c"
    function focusSettings() { localButton.forceActiveFocus() }
    function toggleMax() {
        if (shell.visibility === Window.Maximized) shell.showNormal()
        else shell.showMaximized()
    }
    MouseArea {
        anchors.fill: parent
        onPressed: frameControl.pressTitle()
        onPositionChanged: if (pressed) frameControl.dragTitle()
        onReleased: frameControl.releaseTitle()
        onCanceled: frameControl.releaseTitle()
        onDoubleClicked: titleBar.toggleMax()
    }
    RowLayout {
        anchors.fill: parent; anchors.leftMargin: shell.portrait ? 12 : 24; anchors.rightMargin: 7
        spacing: shell.portrait ? 2 : 6
        Item {
            implicitWidth: 25; implicitHeight: 30
            Repeater {
                model: [12,24,16]
                Rectangle { required property int index; required property int modelData
                    x: 2+index*8; anchors.verticalCenter: parent.verticalCenter
                    width: 4; height: modelData; rotation: 12; radius: 2; color: "#bcddf0"
                }
            }
        }
        Word { text: "Audimate"; font.pixelSize: shell.portrait ? 21 : 28; font.weight: Font.DemiBold }
        Word { visible: !shell.portrait; Layout.leftMargin: 12; text: v.demo ? "本地试听" : "音乐房间"; color: shell.secondary }
        Item { Layout.fillWidth: true }
        Word { visible: v.entered && !v.demo; text: v.members + " 人"; color: shell.secondary; font.pixelSize: 14 }
        RoomButton {
            visible: v.entered && !shell.editingDefaults; quiet: true; horizontalPadding: shell.portrait ? 5 : 12
            text: shell.portrait ? "退出" : v.demo ? "退出试听" : "离开房间"; enabled: !v.busy
            onClicked: model.exitRoom()
        }
        WindowButton { id: localButton; objectName: "localSettingsButton"; symbol: "settings"
            description: "本地设置"; onClicked: shell.toggleDefaults() }
        WindowButton { objectName: "modeButton"; symbol: shell.portrait ? "landscape" : "portrait"
            description: shell.portrait ? "切换到横屏" : "切换到竖屏"; onClicked: frameControl.switchMode() }
        WindowButton { symbol: "min"; description: "最小化"; onClicked: shell.showMinimized() }
        WindowButton { symbol: shell.visibility === Window.Maximized ? "restore" : "max"
            description: shell.visibility === Window.Maximized ? "还原" : "最大化"; onClicked: titleBar.toggleMax() }
        WindowButton { symbol: "close"; description: "关闭窗口"; onClicked: shell.close() }
    }
    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: "#303447" }
}
