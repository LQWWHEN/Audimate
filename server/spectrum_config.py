"""Version 1 desktop visualizer settings, shared by the room and player."""

STYLES = ("pop", "heavy", "light")
EQ_HZ = (80, 250, 1000, 5000)
COUNTS = (48, 64, 96)


def preset(style="pop"):
    gate, gains, contrast, transient = {
        "pop": (-56, [0, 0, 0, 0], 1.8, 1.0),
        "heavy": (-50, [4, 2, 0, -2], 2.0, 1.35),
        "light": (-62, [0, 0, 1, 1], 1.45, .65),
    }[style]
    return dict(version=1, base=style, custom=False, start=20, count=96,
                gate=gate, eq=gains, contrast=contrast, transient=transient)


def validate(config):
    if not isinstance(config, dict) or type(config.get("version")) is not int or config["version"] != 1:
        raise ValueError("频谱配置版本无效")
    base = config.get("base")
    if base not in STYLES or type(config.get("custom")) is not bool:
        raise ValueError("频谱预设无效")
    result = preset(base)
    for key, low, high in (("start", 10, 60), ("gate", -72, -24),
                           ("contrast", .5, 3), ("transient", 0, 2)):
        value = config.get(key)
        if type(value) not in (int, float) or not low <= value <= high:
            raise ValueError("频谱参数超出范围")
        result[key] = value
    count, eq = config.get("count"), config.get("eq")
    if type(count) is not int or count not in COUNTS:
        raise ValueError("频谱段数无效")
    if not isinstance(eq, list) or len(eq) != len(EQ_HZ) or any(
            type(g) not in (int, float) or not -12 <= g <= 12 for g in eq):
        raise ValueError("均衡增益须为 −12 至 +12 dB")
    result.update(count=count, eq=list(eq))
    result["custom"] = config["custom"] or result != preset(base)
    return result
