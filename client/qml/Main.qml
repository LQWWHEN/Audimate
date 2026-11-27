import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts
import Audimate 1.0

ApplicationWindow {
    id: win
    width: 1140; height: 760
    flags: Qt.Window | Qt.FramelessWindowHint | Qt.WindowSystemMenuHint | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint
    property bool portrait: false
    property string dockEdge: ""
    property real fold: 0
    readonly property bool popupOpen: settings.visible || sourceSheet.visible || presets.visible
    minimumWidth: portrait ? 420 : 960
    minimumHeight: Math.ceil(Math.max(portrait ? 600 : 560, titleBar.height + footerBar.height
        + (v.entered ? (portrait ? infoHeight + song.height + 128 : Math.max(infoHeight + 44, song.height + 180))
                     : login.height + (portrait ? 135 : 32))))
    readonly property real infoHeight: Math.max(sessionInfo.implicitHeight, signalInfo.implicitHeight)
    visible: true; title: "Audimate · 音乐房间"
    color: "#151827"
    font: uiFont
    Material.theme: Material.Dark
    Material.accent: "#bccff3"
    property var v: room.view
    property color secondary: "#b6b9ce"
    property color muted: "#a0a6c0"
    property var styles: ["pop","heavy","light"]
    property var styleNames: ["流行摇滚","重型音乐","轻音乐"]
    property string styleName: styleNames[Math.max(0,styles.indexOf(v.style))]
    property bool reduced: (Qt.styleHints.accessibility?.motionPreference ?? 0) === 1
    onReducedChanged: if (disc) disc.reduceMotion(reduced)
    Component.onCompleted: disc.reduceMotion(reduced)
    Connections {
        target: room
        function onChanged() {
            if (!v.entered) { settings.close(); sourceSheet.close(); presets.close() }
            else password.clear()
        }
    }

    Item {
        id: playerSurface
        anchors.fill: parent
        transform: Translate {
            x: (win.dockEdge === "left" ? -1 : win.dockEdge === "right" ? 1 : 0) * (win.width-6) * win.fold
            y: win.dockEdge === "top" ? -(win.height-6)*win.fold : 0
        }
        TitleBar { id: titleBar; width: parent.width; shell: win; model: room }
        Item {
            id: field
            anchors { top: titleBar.bottom; bottom: footerBar.top; left: parent.left; right: parent.right }
            readonly property real infoTop: height - win.infoHeight - 12
            readonly property real centerHeight: win.portrait ? infoTop - 8 : height
            SpectrumDisc {
                id: disc; objectName: "record"
                width: Math.max(70, v.entered
                    ? Math.min(field.width*(win.portrait ? .83 : .48), field.centerHeight-song.height-8, 540)
                    : Math.min(field.width*(win.portrait ? .65 : .48), win.portrait ? field.height-login.height-20 : field.height-90, 480))
                height: width
                x: (v.entered || win.portrait ? field.width*.5 : field.width*.285) - width/2
                y: v.entered ? Math.max(0,(field.centerHeight-height-song.height)/2)
                             : win.portrait ? Math.max(0,(field.height-height-login.height)/2) : (field.height-height-45)*.46
            }
            Column {
                id: song; spacing: 4
                width: v.entered ? (!win.portrait? field.width*.53:field.width-40) : (win.portrait?field.width-40:field.width*.49)
                anchors.horizontalCenter: disc.horizontalCenter
                y: disc.y+disc.height-8
                visible: v.entered || !win.portrait
                Word {
                    width: parent.width; horizontalAlignment: Text.AlignHCenter
                    font.pixelSize: win.portrait ? 32 : 45; font.weight: Font.DemiBold; font.letterSpacing: -1
                    text: v.entered ? v.title : "Audimate"
                    maximumLineCount: 2; wrapMode: Text.Wrap
                }
                Word { width: parent.width; horizontalAlignment: Text.AlignHCenter; font.pixelSize: 19; color: "#d1d0e1"
                    visible: text.length>0; text: v.entered ? v.artist : "" }
                Word { width: parent.width; horizontalAlignment: Text.AlignHCenter; font.pixelSize: 15; color: muted
                    visible: text.length>0; text: v.entered ? v.album : "" }
            }

            ColumnLayout {
                id: login
                visible: !v.entered; spacing: 10
                width: Math.min(win.portrait?field.width-48:field.width*.34,360)
                x: win.portrait ? (field.width-width)/2 : field.width*.73-width/2
                y: win.portrait ? disc.y+disc.height-5 : Math.max(20,(field.height-height)/2)
                Word { text: "进入音乐房间"; font.pixelSize: 33; font.weight: Font.DemiBold; Layout.bottomMargin: 6 }
                TextField { id: account; Layout.fillWidth: true; placeholderText: "用户名"; Accessible.name: placeholderText; onAccepted: password.forceActiveFocus() }
                TextField { id: password; Layout.fillWidth: true; placeholderText: "密码"; Accessible.name: placeholderText; echoMode: TextInput.Password
                    onAccepted: if (account.text && password.text) room.login(address.text,account.text,password.text,false) }
                TextField { id: address; Layout.fillWidth: true; text: room.address; placeholderText: "服务器地址"; Accessible.name: placeholderText; visible: hostAddress.checked }
                CheckBox { id: hostAddress; text: "服务器地址"; font.pixelSize: 14 }
                RowLayout {
                    Layout.fillWidth: true
                    RoomButton { text: "登录"; primary: true; Layout.fillWidth: true; enabled: !!account.text && !!password.text && !v.busy
                        onClicked: room.login(address.text,account.text,password.text,false) }
                    RoomButton { text: "注册并进入"; enabled: !!account.text && !!password.text && !v.busy
                        onClicked: room.login(address.text,account.text,password.text,true) }
                }
                RoomButton { text: "试听可视化"; quiet: true; enabled: !v.busy; onClicked: room.auditionRoom(); Layout.alignment: Qt.AlignHCenter }
                Word { text: v.notice; maximumLineCount: 2; visible: text.length>0; color: "#f4ba92"; wrapMode: Text.Wrap; Layout.fillWidth: true }
            }

            Column {
                id: sessionInfo; visible: v.entered; spacing: 6
                width: win.portrait ? field.width*.46-18 : field.width*.22-22
                x: win.portrait ? 16 : 24
                y: win.portrait ? field.infoTop : (field.height-height)/2
                Word { text: v.demo ? "试听音源" : "房间主持"; color: muted; font.pixelSize: 14 }
                Word { width: parent.width; text: v.demo ? "本地合成器" : v.host || "席位空闲"; font.pixelSize: 22; font.weight: Font.DemiBold }
                RoomButton {
                    id: presetButton; objectName: "presetButton"
                    width: Math.min(implicitWidth, parent.width); horizontalPadding: 6
                    text: (v.demo ? "" : v.mine ? "房间 · " : v.following ? "跟随 · " : "自选 · ")+win.styleName+" ▾"
                    Accessible.name: "可视化预设，"+text
                    onClicked: presets.visible ? presets.close() : presets.open()
                    Menu {
                        id: presets; objectName: "presets"; parent: presetButton
                        popupType: Popup.Item
                        y: presetButton.height+4
                        width: Math.min(172, presetButton.parent.width); modal: false; dim: false
                        font: uiFont
                        background: Rectangle { radius: 8; color: "#292e42"; border.color: "#535970" }
                        MenuItem { visible: !v.mine && !v.demo; height: visible ? 36 : 0
                            text: "跟随主持"; checkable: true; checked: v.following; onTriggered: room.preset("") }
                        Repeater {
                            model: win.styleNames
                            MenuItem {
                                required property int index; required property string modelData
                                objectName: "preset-" + win.styles[index]
                                text: modelData; checkable: true; height: 36
                                checked: (v.mine || v.demo || !v.following) && v.style === win.styles[index]
                                enabled: !v.busy; onTriggered: room.preset(win.styles[index])
                            }
                        }
                    }
                }
                Item { visible: win.portrait && presets.visible; height: presets.implicitHeight; width: 1 }
            }
            Column {
                id: signalInfo; visible: v.entered; spacing: 7
                width: !win.portrait ? field.width*.23-22 : field.width*.54-22
                x: !win.portrait ? field.width-width-20 : field.width-width-16
                y: !win.portrait ? (field.height-height)/2 : field.infoTop
                Word { text: v.demo ? "播放音源" : "主持共享"; color: muted; font.pixelSize: 14 }
                Word { width: parent.width; text: v.shared; font.pixelSize: 18; maximumLineCount: 2; wrapMode: Text.Wrap }
                Item { width: 1; height: 6 }
                Word { text: v.mine ? "本机输入" : "本机收听"; color: muted; font.pixelSize: 14 }
                Word { width: parent.width; text: v.received; font.pixelSize: 18; maximumLineCount: 2; wrapMode: Text.Wrap }
                Column {
                    parent: win.portrait ? sessionInfo : signalInfo
                    width: parent.width; spacing: 4
                    visible: !!v.candidate || !!v.recognitionNotice
                    Word { visible: !!v.candidate; text: "耳朵一动"; color: "#d6c4ea"; font.pixelSize: 15 }
                    Word { width: parent.width; visible: !!text; text: v.candidate; maximumLineCount: 2; wrapMode: Text.Wrap }
                    Word { width: parent.width; visible: !!text; text: v.recognitionNotice; maximumLineCount: win.portrait && v.candidate ? 1 : 2; wrapMode: Text.Wrap; color: secondary }
                }
                Word { width: parent.width; text: "播放延迟约 "+v.delay+" ms"; visible: v.delay>0; color: "#f4ba92" }
                RoomButton { visible: v.delay>0; text: "立即同步"; onClicked: room.sync() }
            }
        }

        Rectangle {
            id: footerBar; anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
            height: implicitHeight
            visible: v.entered; implicitHeight: visible ? bar.implicitHeight+24 : 0
            color: "#1d2032"
            Rectangle { width: parent.width; height: 1; color: "#35394e" }
            ColumnLayout {
                id: bar; anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                anchors.margins: win.portrait?12:32; spacing: 5
                RowLayout {
                    visible: v.requests.length>0; Layout.fillWidth: true
                    property var applicant: v.requests[0] || ({})
                    Word { Layout.fillWidth: true; text: (parent.applicant.name || "")+" 申请接替"+(v.requests.length>1?"（另有 "+(v.requests.length-1)+" 位）":"") }
                    RoomButton { text:"接受"; enabled: !v.busy; onClicked: room.decide("accept",parent.applicant.id) }
                    RoomButton { text:"拒绝"; enabled: !v.busy; onClicked: room.decide("reject",parent.applicant.id) }
                }
                Word { visible: !!v.notice && !sourceSheet.opened; text:v.notice; color:"#f4ba92"; Layout.fillWidth: true; wrapMode: Text.Wrap; maximumLineCount: 2 }
                GridLayout {
                    Layout.fillWidth: true; columns: 2
                    rowSpacing: 3; columnSpacing: 10
                    ColumnLayout {
                        Layout.fillWidth: true; spacing: 2
                        Word { Layout.fillWidth: true; font.weight: Font.DemiBold; text: v.mine?"主持中":v.pending?"等待主持同意":!v.want?"已停止收听":v.playing?"正在收听":v.host||v.demo?"正在连接音源":"正在等候开播" }
                        Word { visible: !win.portrait || v.mine; Layout.fillWidth: true; font.pixelSize: 14; color: muted; text: v.mine?v.process:v.user }
                        Row {
                            visible: v.mine; spacing: 3
                            Repeater {
                                model: [v.left,v.right]
                                Rectangle { required property real modelData; width: 40; height: 4; color:"#404660"
                                    Rectangle { width: parent.width*Math.min(1,Math.sqrt(modelData)); height: 4; color:"#a4d4ed" }
                                }
                            }
                        }
                    }
                    RowLayout {
                        Layout.alignment: Qt.AlignRight
                        spacing: win.portrait ? 6 : 10
                        RoomButton { visible: !v.mine; text: v.want?"停止收听":"开始收听"; enabled:v.ready; onClicked: room.listen() }
                        RoomButton { text: v.mine?"主持设置":win.portrait?"设置":"收听设置"; quiet:true; onClicked: settings.open() }
                        RoomButton { visible: !v.demo && v.canHost; primary:true; enabled:v.ready && !v.busy
                            text:v.mine?"下台":v.pending?"取消申请":v.host?"申请接替":"开始主持"
                            onClicked: if(v.mine||v.pending) room.leaveStage(""); else sourceSheet.open() }
                    }
                }
            }
        }

    }
    Rectangle {
        visible: win.fold > .97
        width: win.dockEdge === "top" ? 62 : 4
        height: win.dockEdge === "top" ? 4 : 62
        radius: 2; color: "#abd9f1"
        x: win.dockEdge === "top" ? (win.width-width)/2 : win.dockEdge === "left" ? 1 : win.width-width-1
        y: win.dockEdge === "top" ? 1 : (win.height-height)/2
    }
    ResizeEdges { anchors.fill: parent; shell: win; visible: win.visibility === Window.Windowed && win.fold === 0 }
    Sheet {
        id: settings; heading: v.mine?"主持设置":"收听设置"
        onOpened: {
            receive.currentIndex = v.receive==="Opus"?0:1; autoSync.checked=v.auto
            interval.currentIndex=Math.max(0,v.intervals.indexOf(v.interval))
            threshold.text=v.threshold===null||v.threshold===undefined?"":String(v.threshold/1000)
        }
        Word { visible: !v.demo&&!v.mine; text:"优先接收" }
        ComboBox { id:receive; visible: !v.demo&&!v.mine; Layout.fillWidth:true; model:["Opus","无损 PCM"] }
        CheckBox { id:autoSync; visible:!v.demo&&!v.mine; text:"自动同步" }
        Word { visible:!v.demo&&!v.mine; text:"校准间隔（秒）" }
        ComboBox { id:interval; visible:!v.demo&&!v.mine; Layout.fillWidth:true; model:v.intervals }
        RoomButton { visible:!v.demo&&!v.mine; text:"立即同步"; enabled:v.playing; onClicked: {room.sync(); settings.close()} }
        TextField { id:threshold; visible:v.mine; Layout.fillWidth:true; placeholderText:"同步阈值（秒），留空跟随房间" }
        CheckBox { text:"减弱动态效果"; checked:win.reduced; onToggled:win.reduced=checked }
        RoomButton { visible: !v.demo; primary:true; text:"应用"; enabled:!v.busy
            onClicked: { if(v.mine) room.threshold(threshold.text); else room.preferences(receive.currentText,autoSync.checked,Number(interval.currentText)); settings.close() } }
    }
    SourceSheet { id:sourceSheet; objectName: "sourceSheet"; room: win.roomModel }
    property var roomModel: room
}
