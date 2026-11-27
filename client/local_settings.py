"""Defaults for the Qt desktop player, shared across rooms on this computer."""
import json
from pathlib import Path

from PySide6.QtCore import QIODevice, QSaveFile

from host.capture.record import AudioFormat
from server import spectrum_config
from .sync import threshold_ms


DEFAULTS = dict(delay=50, motion="system", rate=48000, bits=16, codec="FLAC",
                recognize=False, style="pop", threshold="", receive="Opus", auto=True, interval=5)


def validate(values):
    data = {key: values.get(key, value) for key, value in DEFAULTS.items()}
    for key, choices in (
        ("motion", ("system", "full", "reduced")), ("style", ("pop", "heavy", "light")),
        ("codec", ("PCM", "FLAC", "Opus 64", "Opus 128", "Opus 256")),
        ("receive", ("Opus", "无损 PCM")),
    ):
        if data[key] not in choices:
            raise ValueError("请选择有效的预设。")
    for key in ("rate", "bits", "interval", "delay"):
        value = data[key]
        if isinstance(value, float) and value.is_integer():
            data[key] = int(value)
        elif type(value) is not int:
            raise ValueError("请输入整数。")
    try:
        AudioFormat(data["rate"], data["bits"])
    except ValueError as exc:
        raise ValueError("采样率须为 8000 至 192000 Hz 之间的整百数，位深可选 16、24 或 32 bit。") from exc
    if not 0 <= data["delay"] <= 200 or not 1 <= data["interval"] <= 300:
        raise ValueError("请检查音画延迟和自动同步间隔。")
    if any(type(data[k]) is not bool for k in ("auto", "recognize")) or not isinstance(data["threshold"], str):
        raise ValueError("设置值无效。")
    # Room-specific caps are applied when the host requests the stage.
    threshold_ms(data["threshold"], 60000)
    config = values.get("spectrum", spectrum_config.preset(data["style"]))
    data["spectrum"] = spectrum_config.validate(config)
    data["style"] = data["spectrum"]["base"]
    return data


def load(path):
    source = path if path.exists() else path.with_name("listening.json")
    try:
        values = json.loads(source.read_text(encoding="utf8"))
        if isinstance(values, dict):
            return validate(values)
    except (OSError, ValueError):
        pass
    return validate(DEFAULTS)


def save(path: Path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    file = QSaveFile(str(path))
    payload = json.dumps(values, ensure_ascii=False, indent=2).encode("utf8")
    if not file.open(QIODevice.OpenModeFlag.WriteOnly) or file.write(payload) != len(payload) or not file.commit():
        raise OSError(file.errorString())
