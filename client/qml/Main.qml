import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts
import Audimate 1.0

ApplicationWindow {
    id: win
    width: 1140; height: 760; minimumWidth: 360; minimumHeight: 490
    visible: true; title: "Audimate · 音乐房间"
    color: "#151827"
    font.family: "Microsoft YaHei UI"
    Material.theme: Material.Dark
    Material.accent: "#bccff3"
    property var v: room.view
    property bool wide: width >= 940
    property bool compact: width < 650
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

    header: ToolBar {
        height: win.compact ? 60 : 76
        background: Rectangle { color: "#181b2c"; Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: "#303447" } }
        RowLayout {
            anchors.fill: parent; anchors.leftMargin: win.compact ? 18 : 36; anchors.rightMargin: anchors.leftMargin; spacing: 11
            Item {
                implicitWidth: 27; implicitHeight: 30
                Repeater {
                    model: [12,24,16]
                    Rectangle { required property int index; required property int modelData
                        x: 2+index*8; anchors.verticalCenter: parent.verticalCenter; width: 4; height: modelData
                        rotation: 12; radius: 2; color: "#bcddf0"
                    }
                }
            }
            Word { text: "Audimate"; font.family: "Segoe UI Variable Display"; font.weight: Font.DemiBold; font.pixelSize: 26; font.letterSpacing: -1 }
            Rectangle { visible: !win.compact; Layout.leftMargin: 13; width: 1; height: 20; color: "#494b61" }
            Word { visible: !win.compact; text: v.demo ? "本地试听" : "音乐房间"; color: secondary }
            Item { Layout.fillWidth: true }
            Rectangle { visible: v.entered && !v.demo; width: 6; height: 6; radius: 3; color: "#a9cbd2" }
            Word { visible: v.entered && !v.demo; text: v.members+" 人在线"; color: secondary; font.pixelSize: 12 }
            RoomButton { visible: v.entered; text: v.demo ? "退出试听" : "离开房间"; quiet: true; enabled: !v.busy; onClicked: room.exitRoom() }
        }
    }

    Flickable {
        id: scroll
        anchors.fill: parent; clip: true; boundsBehavior: Flickable.StopAtBounds
        contentWidth: width
        contentHeight: Math.max(height, bottomContent + 20)
        property real bottomContent: !v.entered ? (win.compact ? login.y+login.height : Math.max(login.y+login.height,song.y+song.height))
                                               : win.wide ? Math.max(song.y+song.height, signalInfo.y+signalInfo.height,sessionInfo.y+sessionInfo.height)
                                               : signalInfo.y+signalInfo.height
        ScrollBar.vertical: ScrollBar {}
        Item {
            id: field
            width: scroll.width; height: scroll.contentHeight
            SpectrumDisc {
                id: disc; objectName: "record"
                width: v.entered ? Math.min(win.wide ? field.width*.49 : field.width*.9, Math.max(235,scroll.height*.73),520)
                                 : Math.min(win.compact ? field.width*.55 : field.width*.48,win.compact?236:485,scroll.height*.69)
                height: width
                x: (v.entered || win.compact ? field.width*.5 : field.width*.285) - width/2
                y: v.entered ? Math.max(0,(scroll.height-height-122)*(win.wide?.43:.13)) : win.compact ? -6 : Math.max(0,(scroll.height-height-72)*.42)
            }
            Column {
                id: song; spacing: 4
                width: v.entered ? (win.wide? field.width*.53:field.width-40) : (win.compact?field.width-40:field.width*.49)
                anchors.horizontalCenter: disc.horizontalCenter
                y: disc.y+disc.height-(win.compact?10:18)
                visible: v.entered || !win.compact
                Word {
                    width: parent.width; horizontalAlignment: Text.AlignHCenter
                    font.pixelSize: win.compact ? 30 : 43; font.weight: Font.DemiBold; font.letterSpacing: -1
                    text: v.entered ? v.title : "Audimate"
                    maximumLineCount: 2; wrapMode: Text.Wrap
                }
                Word { width: parent.width; horizontalAlignment: Text.AlignHCenter; font.pixelSize: 17; color: "#d1d0e1"
                    visible: text.length>0; text: v.entered ? v.artist : "" }
                Word { width: parent.width; horizontalAlignment: Text.AlignHCenter; font.pixelSize: 13; color: muted
                    visible: text.length>0; text: v.entered ? v.album : "" }
            }

            ColumnLayout {
                id: login
                visible: !v.entered; spacing: 10
                width: Math.min(win.compact?field.width-48:field.width*.34,360)
                x: win.compact ? (field.width-width)/2 : field.width*.73-width/2
                y: win.compact ? disc.y+disc.height-5 : Math.max(20,(scroll.height-height)/2)
                Word { text: "进入音乐房间"; font.pixelSize: 31; font.weight: Font.DemiBold; Layout.bottomMargin: 6 }
                TextField { id: account; Layout.fillWidth: true; placeholderText: "用户名"; Accessible.name: placeholderText; onAccepted: password.forceActiveFocus() }
                TextField { id: password; Layout.fillWidth: true; placeholderText: "密码"; Accessible.name: placeholderText; echoMode: TextInput.Password
                    onAccepted: if (account.text && password.text) room.login(address.text,account.text,password.text,false) }
                TextField { id: address; Layout.fillWidth: true; text: room.address; placeholderText: "服务器地址"; Accessible.name: placeholderText; visible: hostAddress.checked }
                CheckBox { id: hostAddress; text: "服务器地址"; font.pixelSize: 12 }
                RowLayout {
                    Layout.fillWidth: true
                    RoomButton { text: "登录"; primary: true; Layout.fillWidth: true; enabled: !!account.text && !!password.text && !v.busy
                        onClicked: room.login(address.text,account.text,password.text,false) }
                    RoomButton { text: "注册并进入"; enabled: !!account.text && !!password.text && !v.busy
                        onClicked: room.login(address.text,account.text,password.text,true) }
                }
                RoomButton { text: "试听可视化"; quiet: true; enabled: !v.busy; onClicked: room.auditionRoom(); Layout.alignment: Qt.AlignHCenter }
                Word { text: v.notice; visible: text.length>0; color: "#f4ba92"; wrapMode: Text.Wrap; Layout.fillWidth: true }
            }

            Column {
                id: sessionInfo; visible: v.entered; spacing: 8
                width: win.wide ? Math.min(202,field.width*.19) : Math.min(490,field.width-48)
                x: win.wide ? 38 : (field.width-width)/2
                y: win.wide ? disc.y+disc.height*.39 : song.y+song.height+24
                Word { text: v.demo ? "试听音源" : "房间主持"; color: muted; font.pixelSize: 12 }
                Word { width: parent.width; text: v.demo ? "本地合成器" : v.host || "席位空闲"; font.pixelSize: 20; font.weight: Font.DemiBold }
                RoomButton { text: (v.demo ? "" : v.mine ? "房间预设  " : v.following ? "跟随主持  " : "自选  ")+win.styleName+"  ﹀"
                    onClicked: presets.open(); anchors.left: parent.left }
            }
            Column {
                id: signalInfo; visible: v.entered; spacing: 7
                width: win.wide ? Math.min(213,field.width*.195) : sessionInfo.width
                x: win.wide ? field.width-width-30 : sessionInfo.x
                y: win.wide ? disc.y+disc.height*.30 : sessionInfo.y+sessionInfo.height+22
                Word { text: v.demo ? "播放音源" : "主持共享"; color: muted; font.pixelSize: 12 }
                Word { width: parent.width; text: v.shared; font.family: "Segoe UI"; font.pixelSize: 16; wrapMode: Text.Wrap }
                Item { width: 1; height: 6 }
                Word { text: v.mine ? "本机输入" : "本机收听"; color: muted; font.pixelSize: 12 }
                Word { width: parent.width; text: v.received; font.family: "Segoe UI"; font.pixelSize: 16; wrapMode: Text.Wrap }
                Column {
                    width: parent.width; visible: !!v.candidate; spacing: 4
                    Word { text: "耳朵一动"; color: "#d6c4ea"; font.pixelSize: 13 }
                    Word { width: parent.width; text: v.candidate; wrapMode: Text.Wrap }
                }
                Word { width: parent.width; text: v.recognitionNotice; visible: !!text; wrapMode: Text.Wrap; color: secondary }
                Word { width: parent.width; text: "播放延迟约 "+v.delay+" ms"; visible: v.delay>0; color: "#f4ba92" }
                RoomButton { visible: v.delay>0; text: "立即同步"; onClicked: room.sync() }
            }
        }
    }

    footer: Rectangle {
        visible: v.entered; implicitHeight: visible ? bar.implicitHeight+24 : 0
        color: "#1d2032"
        Rectangle { width: parent.width; height: 1; color: "#35394e" }
        ColumnLayout {
            id: bar; anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
            anchors.margins: win.compact?12:32; spacing: 5
            RowLayout {
                visible: v.requests.length>0; Layout.fillWidth: true
                property var applicant: v.requests[0] || ({})
                Word { Layout.fillWidth: true; text: (parent.applicant.name || "")+" 申请接替"+(v.requests.length>1?"（另有 "+(v.requests.length-1)+" 位）":"") }
                RoomButton { text:"接受"; enabled: !v.busy; onClicked: room.decide("accept",parent.applicant.id) }
                RoomButton { text:"拒绝"; enabled: !v.busy; onClicked: room.decide("reject",parent.applicant.id) }
            }
            Word { visible: !!v.notice && !sourceSheet.opened; text:v.notice; color:"#f4ba92"; Layout.fillWidth: true; wrapMode: Text.Wrap; maximumLineCount: 3 }
            RowLayout {
                Layout.fillWidth: true; spacing: win.compact?5:10
                ColumnLayout {
                    visible: !win.compact || v.mine; Layout.fillWidth: true; spacing: 2
                    Word { Layout.fillWidth: true; font.weight: Font.DemiBold; text: v.mine?"主持中":v.pending?"等待主持同意":!v.want?"已停止收听":v.playing?"正在收听":v.host||v.demo?"正在连接音源":"正在等候开播" }
                    Word { Layout.fillWidth: true; font.pixelSize: 12; color: muted; text: v.mine?v.process:v.user }
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
                RoomButton { visible: !v.mine; text: v.want?"停止收听":"开始收听"; enabled:v.ready; onClicked: room.listen() }
                RoomButton { text: v.mine?"主持设置":win.compact?"设置":"收听设置"; quiet:true; onClicked: settings.open() }
                RoomButton { visible: !v.demo && v.canHost; primary:true; enabled:v.ready && !v.busy
                    text:v.mine?"下台":v.pending?"取消申请":v.host?"申请接替":"开始主持"
                    onClicked: if(v.mine||v.pending) room.leaveStage(""); else sourceSheet.open() }
            }
        }
    }

    Sheet {
        id: presets; objectName: "presets"; heading: v.mine?"房间可视化预设":"可视化风格"
        RoomButton { visible: !v.mine && !v.demo; Layout.fillWidth: true; text: (v.following?"✓  ":"")+"跟随主持"; onClicked: {room.preset("");presets.close()} }
        Repeater {
            model: win.styleNames
            RoomButton {
                required property int index; required property string modelData
                Layout.fillWidth: true
                text: ((v.mine||v.demo||!v.following)&&v.style===win.styles[index]?"✓  ":"")+modelData
                enabled: !v.busy
                onClicked: { room.preset(win.styles[index]); presets.close() }
            }
        }
        CheckBox { text:"减弱动态效果"; checked:win.reduced; onToggled: win.reduced=checked }
    }
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
