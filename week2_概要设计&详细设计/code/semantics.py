"""Find front camera keyframes by their generated Chinese descriptions."""

import json
from contextlib import contextmanager

from scene_objects import MAX_TARGETS, query_categories


COLLECTION = "nuscenes_cam_front_qwen_objects"
BATCH_SIZE = 128
MAX_QUERY_LENGTH = 100
RESULT_FIELDS = ["frame_id", "description", "keywords"]


class SemanticError(RuntimeError):
    pass


@contextmanager
def connect_vectors(uri):
    if not uri:
        raise SemanticError("请通过 --milvus-uri 指定 Milvus 地址。")
    try:
        from pymilvus import MilvusClient
        from pymilvus.exceptions import MilvusException
    except ImportError as exc:
        raise SemanticError("请在项目环境中安装 PyMilvus。") from exc
    client = None
    try:
        client = MilvusClient(uri=uri, timeout=5)
        yield client
    except MilvusException as exc:
        raise SemanticError(f"Milvus 操作失败：{exc}") from exc
    finally:
        if client is not None:
            client.close()


def create_collection(client):
    from pymilvus import DataType, Function, FunctionType

    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field(field_name="frame_id", datatype=DataType.VARCHAR, is_primary=True, max_length=64)
    schema.add_field(field_name="scene_id", datatype=DataType.VARCHAR, max_length=64)
    schema.add_field(field_name="description", datatype=DataType.VARCHAR, max_length=8192)
    schema.add_field(field_name="keywords", datatype=DataType.VARCHAR, max_length=512)
    schema.add_field(field_name="categories", datatype=DataType.ARRAY, element_type=DataType.VARCHAR,
                     max_capacity=MAX_TARGETS, max_length=32)
    schema.add_field(
        field_name="search_text", datatype=DataType.VARCHAR, max_length=9000, enable_analyzer=True,
        analyzer_params={"tokenizer": "jieba", "filter": [
            "cnalphanumonly",
            {"type": "stop", "stop_words": ["画面", "画面中", "图中", "图片", "中", "的", "有", "了", "在"]},
        ]},
    )
    schema.add_field(field_name="sparse", datatype=DataType.SPARSE_FLOAT_VECTOR)
    schema.add_function(Function(
        name="caption_bm25", function_type=FunctionType.BM25,
        input_field_names=["search_text"], output_field_names=["sparse"],
    ))
    client.create_collection(
        collection_name=COLLECTION, schema=schema, consistency_level="Strong", timeout=60,
    )
    load_collection(client)


def load_collection(client):
    if not client.list_indexes(collection_name=COLLECTION, field_name="sparse", timeout=10):
        indexes = client.prepare_index_params()
        indexes.add_index(field_name="sparse", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25",
                          params={"inverted_index_algo": "DAAT_MAXSCORE"})
        client.create_index(collection_name=COLLECTION, index_params=indexes, timeout=60)
    client.load_collection(collection_name=COLLECTION, timeout=60)


def frame_batches(frame_ids):
    for offset in range(0, len(frame_ids), BATCH_SIZE):
        yield frame_ids[offset:offset + BATCH_SIZE]


def search_batch(client, frame_ids, description, categories):
    frame_filter = "frame_id in " + json.dumps(frame_ids)
    if categories:
        rows = client.query(
            collection_name=COLLECTION, output_fields=RESULT_FIELDS,
            filter=frame_filter + " and ARRAY_CONTAINS_ANY(categories, " + json.dumps(categories) + ")",
            limit=len(frame_ids), timeout=10,
        )
        for row in rows:
            yield row.get("frame_id"), row, None
        return

    matches = client.search(
        collection_name=COLLECTION, anns_field="sparse", data=[description],
        filter=frame_filter, limit=len(frame_ids), search_params={"metric_type": "BM25"},
        output_fields=RESULT_FIELDS, timeout=10,
    )
    for hit in (matches[0] if matches else []):
        metadata = hit["entity"]
        frame_id = hit.get("frame_id") or metadata.get("frame_id") or hit.get("id")
        yield frame_id, metadata, float(hit["distance"])


def rank_scenes(scenes, frames, description, milvus_uri):
    by_scene = {scene["id"]: scene for scene in scenes}
    candidates = {frame["id"]: frame for frame in frames if frame["scene_id"] in by_scene}
    if not candidates:
        return []

    categories = query_categories(description)
    best_by_scene = {}
    with connect_vectors(milvus_uri) as client:
        if not client.has_collection(collection_name=COLLECTION, timeout=5):
            raise SemanticError("请先运行 python index_semantics.py 导入关键帧描述。")
        for frame_ids in frame_batches(list(candidates)):
            for frame_id, metadata, score in search_batch(client, frame_ids, description, categories):
                if not frame_id:
                    raise SemanticError("Milvus 命中结果缺少帧编号。")
                frame = candidates.get(frame_id)
                if frame is None:
                    raise SemanticError(f"Milvus 命中帧 {frame_id} 超出本次候选范围。")
                timestamp = frame["timestamp_us"]
                key = (-timestamp, frame_id) if score is None else (-score, timestamp, frame_id)
                scene_id = frame["scene_id"]
                if scene_id not in best_by_scene or key < best_by_scene[scene_id][0]:
                    result = dict(by_scene[scene_id], frame_id=frame["id"],
                                  frame_time_us=timestamp, relevance_score=score,
                                  description=metadata["description"],
                                  keywords=metadata["keywords"].split("、") if metadata["keywords"] else [])
                    best_by_scene[scene_id] = (key, result)

    return [result for _, result in sorted(best_by_scene.values(), key=lambda match: match[0])[:10]]
