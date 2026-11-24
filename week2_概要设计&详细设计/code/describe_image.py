"""Generate a Chinese description of a local image with Qwen3-VL."""

import argparse
import sys
from importlib.util import find_spec
from pathlib import Path

from scene_objects import MAX_TARGETS, NO_TARGETS, parse_caption


MODEL_DIR = Path(__file__).resolve().parent / ".runtime" / "models"
DEFAULT_PROMPT = (
    "这是一张自车前置相机拍摄的道路照片。以自车朝向为基准，画面左侧对应自车左侧，右侧对应自车右侧。\n"
    f"只列出最主要的可见行人、车辆或道路设施，最多{MAX_TARGETS}项。优先写自车前方和近处的目标。"
    "同一位置的同类目标合并成一项。\n"
    "车辆写能看清的具体类型，例如自行车、电动自行车、摩托车、公交车、轿车、SUV、面包车或货车。"
    "类型看不清时写车辆，颜色清楚时可以加颜色。\n"
    "每行只写两个字段：目标类型｜相对自车的位置。位置用左前方、正前方或右前方，可补充远近、车道或人行道。"
    "只写相对位置，省略目标朝向、行进方向和其它解释。\n"
    f"列完立即结束。画面没有主要目标时，只写「{NO_TARGETS}」。"
)


def add_inference_arguments(parser):
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto", help="Qwen 运行设备")
    parser.add_argument("--precision", choices=("auto", "fp32", "fp16", "bf16", "int4"),
                        default="auto", help="Qwen 加载精度，int4 使用 CUDA 和 bitsandbytes")


def load_captioner(model_dir, device="auto", precision="auto"):
    model_dir = model_dir.resolve()
    if not (model_dir / "model.safetensors").is_file():
        raise FileNotFoundError(f"找不到权重 {model_dir / 'model.safetensors'}")
    if not (model_dir / "config.json").is_file():
        raise FileNotFoundError(f"请将 Qwen3-VL-2B-Instruct 的配置和分词器文件下载到 {model_dir}")

    import torch
    from transformers import AutoProcessor, Qwen3VLForConditionalGeneration

    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cuda" and not torch.cuda.is_available():
        raise ValueError("当前 PyTorch 无法使用 CUDA，请检查 NVIDIA 驱动和 PyTorch 的 CUDA 支持。")
    if precision == "int4" and device != "cuda":
        raise ValueError("4 位量化需要 CUDA，请使用 --device cuda --precision int4。")

    native_bf16 = device == "cuda" and torch.cuda.is_bf16_supported(including_emulation=False)
    if precision == "bf16" and device == "cuda" and not native_bf16:
        raise ValueError("当前 GPU 缺少原生 BF16 支持，可使用 --precision fp16。")
    weight_precision = precision
    if precision in {"auto", "int4"}:
        weight_precision = "fp32" if device == "cpu" else ("bf16" if native_bf16 else "fp16")
    dtype = {"fp32": torch.float32, "fp16": torch.float16, "bf16": torch.bfloat16}[weight_precision]

    load_options = {}
    if precision == "int4":
        if find_spec("bitsandbytes") is None:
            raise ValueError("请在运行脚本的 Python 环境中执行 python -m pip install bitsandbytes。")
        from transformers import BitsAndBytesConfig

        load_options["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=dtype, llm_int8_skip_modules=["visual", "lm_head"],
        )

    mode = f"NF4 量化，计算精度 {weight_precision}" if precision == "int4" else weight_precision
    print(f"加载 Qwen，设备 {device}，精度 {mode}", file=sys.stderr, flush=True)
    processor = AutoProcessor.from_pretrained(model_dir, local_files_only=True)
    model = Qwen3VLForConditionalGeneration.from_pretrained(
        model_dir, dtype=dtype, device_map=device, local_files_only=True, use_safetensors=True, **load_options
    )
    return model, processor


def describe_image(model, processor, image, prompt=DEFAULT_PROMPT):
    import torch

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "path": str(image.resolve())},
                {"type": "text", "text": prompt},
            ],
        }
    ]
    inputs = processor.apply_chat_template(
        messages,
        add_generation_prompt=True,
        tokenize=True,
        return_dict=True,
        return_tensors="pt",
    )

    inputs = inputs.to(model.device)
    if prompt == DEFAULT_PROMPT:
        generation = dict(max_new_tokens=128, do_sample=False, temperature=1.0, top_p=1.0, top_k=50)
    else:
        generation = dict(max_new_tokens=256)
    with torch.inference_mode():
        output_ids = model.generate(**inputs, **generation)

    prompt_length = inputs["input_ids"].shape[1]
    description = processor.batch_decode(output_ids[:, prompt_length:], skip_special_tokens=True)[0]
    return description.strip()


def main():
    parser = argparse.ArgumentParser(description="用 Qwen3-VL 生成图片的中文描述")
    parser.add_argument("image", type=Path, metavar="图片路径")
    parser.add_argument("--model-dir", type=Path, default=MODEL_DIR, help="本地权重与配置文件目录")
    add_inference_arguments(parser)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="自定义中文问题或描述要求")
    args = parser.parse_args()
    if not args.image.is_file():
        parser.error(f"找不到图片 {args.image}")
    try:
        model, processor = load_captioner(args.model_dir, args.device, args.precision)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    description = describe_image(model, processor, args.image, args.prompt)
    if args.prompt == DEFAULT_PROMPT:
        try:
            description, _, _ = parse_caption(description)
        except ValueError as exc:
            parser.error(str(exc))
    print(description)


if __name__ == "__main__":
    main()
