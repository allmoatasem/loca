# Loca — Model Guide

## MLX vs GGUF — which to use?

| | MLX | GGUF |
|---|---|---|
| **Platform** | macOS Apple Silicon only | macOS (Intel + ARM), Linux, Windows |
| **Speed on M-series** | Faster (~15–30% better prompt processing) | Slightly slower |
| **File format** | Directory of `.safetensors` + `config.json` | Single `.gguf` file |
| **Source** | `mlx-community/` on Hugging Face | `bartowski/`, `lmstudio-community/` on HF |
| **RAM usage** | Similar to GGUF at same quantisation | Similar |

**Rule of thumb:** On an M-series Mac, use MLX for large models where you want maximum speed. Use GGUF if you want portability or if no MLX conversion exists for a model.

---

## Recommended models

### General / default

These handle most conversations, vision, and file analysis.

| Model | Format | Size | Notes |
|---|---|---|---|
| `mlx-community/Qwen2.5-72B-Instruct-4bit` | MLX | ~40 GB | Best quality, needs 64+ GB RAM |
| `mlx-community/Qwen2.5-32B-Instruct-4bit` | MLX | ~18 GB | Good balance for 32–36 GB RAM |
| `mlx-community/Qwen2.5-14B-Instruct-4bit` | MLX | ~8 GB | Fast, runs on 16 GB RAM |
| `bartowski/Qwen2.5-7B-Instruct-GGUF` | GGUF | ~4 GB | Cross-platform, minimal RAM |

### Code

| Model | Format | Size | Notes |
|---|---|---|---|
| `mlx-community/Qwen2.5-Coder-32B-Instruct-4bit` | MLX | ~18 GB | Best code model at 32B |
| `mlx-community/Qwen2.5-Coder-7B-Instruct-4bit` | MLX | ~4 GB | Lighter code model |
| `bartowski/Qwen2.5-Coder-7B-Instruct-GGUF` | GGUF | ~4 GB | Cross-platform |

### Reasoning / Thinking

| Model | Format | Size | Notes |
|---|---|---|---|
| `mlx-community/QwQ-32B-4bit` | MLX | ~18 GB | Deep reasoning, slower |
| `mlx-community/DeepSeek-R1-Distill-Qwen-14B-4bit` | MLX | ~8 GB | R1 distill, fast |
| `bartowski/QwQ-32B-GGUF` | GGUF | ~18 GB | Cross-platform |

### Write / Creative

| Model | Format | Size | Notes |
|---|---|---|---|
| `mlx-community/Mistral-Small-3.1-24B-Instruct-2503-4bit` | MLX | ~13 GB | Strong writing quality |
| `bartowski/Mistral-7B-Instruct-v0.3-GGUF` | GGUF | ~4 GB | Lightweight, cross-platform |

---

## Downloading models

### Via Loca settings panel (recommended)
1. Open Loca → gear icon → Models tab
2. Enter the Hugging Face `repo_id` (e.g. `bartowski/Qwen2.5-7B-Instruct-GGUF`)
3. For GGUF: enter the specific filename (e.g. `Qwen2.5-7B-Instruct-Q4_K_M.gguf`)
4. For MLX: leave filename blank — the full directory will be downloaded
5. Click Download and watch the progress bar

### Via command line
```bash
# GGUF (single file)
pip install huggingface_hub
huggingface-cli download bartowski/Qwen2.5-7B-Instruct-GGUF \
    Qwen2.5-7B-Instruct-Q4_K_M.gguf \
    --local-dir ~/loca_models/gguf

# MLX (full directory)
huggingface-cli download mlx-community/Qwen2.5-7B-Instruct-4bit \
    --local-dir ~/loca_models/mlx/Qwen2.5-7B-Instruct-4bit
```

---

## Choosing quantisation (GGUF)

| Quantisation | Quality | RAM | Recommended for |
|---|---|---|---|
| `Q8_0` | Near-lossless | ~8 GB for 7B | If you have RAM to spare |
| `Q4_K_M` | Good | ~4 GB for 7B | Best quality/size balance — **default choice** |
| `Q3_K_M` | Acceptable | ~3 GB for 7B | Tight RAM budget |
| `Q2_K` | Noticeable loss | ~2.5 GB for 7B | Avoid unless necessary |

MLX models use fixed quantisation per repo (usually 4-bit or 8-bit) — no choice needed.

---

## Adding a model to config.yaml

The `config.yaml` models section is now for display names only — routing uses whichever model is loaded in the inference backend. You don't need to edit config.yaml to use a model; just download it and load it from the settings panel.

The `routing` section controls which system prompt mode handles each conversation type (general, code, reason, write). These are independent of which model is loaded.
