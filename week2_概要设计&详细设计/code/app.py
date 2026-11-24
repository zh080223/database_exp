"""Local scene browser and read-only API."""

import argparse
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_file, url_for

from database import (DatabaseError, add_database_arguments, connect, database_options, find_scenes,
                      list_keyframes, list_regions, scene_detail)
from nuscenes_data import ROOT, camera_image
from semantics import MAX_QUERY_LENGTH, SemanticError, rank_scenes


DISPLAY_ZONE = timezone(timedelta(hours=8))
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
FILTER_NAMES = ("start", "end", "region", "brake", "description")
LOCATION_NAMES = {
    "boston-seaport": "波士顿 Seaport",
    "singapore-onenorth": "新加坡 One-North",
    "singapore-queenstown": "新加坡 Queenstown",
    "singapore-hollandvillage": "新加坡 Holland Village",
}

template_folder = "static/templates" if (ROOT / "static" / "templates").is_dir() else "templates"
app = Flask(__name__, template_folder=template_folder)
app.config["DATABASE_OPTIONS"] = {}
app.config["MILVUS_URI"] = os.environ.get("MILVUS_URI")
app.config["NUSCENES_ROOT"] = Path(os.environ.get("NUSCENES_ROOT") or ROOT / "v1").resolve()


def parse_time(value):
    if "T" not in value and " " not in value:
        raise ValueError("时间须包含日期与时刻")
    suffix = value[:-1] + "+00:00" if value.endswith("Z") else value
    moment = datetime.fromisoformat(suffix)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=DISPLAY_ZONE)
    delta = moment - EPOCH
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


def iso_time(us):
    return (EPOCH + timedelta(microseconds=us)).astimezone(DISPLAY_ZONE).isoformat(timespec="microseconds")


@app.template_filter("time")
def display_time(us):
    return iso_time(us)[:23].replace("T", " ")


@app.template_filter("place")
def display_location(location):
    return LOCATION_NAMES.get(location, location)


def filter_values():
    return {name: request.args[name].strip() for name in FILTER_NAMES if request.args.get(name, "").strip()}


def parse_filters(values, regions):
    start, end = values.get("start"), values.get("end")
    region, brake_value = values.get("region"), values.get("brake", "0")
    description = values.get("description", "")
    if len(description) > MAX_QUERY_LENGTH:
        raise ValueError(f"画面描述最多 {MAX_QUERY_LENGTH} 个字符。")
    if bool(start) != bool(end):
        raise ValueError("请同时填写开始和结束时间。")
    if start:
        try:
            start, end = parse_time(start), parse_time(end)
        except (ValueError, OverflowError) as exc:
            raise ValueError("时间格式无效，请选择日期和时刻。") from exc
        if start >= end:
            raise ValueError("结束时间须晚于开始时间。")
    if region and region not in {row["id"] for row in regions}:
        raise ValueError("请选择列表中的路口。")
    if brake_value not in {"0", "1"}:
        raise ValueError("急刹筛选值须为 0 或 1。")
    brake = brake_value == "1"
    if start is None and not region and not brake and not description:
        raise ValueError("请至少选择一个筛选条件。")
    return {"start": start, "end": end, "region": region, "brake": brake, "description": description}


def query_scenes(database, filters):
    options = (filters or {}).copy()
    description = options.pop("description", "")
    scenes = find_scenes(database, **options)
    if not description:
        return "spatiotemporal", scenes
    mode = "hybrid" if options["start"] is not None or options["region"] or options["brake"] else "semantic"
    if not scenes:
        return mode, []
    frames = list_keyframes(database, options["start"], options["end"])
    return mode, rank_scenes(scenes, frames, description, app.config["MILVUS_URI"])


def frame_payload(frame_id, timestamp):
    return {"id": frame_id, "time": iso_time(timestamp), "image_url": url_for("image", frame_id=frame_id)}


@app.route("/")
def index():
    values = filter_values()
    search = bool(request.args)
    filters, error, scenes, mode = None, None, [], "spatiotemporal"
    with connect(**app.config["DATABASE_OPTIONS"]) as database:
        regions = list_regions(database)
        if search:
            try:
                filters = parse_filters(values, regions)
            except ValueError as exc:
                error = str(exc)
        if not error:
            mode, scenes = query_scenes(database, filters)
    names = {row["id"]: row["name"] for row in regions}
    return render_template("index.html", scenes=scenes, regions=regions, values=values,
                           selected_region=names.get(values.get("region")), search=search,
                           error=error, mode=mode, semantic_enabled=bool(app.config["MILVUS_URI"]),
                           max_query_length=MAX_QUERY_LENGTH), 400 if error else 200


