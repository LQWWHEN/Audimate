import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: editor
    required property var roomModel
    property var draft: roomModel.spectrumPreset("pop")
    signal edited()
    spacing: 6

    function update(key, value) {
        if (JSON.stringify(draft[key]) === JSON.stringify(value)) return
        const next = Object.assign({}, draft, {custom: true})
        next[key] = value
        draft = next
        edited()
    }
    component Knob: RowLayout {
        id: row
        required property string caption
        required property string suffix
        required property real setting
        required property real low
        required property real high
        required property real step
        signal adjusted(real setting)
        Layout.fillWidth: true
        Word { text: row.caption; Layout.preferredWidth: 95 }
        Slider {
            Layout.fillWidth: true; from: row.low; to: row.high
            stepSize: row.step; value: row.setting; snapMode: Slider.SnapAlways
            Accessible.name: row.caption
            onMoved: row.adjusted(Math.round(value * 100) / 100)
        }
        Word { Layout.preferredWidth: 78; horizontalAlignment: Text.AlignRight
            text: Number(row.setting).toFixed(row.step < 1 ? 2 : 0) + row.suffix }
    }
    RowLayout {
        Layout.fillWidth: true
        Word { text: "频谱预设"; Layout.fillWidth: true }
        ComboBox {
            objectName: "spectrumPreset"
            property var ids: ["pop","heavy","light"]
            model: ["流行摇滚","重型音乐","轻音乐"]
            currentIndex: editor.draft.custom ? -1 : ids.indexOf(editor.draft.base)
            displayText: editor.draft.custom ? "自定义" : currentText
            Accessible.name: "频谱预设"
            onActivated: index => {
                editor.draft = editor.roomModel.spectrumPreset(ids[index])
                editor.edited()
            }
        }
    }
    RowLayout {
        Word { text: "频谱段数"; Layout.fillWidth: true }
        ComboBox { model: [48,64,96]; currentIndex: model.indexOf(editor.draft.count)
            Accessible.name: "频谱段数"; onActivated: index => editor.update("count", model[index]) }
    }
    Knob { caption: "起始频率"; suffix: " Hz"; low: 10; high: 60; step: 1
        setting: editor.draft.start; onAdjusted: v => editor.update("start", v) }
    Knob { caption: "抑制阈值"; suffix: " dBFS"; low: -72; high: -24; step: 1
        setting: editor.draft.gate; onAdjusted: v => editor.update("gate", v) }
    Knob { caption: "峰谷对比度"; suffix: ""; low: 0.5; high: 3; step: 0.05
        setting: editor.draft.contrast; onAdjusted: v => editor.update("contrast", v) }
    Knob { caption: "瞬态增益"; suffix: " ×"; low: 0; high: 2; step: 0.05
        setting: editor.draft.transient; onAdjusted: v => editor.update("transient", v) }
    Word { text: "频段增益"; font.weight: Font.DemiBold; Layout.topMargin: 6 }
    Repeater {
        model: ["80 Hz","250 Hz","1 kHz","5 kHz"]
        Knob {
            required property int index
            required property string modelData
            caption: modelData; suffix: " dB"; low: -12; high: 12; step: 1
            setting: editor.draft.eq[index]
            onAdjusted: v => {
                const gains = editor.draft.eq.slice()
                gains[index] = v
                editor.update("eq", gains)
            }
        }
    }
}
