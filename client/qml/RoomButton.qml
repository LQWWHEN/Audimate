import QtQuick
import QtQuick.Templates as T

T.Button {
    id: button
    property bool primary: false
    property bool quiet: false
    padding: 12
    horizontalPadding: 16
    implicitHeight: 40
    implicitWidth: Math.max(58, label.implicitWidth + leftPadding + rightPadding + 2)
    hoverEnabled: true
    font.family: "Microsoft YaHei UI"
    font.pixelSize: 13
    background: Rectangle {
        radius: 10
        color: button.primary ? (button.hovered ? "#e7d9ff" : "#d8cef4")
             : button.down ? "#43475d" : button.hovered ? "#34394e" : button.quiet ? "transparent" : "#282d42"
        border.width: button.visualFocus ? 2 : button.quiet || button.primary ? 0 : 1
        border.color: button.visualFocus ? "#a2ddfa" : "#42475d"
        opacity: button.enabled ? 1 : .5
    }
    contentItem: Word {
        id: label
        text: button.text
        elide: Text.ElideNone
        horizontalAlignment: Text.AlignHCenter
        font.weight: button.primary ? Font.DemiBold : Font.Normal
        color: !button.enabled ? "#9293a3" : button.primary ? "#242136" : "#e5e5f1"
    }
}
