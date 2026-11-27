import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Sheet {
    id: sheet
    property var room
    property var v: room.view
    property bool expanded: false
    property string selected: ""
    property var filtered: []
    function filter() {
        const term = search.text.toLowerCase().trim()
        filtered = room.sources.filter(s => (s.name+" "+s.title+" "+s.pid).toLowerCase().includes(term))
        if (!filtered.some(s => s.key === selected)) selected = ""
    }
    onOpened: {
        selected = ""; expanded = false; search.clear(); filter()
        codec.currentIndex = 0; recognition.checked = false; limit.clear()
        style.currentIndex = Math.max(0, ["pop","heavy","light"].indexOf(v.style))
        room.listSources(); search.forceActiveFocus()
    }
    Connections {
        target: sheet.room
        function onSourcesChanged() { sheet.filter() }
        function onSubmitted() { sheet.close() }
    }
    heading: "选择共享音源"
    RowLayout {
        Layout.fillWidth: true
        TextField { id: search; Layout.fillWidth: true; placeholderText: "搜索播放器、窗口或 PID"; onTextChanged: sheet.filter() }
        RoomButton { text: "刷新"; onClicked: sheet.room.listSources() }
    }
    ListView {
        id: list
        Layout.fillWidth: true; Layout.preferredHeight: 166
        clip: true; spacing: 2; model: sheet.filtered
        ScrollBar.vertical: ScrollBar {}
        delegate: ItemDelegate {
            required property var modelData
            width: list.width; height: 43
            highlighted: sheet.selected === modelData.key
            contentItem: RowLayout {
                Word { text: modelData.name; font.weight: Font.DemiBold; Layout.preferredWidth: 128 }
                Word { text: modelData.title || ("PID "+modelData.pid); color: "#b9bdd3"; Layout.fillWidth: true }
            }
            onClicked: sheet.selected = modelData.key
        }
        Word { anchors.centerIn: parent; visible: !list.count; text: "没有匹配进程，可刷新或更换搜索词"; color: "#b9bdd3" }
    }
    RowLayout {
        Layout.fillWidth: true
        Word { Layout.fillWidth: true; wrapMode: Text.Wrap; text: rate.text+" Hz / "+bits.currentText+" bit   "+codec.currentText+"   "+(recognition.checked?"识曲开启":"识曲关闭") }
        RoomButton { text: sheet.expanded ? "收起" : "调整"; quiet: true; onClicked: sheet.expanded = !sheet.expanded }
    }
    GridLayout {
        visible: sheet.expanded; Layout.fillWidth: true; columns: 2
        Word { text: "采集音质" }
        ComboBox {
            id: quality
            Layout.fillWidth: true
            model: ["标准 · 48 kHz", "CD · 44.1 kHz", "录音室 · 96 kHz", "自定义"]
            onActivated: index => {
                if (index < 3) { rate.text = ["48000","44100","96000"][index]; bits.currentIndex = index === 2 ? 1 : 0 }
            }
        }
        Word { text: "采样率 (Hz)" }
        TextField { id: rate; text: "48000"; Layout.fillWidth: true; onTextEdited: quality.currentIndex = 3; validator: IntValidator { bottom: 8000; top: 192000 } }
        Word { text: "位深 (bit)" }
        ComboBox { id: bits; model: ["16","24","32"]; onActivated: quality.currentIndex = 3; Layout.fillWidth: true }
        Word { text: "上传格式" }
        ComboBox { id: codec; model: (v.flac ? ["FLAC","PCM"] : ["PCM"]).concat(v.rtc ? ["Opus 64","Opus 128","Opus 256"] : []); Layout.fillWidth: true }
        Word { text: "房间预设" }
        ComboBox { id: style; model: ["流行摇滚","重型音乐","轻音乐"]; Layout.fillWidth: true }
        Word { text: "同步阈值 (秒)" }
        TextField { id: limit; placeholderText: "跟随房间"; Layout.fillWidth: true }
        CheckBox { id: recognition; enabled: v.recognize; text: "Shazam 识曲"; Layout.columnSpan: 2 }
    }
    Word { visible: text.length > 0; text: v.notice; color: "#f4ba92"; Layout.fillWidth: true; wrapMode: Text.Wrap }
    RoomButton {
        primary: true; Layout.fillWidth: true; text: v.host ? "发送接替申请" : "开始主持"
        enabled: !!sheet.selected && !v.busy && !v.pending && !v.mine
        onClicked: room.requestStage(sheet.selected, Number(rate.text), Number(bits.currentText), codec.currentText,
                                     recognition.checked, limit.text, ["pop","heavy","light"][style.currentIndex])
    }
}
