import QtQuick
import QtQuick.Controls
import QtQuick.Templates as T

T.Button {
    id: button
    required property string symbol
    required property string description
    implicitWidth: 38
    implicitHeight: 36
    Accessible.name: description
    hoverEnabled: true
    ToolTip.visible: hovered
    ToolTip.delay: 500
    ToolTip.text: description
    background: Rectangle {
        radius: 6
        color: button.hovered ? (button.symbol === "close" ? "#993e52" : "#32374b") : "transparent"
        border.width: button.visualFocus ? 1 : 0
        border.color: "#a2ddfa"
    }
    contentItem: Item {
        Canvas {
            anchors.centerIn: parent
            width: 18; height: 18
            property string glyph: button.symbol
            onGlyphChanged: requestPaint()
            onPaint: {
                const c = getContext("2d")
                c.clearRect(0, 0, 18, 18)
                c.lineWidth = 1.4; c.strokeStyle = "#e0e1ee"; c.beginPath()
                switch (glyph) {
                case "close": c.moveTo(4,4); c.lineTo(14,14); c.moveTo(14,4); c.lineTo(4,14); break
                case "min": c.moveTo(3,11); c.lineTo(15,11); break
                case "restore": c.rect(3,6,9,9); c.moveTo(6,4); c.lineTo(6,3); c.lineTo(15,3); c.lineTo(15,12); c.lineTo(14,12); break
                case "portrait": c.rect(5,2,8,14); c.moveTo(7,12); c.lineTo(11,12); break
                case "landscape": c.rect(2,4,14,10); c.moveTo(3,7); c.lineTo(15,7); break
                default: c.rect(3,3,12,12)
                }
                c.stroke()
            }
        }
    }
}
