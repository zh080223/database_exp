"""Short descriptions and category aliases for visible road objects."""

import re


MAX_TARGETS = 3
NO_TARGETS = "未见明显目标"
VEHICLE_ALIASES = {
    "bicycle": ("自行车", "单车", "脚踏车", "骑自行车的人", "自行车骑行者"),
    "electric_bicycle": ("电动自行车", "电单车", "电瓶车"),
    "motorcycle": ("摩托车", "机车", "摩托"),
    "bus": ("公交车", "公交", "公共汽车", "巴士", "大巴", "大客车"),
    "sedan": ("轿车", "小轿车"),
    "suv": ("suv", "越野车"),
    "mpv": ("mpv", "商务车"),
    "van": ("面包车", "厢式车"),
    "truck": ("卡车", "货车", "皮卡"),
    "taxi": ("出租车", "的士", "计程车"),
    "passenger_car": ("私家车", "乘用车", "小汽车", "小车", "跑车"),
    "vehicle": ("车辆", "汽车", "机动车", "类型不明的车辆"),
}
TARGET_TYPES = {
    alias: category
    for category, aliases in dict(
        VEHICLE_ALIASES, pedestrian=("行人", "路人", "步行者", "人群", "儿童", "人"),
    ).items()
    for alias in aliases
}
TARGET_SUFFIXES = sorted(
    (alias for alias in TARGET_TYPES if len(alias) > 1), key=len, reverse=True,
)


def query_categories(description):
    term = description.casefold().strip(" ?,，。？！!")
    term = re.sub(r"^(?:(?:画面|图|照片)(?:中|里))?有\s*", "", term)
    if term in {"车", "车辆"}:
        return tuple(VEHICLE_ALIASES)
    if term in {"汽车", "机动车"}:
        return tuple(vehicle for vehicle in VEHICLE_ALIASES if vehicle not in {"bicycle", "electric_bicycle"})
    if term in VEHICLE_ALIASES["passenger_car"]:
        return ("passenger_car", "sedan", "suv", "mpv")
    category = TARGET_TYPES.get(term)
    return (category,) if category else ()


def target_category(target):
    name = target.casefold()
    if name == "车":
        return "vehicle"
    if name in TARGET_TYPES:
        return TARGET_TYPES[name]
    return next((TARGET_TYPES[alias] for alias in TARGET_SUFFIXES if name.endswith(alias)), None)


def parse_caption(text):
    observations, keywords, categories, seen = [], [], [], set()
    for line in text.splitlines():
        columns = re.split(r"[｜|]", line.strip().strip("｜|"))
        if len(columns) < 2:
            continue
        target = re.sub(r"^(?:[-*•]\s*|\d+[.、)）]\s*)", "", columns[0].strip()).strip(" *")[:30]
        position = columns[1].strip(" *")
        position = re.sub(r"^(?:位于|在)?自车(?:的)?", "", position)
        if (not target or target in {"目标", "目标类型"} or not target.strip("-:")
                or not position or not position.strip("-:")):
            continue
        direction = re.match(r"左前方|正前方|右前方", position)
        category = target_category(target)
        key = (category or target, direction[0] if direction else position)
        if key in seen:
            continue
        seen.add(key)
        observations.append(f"{target}｜{position[:60]}")
        if target not in keywords:
            keywords.append(target)
        if category and category not in categories:
            categories.append(category)
        if len(observations) == MAX_TARGETS:
            break

    if not observations:
        if text.strip(" \r\n*。！!「」“”") in {NO_TARGETS, "无明显目标", "没有明显目标"}:
            return NO_TARGETS, "", []
        raise ValueError("Qwen 未返回有效的目标和位置条目")
    return "\n".join(observations), "、".join(keywords), categories
