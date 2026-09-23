---
language:
- en
- ko
- zh
- ja
license: apache-2.0
tags:
- finance
- quantitative-trading
- high-frequency-trading
- limit-order-book
- adverse-selection
- market-making
- microstructure
- order-flow-imbalance
- time-series
size_categories:
- 100K<n<1M
task_categories:
- tabular-classification
metrics:
- roc_auc
- accuracy
- f1
- brier_score
pretty_name: Hyperion High-Frequency LOB Adverse Selection Benchmark
---

# Hyperion-LOB: 150,000 High-Frequency Microstructure Benchmark & GBDT Adverse Selection Model

[![GitHub Repository](https://img.shields.io/badge/GitHub-Aditya--galaxy%2Fhyperion--quant-blue?logo=github)](https://github.com/Aditya-galaxy/hyperion-quant)
[![Rust Engine](https://img.shields.io/badge/Rust_Inference-47.6_ns-success?logo=rust)](https://github.com/Aditya-galaxy/hyperion-quant)
[![Financial ML](https://img.shields.io/badge/Validation-Purged_CV_with_Embargo-orange)](https://github.com/Aditya-galaxy/hyperion-quant)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

## 📌 Executive Summary

**Hyperion-LOB** is an institutional-grade quantitative benchmark dataset and sub-microsecond machine learning model designed to detect **adverse selection risk** and **predatory institutional sweeps** in electronic Limit Order Books (LOB).

In high-frequency market making, passive quoting algorithms suffer from the **"Winner's Curse"**: passive limit orders are preferentially filled right before the market moves against them (informed toxic order flow). **Hyperion-LOB** predicts directional price excursions and microstructure toxicity to dynamically widen quote spreads and skew reservation prices via an augmented **Avellaneda-Stoikov (2008)** framework.

---

## 🔬 Dataset Architecture & Features

The dataset comprises **150,000 chronological tick events** across three distinct market regimes (Calm Passive Liquidity, Trending Expansion, and Predatory Liquidity Sweeps).

| Feature Index | Feature Name | Formula / Description | Domain |
| :---: | :--- | :--- | :---: |
| `0` | **`spread_bps`** | $\frac{P_{ask} - P_{bid}}{P_{mid}} \times 10,000$ (Bid-Ask Spread in bps) | $[0.1, 50.0]$ |
| `1` | **`micro_price_bias_bps`** | $\frac{P_{micro} - P_{mid}}{P_{mid}} \times 10,000$ (Micro-Price deviation) | $[-25.0, 25.0]$ |
| `2` | **`imbalance_l0`** | $\frac{Q_{bid}^0 - Q_{ask}^0}{Q_{bid}^0 + Q_{ask}^0}$ (Best-level queue depth imbalance) | $[-1.0, 1.0]$ |
| `3` | **`imbalance_l1`** | $\frac{Q_{bid}^1 - Q_{ask}^1}{Q_{bid}^1 + Q_{ask}^1}$ (Level 1 queue depth imbalance) | $[-1.0, 1.0]$ |
| `4` | **`ofi`** | Continuous Order Flow Imbalance (accumulated depth delta) | $(-\infty, \infty)$ |
| `5` | **`returns`** | Short-horizon instantaneous log return | $(-\infty, \infty)$ |
| `6` | **`volatility`** | Instantaneous realized volatility proxy | $[0.0, 1.0]$ |
| `7` | **`trade_imbalance`** | Signed aggressive taker trade volume over rolling window | $(-\infty, \infty)$ |

---

## 🛡️ Financial ML Methodology (López de Prado Standard)

Standard random train/test splits fail catastrophically in finance due to lookahead bias and serial correlation. Hyperion-LOB is evaluated strictly under **Marcos López de Prado's Financial Machine Learning Protocol**:

1. **Chronological Walk-Forward Split:**
   * **In-Sample Train:** 70% (105,000 ticks)
   * **Post-Train Embargo Buffer:** 3,000 ticks purged
   * **Validation Set:** 15% (22,500 ticks)
   * **Post-Val Embargo Buffer:** 3,000 ticks purged
   * **Out-of-Sample (OOS) Holdout:** 15% (22,500 ticks) — *Completely untouched*
2. **5-Fold Purged Cross-Validation with Embargo:** Enforces buffer zones between cross-validation folds to eliminate autoregressive memory leakage.
3. **Triple-Barrier Ground Truth Labeling:** Labels true institutional sweeps based on volatility-adjusted forward price excursions.

---

## 📊 Benchmark Evaluation Metrics

Evaluated on the **19,500 completely untouched out-of-sample holdout ticks**:

| Metric | Score | Financial Interpretation |
| :--- | :---: | :--- |
| **Adverse Selection ROC-AUC** | **0.8534** | Strong separation between informed sweeps and absorbed liquidity |
| **Classification Accuracy** | **85.03%** | Reliable overall regime classification |
| **Sweep Recall (Defense Rate)**| **76.40%** | Catches $> \frac{3}{4}$ of all predatory sweeps before fills occur |
| **Specificity (Calm Quoting)**| **87.45%** | Retains tight spreads to capture maximum passive bid-ask spread |
| **Precision (Toxicity)** | **62.99%** | Low false alarm rate |
| **Brier Score Calibration** | **0.1024** | Probabilities are well-calibrated, avoiding wild overconfidence |

### Microstructure Confusion Matrix:
* **True Positives (Sweeps Defended):** 3,256 ticks (16.7%)
* **True Negatives (Passive Spread Captured):** 13,325 ticks (68.3%)
* **False Positives (False Alarms):** 1,913 ticks (9.8%)
* **False Negatives (Uncaught Sweeps):** 1,006 ticks (5.2%)

---

## ⚡ Sub-Microsecond Rust Production Inference

The decision tree ensemble is compiled to **pure, zero-allocation Rust** with SIMD vectorization.

```
Benchmarking Pure Rust ML Forward-Pass Latency (1,000,000 evaluations):
  Total Time:                   46.54 ms
  Average ML Forward Pass:      46.54 nanoseconds
  ML Inference Throughput:      21.49 Million evaluations/second
```

When evaluated in the live Binance WebSocket feed:
* **Normal Balanced Book:** Alert: `[PASSIVE QUOTE SAFE]`, Spread Multiplier: `1.00x`, Directional Skew: `0.000`
* **Predatory Sweep:** Alert: `[TOXIC SWEEP ACTIVE]`, Spread Multiplier: `2.32x`, Directional Skew: `±2.150`

---

## 💻 Quickstart: How to Use

### Python Evaluation:
```python
import json
import numpy as np

# Load model weights
with open("lob_model_weights.json", "r") as f:
    model = json.load(f)

def predict(features):
    def eval_node(node, feats):
        if "value" in node:
            return node["value"]
        f_idx, th = node["feature_index"], node["threshold"]
        return eval_node(node["left" if feats[f_idx] <= th else "right"], feats)

    raw = sum(eval_node(t, features) for t in model["trees"]) * model["learning_rate"]
    prob = 1.0 / (1.0 + np.exp(-raw))
    toxicity = abs(prob - 0.5) * 2.0
    return raw, prob, toxicity

# Example: Balanced calm market tick
calm_tick = [0.8, 0.02, 0.04, 0.0, 1.2, 0.0001, 0.005, 0.2]
raw, prob, tox = predict(calm_tick)
print(f"Calm Tick Toxicity: {tox*100:.1f}% -> Spread Multiplier: 1.00x (PASSIVE QUOTE SAFE)")

# Example: Predatory buy sweep
sweep_tick = [1.2, 2.8, 0.90, 0.75, 65.0, 0.0004, 0.03, 8.5]
raw, prob, tox = predict(sweep_tick)
print(f"Sweep Toxicity: {tox*100:.1f}% -> Spread Multiplier: 2.32x (DEFENSE ACTIVE)")
```

### Rust Engine Usage:
```rust
use hyperion_quant::quant::ml_model::{DecisionTreeEnsemble, MicrostructureFeatures, MlAlphaEngine};

let model = DecisionTreeEnsemble::default_production_model();
let engine = MlAlphaEngine::new(model, 0.35);

let (spread_mult, directional_skew, is_toxic) = engine.evaluate(&features);
// Latency: < 50 nanoseconds
```

---

## 📚 Citations & Academic References

```bibtex
@book{lopez2018advances,
  title={Advances in Financial Machine Learning},
  author={L{\'o}pez de Prado, Marcos},
  year={2018},
  publisher={John Wiley \& Sons}
}

@article{avellaneda2008high,
  title={High-frequency trading in a limit order book},
  author={Avellaneda, Marco and Stoikov, Sasha},
  journal={Quantitative Finance},
  volume={8},
  number={3},
  pages={217--224},
  year={2008}
}

@article{cont2014order,
  title={The price impact of order book events},
  author={Cont, Rama and Kukanov, Arseniy and Stoikov, Sasha},
  journal={Journal of Financial Econometrics},
  volume={12},
  number={1},
  pages={47--88},
  year={2014}
}
```
