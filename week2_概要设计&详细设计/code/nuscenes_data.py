"""Read the local nuScenes mini metadata and prepare one offline import."""

import json
from collections import defaultdict
from math import hypot, isfinite
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parent
MIN_BRAKING_SPEED = 2.0  # m/s
BRAKING_ACCELERATION = -2.5  # m/s²


def read_json(path):
    with path.open(encoding="utf-8-sig") as stream:
        return json.load(stream)


def by_token(metadata, table):
    records = read_json(metadata / (table + ".json"))
    indexed = {record["token"]: record for record in records}
    if len(indexed) != len(records):
        raise ValueError("{} 存在重复的 token".format(table))
    return indexed


def camera_image(root, filename):
    relative = PurePosixPath(filename)
    if (len(relative.parts) != 3 or relative.parts[:2] != ("samples", "CAM_FRONT")
            or relative.suffix.lower() not in {".jpg", ".jpeg"} or "\\" in filename
            or ":" in filename):
        raise ValueError("无效的前向相机图片路径：{}".format(filename))
    front_dir = (root / "samples" / "CAM_FRONT").resolve()
    path = (front_dir / relative.name).resolve()
    if path.parent != front_dir or not path.is_file():
        raise FileNotFoundError("找不到前向相机图片：{}".format(filename))
    return path


def select_frames(frames, count=5):
    if len(frames) <= count:
        return frames
    first, last = frames[0][1], frames[-1][1]
    selected = [frames[0]]
    lo = 1
    for slot in range(1, count - 1):
        target = first + (last - first) * slot / (count - 1)
        upper = len(frames) - (count - slot) + 1
        index = min(range(lo, upper), key=lambda i: abs(frames[i][1] - target))
        selected.append(frames[index])
        lo = index + 1
    return selected + [frames[-1]]


def braking_events(points):
    """Estimate deceleration at a recorded pose using adjacent speed intervals."""
    candidates = []
    for prev, point, nxt in zip(points, points[1:], points[2:]):
        _, t0, x0, y0 = prev
        token, t1, x1, y1 = point
        _, t2, x2, y2 = nxt
        dt_before = (t1 - t0) / 1_000_000
        dt_after = (t2 - t1) / 1_000_000
        if not (0.2 <= dt_before <= 0.9 and 0.2 <= dt_after <= 0.9):
            continue
        before = hypot(x1 - x0, y1 - y0) / dt_before
        after = hypot(x2 - x1, y2 - y1) / dt_after
        acceleration = (after - before) / ((dt_before + dt_after) / 2)
        if before >= MIN_BRAKING_SPEED and acceleration <= BRAKING_ACCELERATION:
            candidates.append((token, t1, x1, y1, acceleration, before))

    peaks = []
    previous_time = None
    for event in candidates:
        if previous_time is None or event[1] - previous_time > 1_500_000:
            peaks.append(event)
        elif event[4] < peaks[-1][4]:
            peaks[-1] = event
        previous_time = event[1]
    return peaks


def read_regions(path):
    rows = []
    ids = set()
    for region in read_json(path):
        x1, y1, x2, y2 = region["bounds"]
        if not all(isfinite(x) for x in (x1, y1, x2, y2)) or not (x1 < x2 and y1 < y2):
            raise ValueError("路口 {} 的边界无效".format(region["id"]))
        if not region["id"] or region["id"] in ids:
            raise ValueError("路口编号为空或重复：{}".format(region["id"]))
        ids.add(region["id"])
        rows.append((region["id"], region["name"], region["location"], x1, y1, x2, y2,
                     region["reference_scene"]))
    return rows


def prepare_dataset(root, version, regions_path):
    metadata = root / version
    if not metadata.is_dir():
        raise FileNotFoundError("找不到 nuScenes 元数据目录：{}".format(metadata))
    scenes = by_token(metadata, "scene")
    samples = by_token(metadata, "sample")
    logs = by_token(metadata, "log")
    poses = by_token(metadata, "ego_pose")
    channel_tokens = {r["token"] for r in read_json(metadata / "sensor.json")
                      if r["channel"] == "CAM_FRONT"}
    front_sensors = {r["token"] for r in read_json(metadata / "calibrated_sensor.json")
                     if r["sensor_token"] in channel_tokens}

    samples_by_scene = defaultdict(list)
    for sample in samples.values():
        if sample["scene_token"] not in scenes:
            raise ValueError("sample {} 的场景编号不存在".format(sample["token"]))
        samples_by_scene[sample["scene_token"]].append(sample)

    front_frames = {}
    for frame in read_json(metadata / "sample_data.json"):
        if frame["is_key_frame"] and frame["calibrated_sensor_token"] in front_sensors:
            sample_id = frame["sample_token"]
            if sample_id not in samples or sample_id in front_frames:
                raise ValueError("CAM_FRONT 对应的 sample 无效或重复：{}".format(sample_id))
            front_frames[sample_id] = frame

    result = {"scenes": [], "track_points": [], "events": [], "keyframes": [],
              "regions": read_regions(regions_path)}
    if not scenes:
        raise ValueError("scene.json 中没有可导入的场景")
    for scene_id, scene in scenes.items():
        name = scene["name"]
        try:
            scene_samples = sorted(samples_by_scene[scene_id], key=lambda r: r["timestamp"])
            if not scene_samples or len(scene_samples) != scene["nbr_samples"]:
                raise ValueError("sample 数量与 scene 记录不一致")
            location = logs[scene["log_token"]]["location"]
            frames, points = [], []
            timestamps = [sample["timestamp"] for sample in scene_samples]
            for sample in scene_samples:
                frame = front_frames[sample["token"]]
                pose = poses[frame["ego_pose_token"]]
                x, y = pose["translation"][:2]
                if not isfinite(x) or not isfinite(y):
                    raise ValueError("轨迹坐标无效：{}".format(pose["token"]))
                points.append((pose["token"], pose["timestamp"], x, y))
                frames.append((frame["token"], frame["timestamp"], frame["filename"]))
                timestamps.extend((pose["timestamp"], frame["timestamp"]))
            if any(type(timestamp) is not int or timestamp <= 0 for timestamp in timestamps):
                raise ValueError("录制时间须为正整数微秒")
            points.sort(key=lambda p: (p[1], p[0]))
            frames.sort(key=lambda f: (f[1], f[0]))
            if any(a[1] >= b[1] for a, b in zip(points, points[1:])):
                raise ValueError("轨迹采样时间重复")
            selected = select_frames(frames)
            for _, _, filename in selected:
                camera_image(root, filename)
        except (KeyError, ValueError, OSError, TypeError) as exc:
            raise ValueError("{} 导入校验失败：{}".format(name, exc)) from exc

        result["scenes"].append((scene_id, name, location, min(timestamps), max(timestamps) + 1))
        for table, records in (("track_points", points), ("events", braking_events(points)),
                               ("keyframes", selected)):
            result[table].extend((record[0], scene_id, *record[1:]) for record in records)
    return result
