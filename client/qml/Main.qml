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
    property bool editingDefaults: false
    readonly property bool popupOpen: editingDefaults || roundSettings.visible || sourceSheet.visible || closeSheet.visible
    onClosing: function(event) {
        if (!windowsTray || windowsTray.exiting()) return
        event.accepted = false
        if (closeSheet.visible) return
        const action = room.localDefaults.closeAction
        if (action === "exit") {
            Qt.callLater(function() { windowsTray.quitApplication() })
        } else if (action !== "tray" || !windowsTray.hideWindow()) {
            windowsTray.restore()
            closeSheet.trayAvailable = windowsTray.available()
            rememberClose.checked = false
            closeSheet.error = ""
            closeSheet.open()
        }
    }
    minimumWidth: portrait ? 420 : 960
    minimumHeight: editingDefaults ? (portrait ? 600 : 560) : Math.ceil(Math.max(portrait ? 600 : 560, titleBar.height + footerBar.height
        + (v.entered ? (portrait ? infoHeight + song.height + 128 : Math.max(infoHeight + 44, song.height + 180))
                     : login.height + (portrait ? 135 : 32))))
    readonly property real infoHeight: Math.max(sessionInfo.implicitHeight, signalInfo.implicitHeight)
    visible: true; title: "Audimate · 音乐房间"
    color: "#151827"
    font: uiFont
    Material.theme: Material.Dark
    Material.accent: "#bccff3"
    property var v: room.view
    readonly property bool hasBackdrop: coverField.status === Image.Ready && coverField.source.toString() !== ""
    property color secondary: hasBackdrop ? "#e0e2ee" : "#b6b9ce"
    property color muted: hasBackdrop ? "#d4d7e5" : "#a0a6c0"
    readonly property int railLabelSize: portrait ? 16 : 17
    readonly property int railValueSize: portrait ? 20 : 22
    readonly property int valueGap: portrait ? 5 : 8
    component SongLine: Word {
        width: parent.width
        horizontalAlignment: Text.AlignHCenter
        textFormat: Text.PlainText
        fontSizeMode: Text.Fit
    }
    component RailReading: Column {
        required property string caption
        required property string value
        property bool inwardRight: false
        property bool prominent: false
        spacing: win.valueGap
        Word { width: parent.width; horizontalAlignment: parent.inwardRight ? Text.AlignRight : Text.AlignLeft
            text: parent.caption; color: win.secondary; font.pixelSize: win.railLabelSize }
        Word { width: parent.width; horizontalAlignment: parent.inwardRight ? Text.AlignRight : Text.AlignLeft
            text: parent.value; font.pixelSize: parent.prominent ? win.railValueSize+4 : win.railValueSize
            font.weight: parent.prominent ? Font.DemiBold : Font.Medium
            maximumLineCount: 2; wrapMode: Text.Wrap }
    }
    readonly property string hostStyleName: ({pop:"流行摇滚", heavy:"重型音乐", light:"轻音乐", custom:"自定义"})[v.hostStyle] || "流行摇滚"
    readonly property bool reduced: room.localDefaults.motion === "reduced" ||
        (room.localDefaults.motion === "system" && (Qt.styleHints.accessibility?.motionPreference ?? 0) === 1)
    function toggleDefaults() {
        if (editingDefaults) localPage.cancel()
        else { localPage.load(); editingDefaults = true; localPage.forceActiveFocus() }
    }
    readonly property bool inRoom: !!v.entered
    onInRoomChanged: if (editingDefaults) localPage.cancel()
    onReducedChanged: if (disc) disc.reduceMotion(reduced)
    Component.onCompleted: disc.reduceMotion(reduced)
    Connections {
        target: room
        function onChanged() {
            if (!v.entered) { roundSettings.close(); sourceSheet.close() }
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
        Image {
            id: coverField; objectName: "coverBackdrop"
            anchors.fill: parent
            source: v.entered && !v.demo && !win.editingDefaults && room.localDefaults.coverBackground ? room.coverBackdrop : ""
            fillMode: Image.PreserveAspectCrop
            smooth: true; cache: false
        }
        TitleBar { id: titleBar; width: parent.width; shell: win; model: room }
        Rectangle {
            id: field
            color: win.hasBackdrop ? Qt.rgba(.08, .09, .15, .70) : "transparent"
            visible: !win.editingDefaults
            anchors { top: titleBar.bottom; bottom: footerBar.top; left: parent.left; right: parent.right }
            readonly property real infoTop: height - win.infoHeight - 12
            readonly property real centerHeight: win.portrait ? infoTop - 8 : height
            readonly property real discColumnWidth: Math.min(width * .48, 540)
            readonly property real leftRailEnd: win.portrait ? width / 2 - 10 : (width-discColumnWidth)/2 + discColumnWidth*.045 - 10
            readonly property real railWidth: Math.min(270, leftRailEnd - (win.portrait ? 14 : 24))
            readonly property real groupGap: win.portrait ? 18 : Math.min(44, Math.round(width * .034))
            readonly property real upperRowHeight: Math.max(hostReading.implicitHeight, sharedReading.implicitHeight)
            readonly property real railTop: win.portrait ? infoTop : Math.max(12, Math.min(height - win.infoHeight - 12,
                disc.y + disc.height / 2 - upperRowHeight - groupGap / 2))
            function formatQuality(value) {
                // Keep the unit attached to its number when a narrow rail wraps.
                return value.replace(/ {2,}/g, win.portrait ? " " : "\u2002")
                            .replace(/(\d) (kHz|bit|kbps)\b/g, "$1\u202f$2")
                            .replace(/ \/ /g, win.portrait ? "\u2009/\u2009" : " / ")
            }
            SpectrumDisc {
                id: disc; objectName: "record"
                parent: localPage.showDisc ? localPage.preview : field
                width: localPage.showDisc ? Math.min(parent.width,parent.height) : Math.max(70, v.entered
                    ? Math.min(field.width*(win.portrait ? .83 : .48), field.centerHeight-song.height-8, 540)
                    : Math.min(field.width*(win.portrait ? .65 : .48), win.portrait ? field.height-login.height-20 : field.height-90, 480))
                height: width
                x: localPage.showDisc ? (parent.width-width)/2 : (v.entered || win.portrait ? field.width*.5 : field.width*.285) - width/2
                y: localPage.showDisc ? (parent.height-height)/2 : v.entered ? Math.max(0,(field.centerHeight-height-song.height)/2)
                             : win.portrait ? Math.max(0,(field.height-height-login.height)/2) : (field.height-height-45)*.46
            }
            Word {
                visible: !v.entered && !win.portrait
                x: field.width*.285 - width/2
                y: disc.y + disc.height - 8
                font.pixelSize: 45; font.weight: Font.DemiBold; font.letterSpacing: -1
                text: "Audimate"
            }
            Column {
                id: song; objectName: "songInfo"
                visible: v.entered
                width: win.portrait ? field.width-40 : field.width-2*field.leftRailEnd-24
                anchors.horizontalCenter: parent.horizontalCenter
                y: disc.y + disc.height - 8 - Math.min(win.portrait ? 9 : 18, disc.height * .04)
                spacing: 2
                SongLine {
                    id: trackTitle; objectName: "songTitle"
                    height: win.portrait ? 60 : 80
                    font.pixelSize: titleMeasure.fontInfo.pixelSize; minimumPixelSize: titleMeasure.minimumPixelSize
                    font.weight: titleMeasure.font.weight; font.letterSpacing: titleMeasure.font.letterSpacing
                    verticalAlignment: Text.AlignBottom
                    maximumLineCount: 2; wrapMode: Text.Wrap
                    text: v.title
                    Text {
                        id: titleMeasure
                        visible: false
                        // Measure two full lines without elision. The visible title
                        // then fits natural line spacing inside the reserved height.
                        width: trackTitle.width; height: 2 * lineHeight
                        lineHeightMode: Text.FixedHeight; lineHeight: trackTitle.height
                        font.family: uiFont.family; font.weight: Font.DemiBold; font.letterSpacing: -0.5
                        font.pixelSize: win.portrait ? 28 : 38; minimumPixelSize: 12
                        text: trackTitle.text; textFormat: Text.PlainText; renderType: Text.NativeRendering
                        fontSizeMode: Text.Fit; wrapMode: Text.Wrap; maximumLineCount: 3
                    }
                }
                SongLine {
                    objectName: "songArtist"
                    height: win.portrait ? 26 : 28
                    font.pixelSize: win.portrait ? 17 : 19; minimumPixelSize: 14
                    maximumLineCount: 1
                    color: "#d1d0e1"; text: v.artist
                }
                SongLine {
                    objectName: "songAlbum"
                    height: 22
                    font.pixelSize: 15; minimumPixelSize: 13
                    maximumLineCount: 1
                    color: win.muted; text: v.album
                }
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
                id: sessionInfo; objectName: "hostRail"; visible: v.entered; spacing: field.groupGap
                width: field.railWidth; x: field.leftRailEnd-width; y: field.railTop
                RailReading {
                    id: hostReading; width: parent.width; height: field.upperRowHeight
                    caption: v.demo ? "试听音源" : "房间主持"; value: v.demo ? "本地合成器" : v.host || "席位空闲"
                    inwardRight: true; prominent: true
                }
                Column {
                    width: parent.width; spacing: win.valueGap
                    Word { width: parent.width; horizontalAlignment: Text.AlignRight; text: "主持风格"; color: win.secondary; font.pixelSize: win.railLabelSize }
                    Word { width: parent.width; horizontalAlignment: Text.AlignRight
                        text: v.host ? win.hostStyleName : "—"; font.pixelSize: win.railValueSize }
                    RoomButton {
                        objectName: "adoptSpectrum"; anchors.right: parent.right
                        text: "采用主持风格"; quiet: true
                        visible: !v.mine && !v.demo; enabled: !!v.host
                        implicitHeight: 32; onClicked: room.adoptSpectrum()
                    }
                    Item {
                        id: recognitionSlot
                        visible: v.recognitionEnabled || !!v.candidate || !!v.recognitionNotice
                        readonly property int lineSpace: win.portrait ? 25 : 32
                        readonly property real bodyTop: win.portrait ? 0 : earLabel.height + win.valueGap
                        width: parent.width
                        height: bodyTop + 2 * lineSpace
                        Word {
                            id: earLabel
                            width: parent.width; horizontalAlignment: Text.AlignRight
                            visible: !win.portrait && !!v.candidate; text: "耳朵一动"; color: "#d6c4ea"; font.pixelSize: win.railLabelSize
                        }
                        Word {
                            width: parent.width; horizontalAlignment: Text.AlignRight
                            y: recognitionSlot.bodyTop
                            height: (v.recognitionNotice ? 1 : 2) * recognitionSlot.lineSpace
                            verticalAlignment: Text.AlignTop; font.pixelSize: win.portrait ? 17 : win.railValueSize-1
                            maximumLineCount: v.recognitionNotice ? 1 : 2; wrapMode: Text.Wrap
                            textFormat: Text.PlainText
                            text: win.portrait && v.candidate ? "耳朵一动 · " + v.candidate : v.candidate
                        }
                        Word {
                            width: parent.width; horizontalAlignment: Text.AlignRight
                            y: v.candidate ? parent.height-recognitionSlot.lineSpace : 0
                            height: parent.height-y; verticalAlignment: Text.AlignTop
                            font.pixelSize: win.railLabelSize; color: secondary
                            maximumLineCount: v.candidate ? 1 : 2; wrapMode: Text.Wrap
                            textFormat: Text.PlainText; text: v.recognitionNotice
                        }
                    }
                }
            }
            Column {
                id: signalInfo; objectName: "audioRail"; visible: v.entered; spacing: field.groupGap
                width: field.railWidth; x: field.width-field.leftRailEnd; y: field.railTop
                RailReading {
                    id: sharedReading; width: parent.width; height: field.upperRowHeight
                    caption: v.demo ? "播放音源" : "主持共享"; value: field.formatQuality(v.shared)
                }
                Column {
                    width: parent.width; spacing: win.valueGap
                    RailReading { width: parent.width; caption: v.mine ? "本机输入" : "本机收听"; value: field.formatQuality(v.received) }
                    Word { width: parent.width; text: "播放延迟约 "+v.delay+" ms"; visible: v.delay>0; color: "#f4ba92"; font.pixelSize: win.railLabelSize }
                    RoomButton { visible: !v.mine && !v.demo && (v.playing || v.delay>0)
                        text: "立即同步"; quiet: true; horizontalPadding: 3; implicitHeight: 30; onClicked: room.sync() }
                }
            }
        }

        Rectangle {
            id: footerBar; anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
            height: implicitHeight
            visible: v.entered && !win.editingDefaults; implicitHeight: visible ? bar.implicitHeight+18 : 0
            color: win.hasBackdrop ? Qt.rgba(.11, .13, .20, .80) : "#1d2032"
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
                        RoomButton { visible: v.mine; text: "本轮同步"; quiet: true; onClicked: roundSettings.open() }
                        RoomButton { visible: !v.demo && v.canHost; primary:true; enabled:v.ready && !v.busy
                            text:v.mine?"下台":v.pending?"取消申请":v.host?"申请接替":"开始主持"
                            onClicked: if(v.mine||v.pending) room.leaveStage(""); else sourceSheet.open() }
                    }
                }
            }
        }

        LocalSettings {
            id: localPage; objectName: "localSettings"; visible: win.editingDefaults
            anchors { top: titleBar.bottom; bottom: parent.bottom; left: parent.left; right: parent.right }
            roomModel: win.roomModel; portrait: win.portrait
            onFinished: { win.editingDefaults = false; titleBar.focusSettings() }
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
        id: roundSettings; heading: "本轮同步阈值"
        onOpened: threshold.text=v.threshold==null?"":String(v.threshold/1000)
        TextField { id:threshold; Layout.fillWidth:true; placeholderText:"秒，留空跟随房间" }
        RoomButton { primary:true; text:"应用到本轮"; enabled:v.mine && !v.busy
            onClicked: { room.threshold(threshold.text); roundSettings.close() } }
    }
    SourceSheet { id:sourceSheet; objectName: "sourceSheet"; room: win.roomModel }
    Sheet {
        id: closeSheet; objectName: "closeSheet"
        heading: "关闭 Audimate"; dismissText: "取消"
        // A hidden window cannot advance a pending popup exit animation.
        exit: Transition {}
        property bool trayAvailable: false
        property string error: ""
        function choose(action) {
            if (action === "tray" && !windowsTray.available()) {
                trayAvailable = false
                return
            }
            if (rememberClose.checked) {
                error = room.saveDefaults({closeAction: action})
                if (error) return
                if (win.editingDefaults) localPage.loadCloseAction(action)
            }
            if (action === "exit") {
                windowsTray.quitApplication()
            } else if (windowsTray.hideWindow()) {
                close()
            } else {
                trayAvailable = false
            }
        }
        Word { Layout.fillWidth: true; wrapMode: Text.Wrap
            text: closeSheet.trayAvailable ? "收起到系统托盘后，主持和收听会继续。点击托盘图标可恢复窗口。" : "系统托盘暂不可用，可取消或退出应用。" }
        CheckBox { id: rememberClose; text: "记住我的选择"; objectName: "rememberClose" }
        Word { visible: !!text; text: closeSheet.error; Layout.fillWidth: true; wrapMode: Text.Wrap; color: "#f4ba92" }
        RowLayout {
            Layout.alignment: Qt.AlignRight
            RoomButton { text: "退出应用"; onClicked: closeSheet.choose("exit") }
            RoomButton { text: "最小化到托盘"; primary: true; enabled: closeSheet.trayAvailable; onClicked: closeSheet.choose("tray") }
        }
    }
    property var roomModel: room
}
