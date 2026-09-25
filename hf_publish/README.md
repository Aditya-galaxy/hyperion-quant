---
license: mit
tags:
- finance
- limit-order-book
- adverse-selection
- market-making
- microstructure
- order-flow-imbalance
- synthetic
- simulated-data
size_categories:
- n<1K
task_categories:
- tabular-classification
metrics:
- roc_auc
- accuracy
- f1
- brier_score
pretty_name: Hyperion-LOB — simulated adverse-selection benchmark
---

# Hyperion-LOB: a simulated order-book benchmark and a hand-set adverse-selection rule

[![GitHub Repository](https://img.shields.io/badge/GitHub-Aditya--galaxy%2Fhyperion--quant-blue?logo=github)](https://github.com/Aditya-galaxy/hyperion-quant)
[![Data](https://img.shields.io/badge/Data-simulated-lightgrey)](#read-this-first)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/Aditya-galaxy/hyperion-quant/blob/main/LICENSE)

## Read this first

- **Every record is simulated.** The data comes from
  `generate_microstructure_data()` in
  [`python/train_test_pipeline.py`](https://github.com/Aditya-galaxy/hyperion-quant/blob/main/python/train_test_pipeline.py)
  (numpy, seed 42). No real market data was used to build, tune or evaluate
  anything on this page.
- **The label does not come from what the market did next.**
  `is_adverse_selection` is 1 when a noisy weighted sum of the *same tick's*
  features crosses a threshold. A model that scores well on it has recovered
  that formula; it has not shown it can predict a market.
- **The model is not trained.** It is three decision stumps with hand-set
  thresholds. Cross-validation scores this fixed rule on each fold; the training
  folds go unused.
- **The metrics measure agreement with the simulator's label.** They say nothing
  about real-market accuracy or profitability. There is no real-market
  evaluation yet.
- **Drop the `regime` column before using the sample.** It is the simulator's
  hidden state and gives the label away. In `benchmark_data_sample.jsonl`,
  every `toxic_sweep` row is labelled 1, and guessing the label from `regime`
  alone scores 89.1% accuracy, higher than the rule on this page.

What this *is* useful for: exercising the inference path, measuring latency,
and a reproducible baseline for the evaluation code. A real benchmark needs
recorded order books, with labels taken from how prices moved *after* each tick.

---

## The problem it is aimed at

A passive market maker is filled preferentially just before the price moves
against it — adverse selection, or the "winner's curse" of quoting. The rule
here flags ticks whose order-book state looks like informed, one-sided flow, so
an Avellaneda–Stoikov (2008) quoting engine can widen and skew its quotes.

## The simulated data

150,000 ticks. A regime follows a Markov chain (calm, trending, toxic sweep).
Given the regime, each tick's features are drawn from simple distributions: they
are **sampled, not computed from order books**. The only serial dependence is
regime persistence.

| Index | Feature | Intended meaning |
| :---: | :--- | :--- |
| `0` | `spread_bps` | Bid-ask spread in basis points |
| `1` | `micro_price_bias_bps` | Micro-price minus mid, in bps |
| `2` | `imbalance_l0` | Best-level depth imbalance, [-1, 1] |
| `3` | `imbalance_l1` | Level-1 depth imbalance, [-1, 1] |
| `4` | `ofi` | Order-flow imbalance |
| `5` | `returns` | Short-horizon return |
| `6` | `volatility` | Realized-volatility proxy |
| `7` | `trade_imbalance` | Signed aggressive trade volume |

The label, exactly as generated:

```python
latent_toxicity = (0.35 * abs(micro_price_bias) + 0.025 * abs(ofi)
                   + 0.40 * abs(imbalance_l0) + 0.15 * abs(trade_imbalance)
                   + normal(0, 0.20))
is_adverse_selection = latent_toxicity > 0.85
```

This repository ships `benchmark_data_sample.jsonl`: 1,000 records from a
related generator in `python/publish_to_huggingface.py`. The full 150,000
regenerate deterministically from the code above.

## Evaluation protocol

A chronological split with embargo gaps: train 105,000 ticks → 3,000 embargo →
validation 19,500 → 3,000 embargo → **out-of-sample 19,500**. There is also a
5-fold purged cross-validation on the training block (2% embargo). Both techniques
come from López de Prado (2018). Here they guard against regime persistence
leaking across the split. Because the rule is not fitted, the split mostly changes
which simulated ticks each score is computed on.

## Results: agreement with the simulator's label

Out-of-sample, 19,500 ticks. Re-run with `python3 python/train_test_pipeline.py`;
these figures reproduce exactly.

| Metric | Score |
| :--- | :---: |
| ROC-AUC | 0.8534 |
| Accuracy | 85.03% |
| Recall (toxic ticks flagged) | 76.40% |
| Specificity (calm ticks left alone) | 87.45% |
| Precision | 62.99% |
| Brier score | 0.1024 |
| 5-fold CV accuracy | 85.85% ± 0.58% |

Confusion matrix: 3,256 true positives, 13,325 true negatives, 1,913 false
positives, 1,006 false negatives.

## Latency (measured on real hardware)

The same rule, compiled into the Rust engine
(`DecisionTreeEnsemble::default_production_model()`), over 1,000,000 evaluations:

```
cargo run --release --bin run_ml_alpha      # Apple M1, rustc 1.89.0

  Total Time:                   40.94 ms for 1,000,000 iterations
  Average ML Forward Pass:      40.94 nanoseconds
  ML Inference Throughput:      24.43 Million evals/sec
```

A previous run recorded 46.54 ns. Expect variation between runs and machines.

The same binary then runs three **hard-coded order-book snapshots** through the
quoting engine. These are illustrative outputs, not measurements from a live
feed:

| Snapshot | Flag | Spread multiplier | Skew |
| :--- | :--- | :---: | :---: |
| Balanced book (10 × 10) | normal | 1.00× | 0.000 |
| Bid sweep (85 × 2) | toxic | 2.32× | +2.150 |
| Ask wall (1.5 × 120) | toxic | 2.32× | −2.150 |

---

## Quickstart

### Python

```python
import json
import numpy as np

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

calm_tick = [0.8, 0.02, 0.04, 0.0, 1.2, 0.0001, 0.005, 0.2]
sweep_tick = [1.2, 2.8, 0.90, 0.75, 65.0, 0.0004, 0.03, 8.5]
for name, tick in [("calm", calm_tick), ("sweep", sweep_tick)]:
    _, _, tox = predict(tick)
    print(f"{name}: toxicity {tox * 100:.1f}%")
```

### Rust

```rust
use hyperion_quant::quant::ml_model::{DecisionTreeEnsemble, MicrostructureFeatures, MlAlphaEngine};

let model = DecisionTreeEnsemble::default_production_model();
let engine = MlAlphaEngine::new(model, 0.35);

let (spread_mult, directional_skew, is_toxic) = engine.evaluate(&features);
```

---

## References

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