@app.route("/scene/<scene_id>")
def detail(scene_id):
    with connect(**app.config["DATABASE_OPTIONS"]) as database:
        scene = scene_detail(database, scene_id)
    if scene is None:
        abort(404)
    values = filter_values()
    selected_id = request.args.get("frame")
    first = next((frame for frame in scene["frames"] if frame["id"] == selected_id), None)
    return render_template("detail.html", scene=scene, values=values,
                           first=first or (scene["frames"][0] if scene["frames"] else None))


@app.route("/api/regions")
def api_regions():
    with connect(**app.config["DATABASE_OPTIONS"]) as database:
        return jsonify(list_regions(database))


@app.route("/api/scenes")
def api_scenes():
    values = filter_values()
    with connect(**app.config["DATABASE_OPTIONS"]) as database:
        regions = list_regions(database)
        try:
            filters = parse_filters(values, regions)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        mode, scenes = query_scenes(database, filters)
    selected = next((r for r in regions if r["id"] == filters["region"]), None)
    results = []
    for row in scenes:
        item = {"id": row["id"], "name": row["name"], "location": row["location"],
                "start": iso_time(row["start_us"]), "end": iso_time(row["end_us"]),
                "detail_url": url_for("detail", scene_id=row["id"], **values),
                "representative_frame": frame_payload(row["frame_id"], row["frame_time_us"])}
        if "relevance_score" in row:
            item["relevance_score"] = row["relevance_score"]
            item["representative_frame"].update(description=row["description"], keywords=row["keywords"])
            item["detail_url"] = url_for("detail", scene_id=row["id"], frame=row["frame_id"], **values)
        if row["evidence_time_us"] is not None:
            item["evidence"] = {
                "type": "estimated_braking" if filters["brake"] else "track_point",
                "time": iso_time(row["evidence_time_us"]), "x": row["evidence_x"], "y": row["evidence_y"],
                "region": selected["id"] if selected else None,
            }
            if filters["brake"]:
                item["evidence"]["acceleration_mps2"] = row["acceleration"]
        results.append(item)
    return jsonify(mode=mode, count=len(results), results=results)


@app.route("/api/scenes/<scene_id>")
def api_detail(scene_id):
    with connect(**app.config["DATABASE_OPTIONS"]) as database:
        scene = scene_detail(database, scene_id)
    if scene is None:
        return jsonify(error="场景不存在。"), 404
    return jsonify(
        id=scene["id"], name=scene["name"], location=scene["location"],
        start=iso_time(scene["start_us"]), end=iso_time(scene["end_us"]),
        trajectory=[{"time": iso_time(p["timestamp_us"]), "x": p["x"], "y": p["y"]} for p in scene["track"]],
        events=[{"id": e["id"], "type": "estimated_braking", "time": iso_time(e["timestamp_us"]),
                 "x": e["x"], "y": e["y"], "acceleration_mps2": e["acceleration"],
                 "previous_speed_mps": e["speed_before"]} for e in scene["events"]],
        frames=[frame_payload(f["id"], f["timestamp_us"]) for f in scene["frames"]],
    )


@app.route("/frame/<frame_id>")
def image(frame_id):
    with connect(**app.config["DATABASE_OPTIONS"]) as database:
        row = database.execute("SELECT filename FROM keyframes WHERE id = ?", (frame_id,)).fetchone()
    if row is None:
        abort(404)
    try:
        path = camera_image(app.config["NUSCENES_ROOT"], row["filename"])
    except (OSError, ValueError):
        abort(404)
    return send_file(str(path), mimetype="image/jpeg", conditional=True)


@app.errorhandler(DatabaseError)
def database_error(error):
    app.logger.error("Database query failed: %s", error)
    message = "场景数据库暂不可用。请确认已导入数据且数据库连接正常。"
    if request.path.startswith("/api/"):
        return jsonify(error=message), 503
    return render_template("message.html", title="数据库暂不可用", message=message), 503


@app.errorhandler(SemanticError)
def semantic_error(error):
    app.logger.error("Semantic search failed: %s", error)
    message = "画面语义检索暂不可用。请确认 Milvus 连接并完成关键帧描述导入。"
    if request.path.startswith("/api/"):
        return jsonify(error=message), 503
    return render_template("message.html", title="语义检索暂不可用", message=message), 503


@app.errorhandler(404)
def missing(error):
    return render_template("message.html", title="找不到记录", message="该场景或图片当前不可访问。"), 404


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="启动智能驾驶场景查询系统")
    add_database_arguments(parser)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument("--milvus-uri", default=app.config["MILVUS_URI"])
    parser.add_argument("--dataset", type=Path, default=app.config["NUSCENES_ROOT"])
    args = parser.parse_args()
    app.config.update(DATABASE_OPTIONS=database_options(parser, args), MILVUS_URI=args.milvus_uri,
                      NUSCENES_ROOT=args.dataset.resolve())
    app.run(host=args.host, port=args.port)
