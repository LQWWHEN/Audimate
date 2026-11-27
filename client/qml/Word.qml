import QtQuick
import QtQuick.Controls

Text {
    color: "#f2f1f8"
    id: label
    font.family: uiFont.family
    font.pixelSize: 16
    renderType: Text.NativeRendering
    verticalAlignment: Text.AlignVCenter
    elide: Text.ElideRight
    HoverHandler { id: hover; enabled: label.truncated }
    ToolTip.visible: truncated && hover.hovered
    ToolTip.text: text
    ToolTip.delay: 350
}
