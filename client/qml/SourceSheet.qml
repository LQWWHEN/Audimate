import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Sheet {
    id: sheet
    required property var room
    readonly property var v: room.view
    property bool expanded: false
    readonly property var filtered: {
        const term = search.text.trim().toLowerCase()
        return room.sources.filter(s => (s.name + " " + s.title + " " + s.pid).toLowerCase().includes(term))
    }
    heading: v.host ? "选择音源并申请接替" : "选择要共享的音源"
    function share(source) {
        const p = parameters.values
        room.requestStage(source.key, p.rate, p.bits, p.codec, p.recognize, p.threshold)
    }
    onOpened: {
        expanded = false
        search.clear()
        parameters.load(room.localDefaults)
        room.listSources()
        search.forceActiveFocus()
    }
    Connections { target: sheet.room; function onSubmitted() { sheet.close() } }
    RowLayout {
        Layout.fillWidth: true
        TextField { id: search; objectName: "sourceSearch"; Layout.fillWidth: true; placeholderText: "搜索播放器、窗口或 PID"; Accessible.name: placeholderText }
        RoomButton { text: "刷新"; quiet: true; onClicked: sheet.room.listSources() }
    }
    ListView {
        id: list; Layout.fillWidth: true; Layout.preferredHeight: Math.max(64, Math.min(214, count*56))
        clip: true; spacing: 2; model: sheet.filtered
        ScrollBar.vertical: ScrollBar {}
        delegate: Item {
            required property var modelData
            width: ListView.view.width; height: 54
            RowLayout {
                anchors.fill: parent; anchors.rightMargin: 10; spacing: 12
                ColumnLayout {
                    Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 0
                    Word { Layout.fillWidth: true; font.weight: Font.DemiBold; text: modelData.title || modelData.name }
                    Word { Layout.fillWidth: true; color: "#aeb6cf"; font.pixelSize: 14
                        text: (modelData.title ? modelData.name + "   " : "") + "PID " + modelData.pid }
                }
                RoomButton { objectName: "share-" + modelData.key; text: sheet.v.host ? "申请接替" : "共享"; horizontalPadding: 12
                    Accessible.name: text + " " + (modelData.title || modelData.name)
                    enabled: !sheet.v.busy && !sheet.v.pending && !sheet.v.mine
                    onClicked: sheet.share(modelData) }
            }
        }
        Word { anchors.centerIn: parent; visible: !list.count; text: v.findingSources ? "正在查找音源…" : "没有匹配的音源，可刷新或更换搜索词"; color: "#b9bdd3" }
    }
    Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: "#41465c" }
    RowLayout {
        Layout.fillWidth: true
        Word { Layout.fillWidth: true; color: "#c1c7db"; font.pixelSize: 15
            text: parameters.values.codec + "   " + parameters.values.rate/1000 + " kHz / " + parameters.values.bits + " bit" }
        RoomButton { text: sheet.expanded ? "收起参数" : "本次参数"; quiet: true; onClicked: sheet.expanded = !sheet.expanded }
    }
    HostFields {
        id: parameters; visible: sheet.expanded; Layout.fillWidth: true
        codecs: (v.flac ? ["FLAC","PCM"] : ["PCM"]).concat(v.rtc ? ["Opus 64","Opus 128","Opus 256"] : [])
        canRecognize: v.recognize
    }
    Word { visible: !!text; text: v.notice; color: "#f4ba92"; Layout.fillWidth: true; wrapMode: Text.Wrap }
}
