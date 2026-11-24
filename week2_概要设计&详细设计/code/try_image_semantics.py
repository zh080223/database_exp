"""Compare nuScenes images against Chinese text with Chinese-CLIP RN50 on CPU."""

import argparse
from pathlib import Path
from time import perf_counter


DEFAULT_WEIGHTS = Path(__file__).resolve().parent / ".runtime" / "models" / "clip_cn_rn50.pt"


def main():
    parser = argparse.ArgumentParser(description="在 CPU 上试跑 Chinese-CLIP RN50 并按图文相似度排序")
    parser.add_argument("images", type=Path, nargs="+", metavar="图片")
    parser.add_argument("--query", action="append", help="中文查询，可重复填写")
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS, help="RN50 权重文件路径")
    args = parser.parse_args()

    queries = [query.strip() for query in (args.query or ["画面中有行人"])]
    if any(not query for query in queries):
        parser.error("查询文字不能为空。")
    for path in [args.weights, *args.images]:
        if not path.is_file():
            parser.error(f"找不到文件 {path}")

    import torch
    from cn_clip.clip import load_from_name, tokenize
    from PIL import Image
    from torch.nn.functional import normalize

    load_start = perf_counter()
    model, preprocess = load_from_name(
        str(args.weights.resolve()),
        device="cpu",
        vision_model_name="RN50",
        text_model_name="RBT3-chinese",
        input_resolution=224,
    )
    model.eval()
    print(f"权重加载耗时 {perf_counter() - load_start:.2f} 秒")

    inference_start = perf_counter()
    image_scores = []
    with torch.inference_mode():
        text_features = normalize(model.encode_text(tokenize(queries)), dim=-1)
        for path in args.images:
            with Image.open(path) as image:
                image_tensor = preprocess(image.convert("RGB")).unsqueeze(0)
            image_features = normalize(model.encode_image(image_tensor), dim=-1)
            image_scores.append((image_features @ text_features.T)[0].tolist())
    print(f"{len(args.images)} 张图、{len(queries)} 条查询的推理耗时 {perf_counter() - inference_start:.2f} 秒")

    for query, scores in zip(queries, zip(*image_scores)):
        print(f"\n{query}")
        matches = sorted(zip(args.images, scores), key=lambda item: item[1], reverse=True)
        for path, score in matches:
            print(f"  {score: .4f}  {path}")


if __name__ == "__main__":
    main()
