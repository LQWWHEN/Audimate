import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

FocusScope {
    id: page
    required property var roomModel
    required property bool portrait
    property int section: 0
    property string error: ""
    property var intervalChoices: [5,10,30]
    readonly property alias preview: previewBox
    readonly property bool showDisc: visible && section === 0 && roomModel.view.entered
    signal finished()

    function load() {
        const prefs = roomModel.localDefaults
        error = ""
        section = 0
        delay.value = prefs.delay
        motion.currentIndex = ["system","full","reduced"].indexOf(prefs.motion)
        host.load(prefs)
        receive.currentIndex = prefs.receive === "Opus" ? 0 : 1
        autoSync.checked = prefs.auto
        intervalChoices = Array.from(new Set(roomModel.view.intervals.concat([prefs.interval]))).sort((a,b) => a-b)
        interval.currentIndex = intervalChoices.indexOf(prefs.interval)
    }
    function cancel() {
        roomModel.previewDelay(-1)
        finished()
    }
    function save() {
        const values = Object.assign({}, host.values, {
            delay: Math.round(delay.value), motion: ["system","full","reduced"][motion.currentIndex],
            receive: receive.currentIndex === 0 ? "Opus" : "无损 PCM",
            auto: autoSync.checked, interval: intervalChoices[interval.currentIndex]
        })
        error = roomModel.saveDefaults(values)
        if (!error) cancel()
    }
    Shortcut { sequence: "Escape"; enabled: page.visible; onActivated: page.cancel() }
    ColumnLayout {
        anchors { top: parent.top; bottom: parent.bottom; horizontalCenter: parent.horizontalCenter }
        anchors.topMargin: page.portrait ? 12 : 16; anchors.bottomMargin: 12
        width: Math.min(620, parent.width - (page.portrait ? 32 : 72))
        spacing: 8
        Word { text: "本地设置"; font.pixelSize: page.portrait ? 28 : 32; font.weight: Font.DemiBold }
        RowLayout {
            Layout.fillWidth: true; spacing: 16
            Repeater {
                model: ["音画校准", "主持默认值", "收听偏好"]
                AbstractButton {
                    id: tab; required property string modelData; required property int index
                    objectName: "section"+index; text: modelData
                    implicitWidth: label.implicitWidth+16; implicitHeight: 38
                    checkable: true; checked: page.section === index
                    onClicked: page.section = index
                    contentItem: Word { id: label; text: tab.text; horizontalAlignment: Text.AlignHCenter
                        font.weight: tab.checked ? Font.DemiBold : Font.Normal; color: tab.checked ? "#e7e4ff" : "#b6b9ce" }
                    background: Rectangle {
                        radius: 4; color: tab.hovered ? "#272d41" : "transparent"
                        border.width: tab.visualFocus ? 1 : 0; border.color: "#a2ddfa"
                        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 2; color: "#c8c1f3"; visible: tab.checked }
                    }
                }
            }
            Item { Layout.fillWidth: true }
        }
        Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: "#353b50" }
        ScrollView {
            Layout.fillWidth: true; Layout.fillHeight: true; clip: true
            contentWidth: availableWidth
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                width: parent.width; spacing: 10
                ColumnLayout {
                    visible: page.section === 0; Layout.fillWidth: true; spacing: 8
                    RowLayout {
                        Layout.fillWidth: true; Layout.topMargin: 10
                        ColumnLayout {
                            Layout.fillWidth: true; spacing: 0
                            Word { text: "本机播放延迟"; color: "#bdc5db" }
                            RowLayout {
                                spacing: 7
                                Word { text: Math.round(delay.value); font.pixelSize: 46; font.weight: Font.DemiBold }
                                Word { text: "ms"; color: "#b4cfe5"; font.pixelSize: 20; Layout.alignment: Qt.AlignBaseline }
                            }
                        }
                        Item { id: previewBox; visible: page.showDisc; Layout.preferredWidth: 110; Layout.preferredHeight: 100 }
                    }
                    Slider { id: delay; objectName: "playbackDelay"; Layout.fillWidth: true
                        from: 0; to: 200; stepSize: 5; snapMode: Slider.SnapAlways; Accessible.name: "本机播放延迟（毫秒）"
                        onMoved: page.roomModel.previewDelay(Math.round(value)) }
                    RowLayout {
                        Layout.fillWidth: true
                        Word { text: "0 ms"; font.pixelSize: 14; color: "#aab1c9" }
                        Item { Layout.fillWidth: true }
                        Word { text: "200 ms"; font.pixelSize: 14; color: "#aab1c9" }
                    }
                    Word { Layout.fillWidth: true; wrapMode: Text.Wrap; color: "#bcc2d6"
                        text: "鼓点先响、圆环后动时，向右调整。校准作用于本机收听和试听。主持时，声音由所选播放器直接输出。" }
                    RowLayout {
                        Layout.fillWidth: true; Layout.topMargin: 10
                        Word { text: "动画幅度"; Layout.fillWidth: true }
                        ComboBox { id: motion; model: ["跟随系统","完整动态","减弱动态"]; Accessible.name: "动画幅度" }
                    }
                }
                ColumnLayout {
                    visible: page.section === 1; Layout.fillWidth: true; spacing: 8
                    Word { Layout.fillWidth: true; Layout.topMargin: 6; wrapMode: Text.Wrap; color: "#bcc2d6"; text: "下次主持时使用，也可在选源时临时调整。" }
                    HostFields { id: host; Layout.fillWidth: true }
                }
                ColumnLayout {
                    visible: page.section === 2; Layout.fillWidth: true; spacing: 10
                    Word { Layout.topMargin: 8; text: "优先接收" }
                    ComboBox { id: receive; Layout.fillWidth: true; model: ["Opus · 节省流量","优先无损"]; Accessible.name: "优先接收" }
                    CheckBox { id: autoSync; text: "延迟持续过高时自动同步" }
                    RowLayout { Layout.fillWidth: true; enabled: autoSync.checked
                        Word { text: "同步间隔（秒）"; Layout.fillWidth: true }
                        ComboBox { id: interval; model: page.intervalChoices; Accessible.name: "同步间隔（秒）" }
                    }
                }
            }
        }
        Word { visible: !!page.error; text: page.error; Layout.fillWidth: true; wrapMode: Text.Wrap; color: "#f4ba92" }
        RowLayout {
            Layout.fillWidth: true; spacing: 10
            Item { Layout.fillWidth: true }
            RoomButton { text: "取消"; quiet: true; onClicked: page.cancel() }
            RoomButton { text: "保存并返回"; objectName: "saveDefaults"; primary: true; onClicked: page.save() }
        }
    }
}
