import QtQuick

Item {
    id: surface
    required property var shell
    Repeater {
        model: [Qt.LeftEdge, Qt.RightEdge, Qt.TopEdge, Qt.BottomEdge,
                Qt.LeftEdge|Qt.TopEdge, Qt.RightEdge|Qt.TopEdge,
                Qt.LeftEdge|Qt.BottomEdge, Qt.RightEdge|Qt.BottomEdge]
        MouseArea {
            required property int modelData
            readonly property bool horizontal: !!(modelData & (Qt.LeftEdge|Qt.RightEdge))
            readonly property bool vertical: !!(modelData & (Qt.TopEdge|Qt.BottomEdge))
            readonly property int grip: horizontal && vertical ? 9 : 5
            width: horizontal ? grip : surface.width
            height: vertical ? grip : surface.height
            x: modelData & Qt.RightEdge ? surface.width-width : 0
            y: modelData & Qt.BottomEdge ? surface.height-height : 0
            hoverEnabled: true
            cursorShape: !vertical ? Qt.SizeHorCursor : !horizontal ? Qt.SizeVerCursor
                         : (!!(modelData & Qt.LeftEdge) === !!(modelData & Qt.TopEdge)) ? Qt.SizeFDiagCursor : Qt.SizeBDiagCursor
            onPressed: surface.shell.startSystemResize(modelData)
        }
    }
}
