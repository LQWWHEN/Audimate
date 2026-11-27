import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Popup {
    id: sheet
    property string heading
    default property alias body: stack.data
    parent: Overlay.overlay
    width: Math.min(500, parent.width - 24)
    height: Math.min(parent.height - 36, stack.implicitHeight + 52)
    anchors.centerIn: parent
    modal: true
    focus: true
    padding: 24
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    background: Rectangle { radius: 20; color: "#24283b"; border.color: "#464a61" }
    Overlay.modal: Rectangle { color: "#a00c0e19" }
    contentItem: ScrollView {
        id: scroller
        clip: true
        contentWidth: availableWidth
        ColumnLayout {
            id: stack
            width: scroller.availableWidth
            spacing: 12
            RowLayout {
                Layout.fillWidth: true
                Word { text: sheet.heading; font.pixelSize: 23; font.weight: Font.Bold; Layout.fillWidth: true }
                RoomButton { text: "关闭"; quiet: true; onClicked: sheet.close() }
            }
        }
    }
}
