"""
HYPERION QUANT: HUGGING FACE BENCHMARK & MODEL CARD PUBLISHER
==============================================================
Prepares and uploads the Hyperion-LOB benchmark dataset and model card
to the Hugging Face Hub (Model & Dataset repositories).

Usage:
  1. Automated via Hugging Face API:
     python3 python/publish_to_huggingface.py --repo-id <username>/hyperion-lob-adverse-selection --token <HF_TOKEN>

  2. Or manual web upload:
     Files in `hf_publish/` can be dragged directly into Hugging Face Web UI:
     https://huggingface.co/new or https://huggingface.co/new-dataset
"""

import argparse
import json
import os
import sys

def prepare_benchmark_sample(output_path="hf_publish/benchmark_data_sample.jsonl", n_samples=1000):
    """Creates benchmark sample records. Every record is simulated with numpy (seed 42); none comes from a real market."""
    print(f"[*] Packaging benchmark sample ({n_samples:,} records) -> {output_path}...")
    import numpy as np
    np.random.seed(42)

    records = []
    # Mix of calm, trending, and toxic sweeps
    for i in range(n_samples):
        regime = np.random.choice([0, 1, 2], p=[0.70, 0.20, 0.10])
        spread = float(np.random.exponential(0.6) + 0.3 if regime == 0 else (np.random.exponential(1.5) + 0.8 if regime == 1 else np.random.exponential(3.5) + 2.0))
        imb_l0 = float(np.random.uniform(-0.15, 0.15) if regime == 0 else (np.random.uniform(-0.6, 0.6) if regime == 1 else np.random.choice([-1.0, 1.0]) * np.random.uniform(0.75, 0.98)))
        micro_bias = float(0.85 * imb_l0 * spread)
        ofi = float(np.random.normal(0, 5.0) if regime == 0 else (35.0 * imb_l0 if regime == 1 else np.sign(imb_l0) * 65.0))
        ret = float(np.random.normal(0, 0.00015))
        vol = float(abs(ret) * 120.0 + (regime == 2) * 0.04)
        trade_imb = float(np.sign(ofi) * (np.random.exponential(6.0) + 2.0) if regime == 2 else np.random.exponential(1.5))
        
        is_toxic = 1 if (0.35 * abs(micro_bias) + 0.025 * abs(ofi) + 0.40 * abs(imb_l0) + 0.15 * abs(trade_imb)) > 0.85 else 0

        records.append({
            "tick_index": i,
            "regime": "calm" if regime == 0 else ("trending" if regime == 1 else "toxic_sweep"),
            "spread_bps": round(spread, 3),
            "micro_price_bias_bps": round(micro_bias, 3),
            "imbalance_l0": round(imb_l0, 3),
            "imbalance_l1": round(imb_l0 * 0.7, 3),
            "ofi": round(ofi, 2),
            "returns": round(ret, 6),
            "volatility": round(vol, 4),
            "trade_imbalance": round(trade_imb, 2),
            "is_adverse_selection": is_toxic
        })

    with open(output_path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    print(f"[+] Sample dataset packaged successfully: {output_path}")

def publish_to_hf(repo_id: str, token: str, repo_type: str = "model"):
    try:
        from huggingface_hub import HfApi, create_repo
    except ImportError:
        print("\x1b[1;31m[!] huggingface_hub package not installed.\x1b[0m")
        print("    Install it via: pip install huggingface_hub")
        return False

    api = HfApi(token=token)
    print(f"[*] Creating/Accessing Hugging Face {repo_type} repository: {repo_id}...")
    create_repo(repo_id=repo_id, token=token, repo_type=repo_type, exist_ok=True)

    folder_path = "hf_publish"
    print(f"[*] Uploading files from {folder_path} to {repo_id}...")
    api.upload_folder(
        folder_path=folder_path,
        repo_id=repo_id,
        repo_type=repo_type,
        commit_message="feat: publish Hyperion-LOB adverse selection benchmark and model weights"
    )
    print(f"\x1b[1;32m[+] Successfully published to: https://huggingface.co/{repo_id}\x1b[0m")
    return True

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Publish Hyperion-LOB to Hugging Face Hub")
    parser.add_argument("--repo-id", type=str, help="Hugging Face repository ID (e.g. your_username/hyperion-lob)")
    parser.add_argument("--token", type=str, help="Hugging Face user write access token")
    parser.add_argument("--repo-type", type=str, default="model", choices=["model", "dataset"], help="model or dataset")
    args = parser.parse_args()

    prepare_benchmark_sample()

    if args.repo_id and args.token:
        publish_to_hf(args.repo_id, args.token, args.repo_type)
    else:
        print("\n" + "=" * 80)
        print("  HYPERION-LOB HUGGING FACE PUBLISH PACKAGE PREPARED")
        print("=" * 80)
        print("  All model card, metadata, and benchmark files are ready in: hf_publish/\n")
        print("  Options to publish to Hugging Face:")
        print("  -----------------------------------")
        print("  1. Automated CLI (requires HF token from https://huggingface.co/settings/tokens):")
        print("     pip install huggingface_hub")
        print("     python3 python/publish_to_huggingface.py --repo-id <username>/hyperion-lob --token <HF_TOKEN>")
        print()
        print("  2. Direct Web Upload (Zero CLI / Zero installations):")
        print("     a. Create a repo at https://huggingface.co/new (or https://huggingface.co/new-dataset)")
        print("     b. Drag & drop the files from `hf_publish/`:")
        print("        - hf_publish/README.md               (Complete Model & Dataset Card)")
        print("        - hf_publish/lob_model_weights.json  (Compiled Model Weights v1.3.0)")
        print("        - hf_publish/benchmark_data_sample.jsonl (Benchmark Data)")
        print("=" * 80 + "\n")
