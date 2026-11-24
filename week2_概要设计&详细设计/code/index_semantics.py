"""Index already-imported CAM_FRONT keyframes in Milvus."""

import argparse
import os
import sys
from pathlib import Path

from database import DatabaseError, add_database_arguments, connect, database_options, list_keyframes
from describe_image import MODEL_DIR, add_inference_arguments, describe_image, load_captioner
from nuscenes_data import ROOT, camera_image
from scene_objects import parse_caption
from semantics import (COLLECTION, SemanticError, connect_vectors, create_collection,
                       frame_batches, load_collection)


def index_frames(args, db_options):
    with connect(**db_options) as database:
        frames = list_keyframes(database)
    if not frames:
        raise SemanticError("场景数据库里没有关键帧，请先运行 python import_data.py。")

    with connect_vectors(args.milvus_uri) as client:
        exists = client.has_collection(collection_name=COLLECTION, timeout=5)
        indexed = set()
        if exists:
            load_collection(client)
            for ids in frame_batches([frame["id"] for frame in frames]):
                rows = client.get(collection_name=COLLECTION, ids=ids, output_fields=["frame_id"],
                                  consistency_level="Strong", timeout=10)
                indexed.update(row["frame_id"] for row in rows)

        pending = [frame for frame in frames if frame["id"] not in indexed]
        print(f"关键帧 {len(frames)} 张，已入库 {len(indexed)} 张，待处理 {len(pending)} 张", flush=True)
        if not pending:
            return 0
        if args.limit is not None:
            pending = pending[:args.limit]

        captioner, processor = load_captioner(args.qwen_dir, args.device, args.precision)
        written, failed = 0, 0
        for frame in pending:
            try:
                image = camera_image(args.dataset, frame["filename"])
                description, keywords, categories = parse_caption(describe_image(captioner, processor, image))
            except (OSError, RuntimeError, ValueError) as exc:
                failed += 1
                print(f"处理失败 {frame['id']} {frame['filename']}：{exc}", file=sys.stderr, flush=True)
                continue

            if not exists:
                create_collection(client)
                exists = True
            description = description.encode("utf-8")[:8192].decode("utf-8", errors="ignore")
            client.upsert(collection_name=COLLECTION, data=[{
                "frame_id": frame["id"], "scene_id": frame["scene_id"],
                "description": description, "keywords": keywords, "categories": categories,
                "search_text": keywords.replace("、", " ") + "\n" + description,
            }], timeout=20)
            written += 1
            print(f"[{written + failed}/{len(pending)}] 已写入 {frame['id']} {frame['filename']}", flush=True)

        if written:
            client.flush(collection_name=COLLECTION, timeout=60)
        print(f"本次写入 {written} 张，失败 {failed} 张，当前数据集已索引 {len(indexed) + written}/{len(frames)} 张")
        return int(failed > 0)


def main():
    parser = argparse.ArgumentParser(description="用 Qwen 描述前摄关键帧并写入 Milvus")
    add_database_arguments(parser)
    parser.add_argument("--milvus-uri", default=os.environ.get("MILVUS_URI") or "http://127.0.0.1:19530")
    parser.add_argument("--dataset", type=Path, default=Path(os.environ.get("NUSCENES_ROOT") or ROOT / "v1"))
    parser.add_argument("--qwen-dir", type=Path, default=MODEL_DIR)
    add_inference_arguments(parser)
    parser.add_argument("--limit", type=int, help="本次最多处理多少张待入库图片")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit 须大于 0")
    try:
        return index_frames(args, database_options(parser, args))
    except (AssertionError, DatabaseError, ImportError, OSError, RuntimeError, ValueError) as exc:
        print(f"语义导入失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
