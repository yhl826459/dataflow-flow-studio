"""Encode the exact Qwen target list in GenCeption's referring-expression format."""

import argparse
import json
from pathlib import Path

PREFIX = "Input: RGB. Output: Segmentation Masks. Referring Expression: "


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    targets = json.loads(args.request.read_text(encoding="utf-8"))
    if not targets:
        raise ValueError("No phrases to encode")
    if args.out.exists():
        raise FileExistsError(args.out)

    import numpy as np
    import torch
    from transformers import T5Tokenizer, UMT5EncoderModel

    torch.set_num_threads(8)
    tokenizer = T5Tokenizer.from_pretrained(args.model, local_files_only=True)
    model = UMT5EncoderModel.from_pretrained(
        args.model, torch_dtype=torch.bfloat16, local_files_only=True).eval()
    values = np.zeros((len(targets), 1, 226, 4096), dtype=np.float32)
    with torch.inference_mode():
        for index, target in enumerate(targets):
            text = PREFIX + target["phrase"].rstrip(".") + "."
            inputs = tokenizer(text, return_tensors="pt")
            if not 1 <= inputs.input_ids.shape[-1] <= 226:
                raise ValueError(f"Referring expression exceeds GenCeption's 226 tokens: {text!r}")
            hidden = model(**inputs).last_hidden_state[0].float().numpy()
            values[index, 0, :len(hidden)] = hidden
            print(f"Encoded {target['id']!r} ({len(hidden)} tokens)", flush=True)

    temporary = args.out.with_name(args.out.name + ".partial")
    with temporary.open("xb") as stream:
        np.savez_compressed(stream, targets=json.dumps(targets, ensure_ascii=False), embeddings=values)
    temporary.rename(args.out)


if __name__ == "__main__":
    main()
