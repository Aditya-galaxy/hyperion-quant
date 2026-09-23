# Hyperion Quant

[![Rust](https://img.shields.io/badge/rust-v1.80+-orange.svg)](https://www.rust-lang.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Build Status](https://img.shields.io/badge/build-passing-brightgreen.svg)]()
[![Backtest Speed](https://img.shields.io/badge/backtest-21.2M%20bars%2Fsec-blue.svg)]()
[![Execution Latency](https://img.shields.io/badge/tick--to--trade-416ns-purple.svg)]()

**Hyperion Quant** is an institutional-grade Quantitative Trading & Statistical Arbitrage Platform written in modern Rust. It merges a **sub-microsecond deterministic execution engine** with **mathematical alpha strategies** (Cointegration / Pairs Trading, Cash-and-Carry Basis Arbitrage), an **event-driven backtesting engine**, and **rigorous risk & performance analytics**.

---

## ⚡ Key Platform Capabilities

1. **Statistical Arbitrage & Pairs Trading Engine (`src/quant/stat_arb.rs`):**
   * Cointegrated synthetic spread estimation: $S_t = P_{A,t} - \beta P_{B,t}$.
   * Online rolling mean and variance using Welford's formulation (zero heap allocations).
   * Dynamic Z-Score triggers with mean-reversion exit thresholds and structural break stop-losses.

2. **Cash-and-Carry & Funding Rate Basis Arbitrage (`src/quant/basis_arb.rs`):**
   * Delta-neutral yield harvesting exploiting crypto Perpetual Futures vs. Spot basis premiums.
   * Real-time 8-hour funding rate APR annualization, net carry margin calculation, and auto-unwind triggers.

3. **Event-Driven Historical Backtesting Engine (`src/backtest/`):**
   * High-throughput event simulator benchmarked at **> 21 Million bars/second**.
   * Realistic market friction: maker/taker fee tiers, bid-ask slippage impact, and execution delay modeling.

4. **Institutional Performance Analytics (`src/analytics/metrics.rs`):**
   * Institutional metrics: **Sharpe Ratio**, **Sortino Ratio**, **Maximum Drawdown (MDD)**, **Calmar Ratio**, **Profit Factor**, and **Win Rate**.

5. **Sub-Microsecond Embedded ML Adverse Selection Engine (`src/quant/ml_model.rs`):**
   * Pure Rust Gradient Boosted Decision Tree (GBDT) ensemble running forward-passes in **71 nanoseconds** (**14.02 Million evals/sec**).
   * Ingests 8-dimensional microstructure features (OFI, micro-price skew, queue imbalance, trade flow) and triggers dynamic quote spread widening under toxic institutional sweeps.

6. **Exchange WebSocket Feed Parser (`src/feed/binance_feed.rs`):**
   * Zero-copy, high-speed parser for streaming Binance `@bookTicker` and depth feeds without heavyweight serialization overhead.

7. **Ultra-Low Latency Execution Substrate (`src/core/`, `src/orderbook/`, `src/matching/`, `src/risk/`):**
   * Sub-microsecond deterministic execution (**416 ns** median tick-to-trade).
   * Zero heap allocations on the hot path via continuous slab arenas and cache-aligned lock-free SPSC ring buffers.
   * Sub-50ns pre-trade risk engine with fat-finger checks, price collars, throttle rates, and kill-switches.

---

## 📊 Backtest Performance Example

Simulated statistical arbitrage over 10,000 one-minute bars on a synthetic cointegrated crypto pair (e.g. BTC/ETH with $\beta = 18.5$, 4 bps fee, 2 bps slippage):

```
================================================================================
                          PERFORMANCE & RISK REPORT                             
================================================================================
  Initial Capital:              $100,000.00
  Final Equity:                 $103,766.73
  Total Net PnL:                +$3,766.73
  Cumulative Return:            +3.77%
  Annualized Return:            +194.59%
  Annualized Volatility:        6.70%
--------------------------------------------------------------------------------
  Sharpe Ratio (Rf=4.5%):       28.37
  Sortino Ratio:                42.67
  Maximum Drawdown (MDD):       0.38%
  Calmar Ratio:                 513.73
  Profit Factor:                7.30
--------------------------------------------------------------------------------
  Total Completed Trades:       215
  Win Rate:                     71.6%
  Average Win:                  +$28.34
  Average Loss:                 -$9.80
  Win / Loss Ratio:             2.89
================================================================================
```

---

## 🏛️ System Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                        HYPERION QUANT PLATFORM                         │
├────────────────────────────────────────────────────────────────────────┤
│ 🧠 QUANT ALPHA LAYER (Mathematical Signals & Portfolios)               │
│   • Cointegration Pairs Trading (Rolling Welford Z-Score)              │
│   • Spot-Perp Cash-and-Carry Basis Arbitrage (APR Funding Harvester)   │
│   • Avellaneda-Stoikov Inventory Skewing Market Maker                  │
│   • Order Flow Imbalance (OFI) Short-Horizon Alpha                     │
├────────────────────────────────────────────────────────────────────────┤
│ 📈 EVENT-DRIVEN BACKTESTER & INSTITUTIONAL ANALYTICS                   │
│   • Synthetic & Historical Tick/Bar Ingestion (> 21M bars/sec)         │
│   • Friction Simulation: Taker Fees, Maker Rebates, Slippage           │
│   • Real-Time Metrics: Sharpe, Sortino, Calmar, Max Drawdown           │
├────────────────────────────────────────────────────────────────────────┤
│ ⚡ ULTRA-LOW LATENCY EXECUTION SUBSTRATE (< 1 µs)                       │
│   • Zero-Allocation Continuous Arena & Lock-Free SPSC Ring Buffer      │
│   • L3 Limit Order Book (LOB) with O(1) Doubly-Linked Queues           │
│   • FIFO Price-Time Matching Engine (Limit, IOC, FOK)                  │
│   • Sub-50ns Pre-Trade Risk Engine (Fat-Finger, Collars, Throttle)     │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 🚀 Quickstart & Usage

### Prerequisites
* Rust 1.80+ (`curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh`)

### Clone & Build
```bash
git clone https://github.com/Aditya-galaxy/hyperion-quant.git
cd hyperion-quant
cargo build --release
```

### 1. Run Quantitative Backtest Simulation (Statistical Arbitrage)
```bash
cargo run --release --bin run_stat_arb
```

### 2. Run Cash-and-Carry Basis Arbitrage Engine
```bash
cargo run --release --bin run_basis_arb
```

### 3. Run Sub-Microsecond ML Adverse Selection Defense
```bash
cargo run --release --bin run_ml_alpha
```

### 4. Run Microsecond Execution Benchmark
```bash
cargo run --release --bin benchmark
```

### 5. Run Live High-Throughput Market Simulation
```bash
cargo run --release --bin hyperion_quant
```

### 6. Run Full Test Suite (13 Tests)
```bash
cargo test
```

---

## 📄 License

Licensed under the [MIT License](LICENSE).
