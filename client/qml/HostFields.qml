import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

GridLayout {
    id: form
    columns: 2; columnSpacing: 16; rowSpacing: 2
    property var codecs: ["FLAC","PCM","Opus 64","Opus 128","Opus 256"]
    property bool canRecognize: true
    readonly property var styles: ["pop","heavy","light"]
    readonly property var values: ({rate: Number(rate.editText), bits: Number(bits.currentText),
        codec: codec.currentText, recognize: recognition.checked && canRecognize,
        style: styles[style.currentIndex], threshold: threshold.text.trim()})
    function load(p) {
        const index = rate.find(String(p.rate))
        rate.currentIndex = index
        rate.editText = String(p.rate)
        bits.currentIndex = bits.find(String(p.bits))
        codec.currentIndex = Math.max(0, codecs.indexOf(p.codec))
        recognition.checked = p.recognize && canRecognize
        style.currentIndex = Math.max(0, styles.indexOf(p.style))
        threshold.text = p.threshold
    }
    Word { text: "采样率 (Hz)"; color: "#c1c7db" }
    ComboBox { id: rate; objectName: "captureRate"; Layout.fillWidth: true; Layout.preferredHeight: 42; editable: true; Accessible.name: "采样率 Hz"
        model: ["44100","48000","88200","96000","192000"]; validator: IntValidator { bottom: 8000; top: 192000 } }
    Word { text: "位深 (bit)"; color: "#c1c7db" }
    ComboBox { id: bits; Layout.fillWidth: true; Layout.preferredHeight: 42; model: ["16","24","32"]; Accessible.name: "采样位深 bit" }
    Word { text: "上传格式"; color: "#c1c7db" }
    ComboBox { id: codec; Layout.fillWidth: true; Layout.preferredHeight: 42; model: form.codecs; Accessible.name: "上传格式" }
    Word { text: "房间频谱"; color: "#c1c7db" }
    ComboBox { id: style; Layout.fillWidth: true; Layout.preferredHeight: 42; model: ["流行摇滚","重型音乐","轻音乐"]; Accessible.name: "房间频谱" }
    Word { text: "同步阈值"; color: "#c1c7db" }
    TextField { id: threshold; Layout.fillWidth: true; Layout.preferredHeight: 44; placeholderText: "秒，留空跟随房间"; Accessible.name: "主持同步阈值（秒）" }
    Item { implicitWidth: 1; implicitHeight: 1 }
    CheckBox { id: recognition; text: "Shazam 识曲"; enabled: form.canRecognize }
}
