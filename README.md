# Hyperion HFT

[![Rust](https://img.shields.io/badge/rust-v1.80+-orange.svg)](https://www.rust-lang.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Build Status](https://img.shields.io/badge/build-passing-brightgreen.svg)]()
[![Throughput](https://img.shields.io/badge/throughput-1.64M%20ticks%2Fsec-blue.svg)]()
[![Median Latency](https://img.shields.io/badge/tick--to--trade-416ns-purple.svg)]()

**Hyperion HFT** is an ultra-low-latency, zero-heap-allocation High-Frequency Trading (HFT) and quantitative market-making engine written in modern Rust. It is engineered for sub-microsecond deterministic execution, minimal tail jitter, and institutional-grade risk controls.

---

## ⚡ Performance Highlights

Benchmarked on Apple Silicon (M-series / ARM64) in `--release` mode (`opt-level = 3`, `lto = "fat"`):

* **Peak Throughput:** **1,643,464 ticks/second** (1.64 Million events/sec)
* **Median Tick-to-Trade Latency:** **416 nanoseconds** ($0.42\ \mu\text{s}$)
* **p99 Tick-to-Trade Latency:** **500 nanoseconds** ($0.50\ \mu\text{s}$)
* **Limit Order Book (Add + Cancel):** **125 ns** median ($0.12\ \mu\text{s}$)
* **Pre-Trade Risk Engine (6 validation checks):** **83 ns** median ($0.08\ \mu\text{s}$)
* **Avellaneda-Stoikov Quote Math:** **41 ns** median ($0.04\ \mu\text{s}$)
* **Matching Engine Limit Ingestion:** **208 ns** median ($0.21\ \mu\text{s}$)

---

## 🏛️ System Architecture

```
                  ┌────────────────────────────────────────┐
                  │          Market Data Stream            │
                  │       (Order Book Depth & Trades)      │
                  └───────────────────┬────────────────────┘
                                      │ Zero-copy ingress
                                      ▼
                  ┌────────────────────────────────────────┐
                  │       Lock-Free SPSC Ring Buffer       │
                  │    - 64-byte CPU cache-line aligned    │
                  │    - Atomic Acquire/Release barriers   │
                  └───────────────────┬────────────────────┘
                                      │ O(1) tick dispatch
                                      ▼
                  ┌────────────────────────────────────────┐
                  │      Level 3 Limit Order Book (LOB)    │
                  │    - Pre-allocated continuous Arena<T> │
                  │    - O(1) doubly-linked FIFO queues    │
                  │    - Real-time Micro-Price & OFI Alpha │
                  └───────────────────┬────────────────────┘
                                      │ Microsecond state
                                      ▼
                  ┌────────────────────────────────────────┐
                  │       Quantitative Strategy Engine     │
                  │    - Avellaneda-Stoikov Market Maker   │
                  │    - Dynamic inventory skewing         │
                  │    - Adverse selection protection      │
                  └───────────────────┬────────────────────┘
                                      │ Proposed quotes
                                      ▼
                  ┌────────────────────────────────────────┐
                  │     Pre-Trade Risk Engine (< 50ns)     │
                  │    - Fat-finger size & notional check  │
                  │    - Price collar validation           │
                  │    - Sliding window rate throttle      │
                  │    - Hardware/software kill switch     │
                  └───────────────────┬────────────────────┘
                                      │ Approved orders
                                      ▼
                  ┌────────────────────────────────────────┐
                  │        Order Management System (OMS)   │
                  │    - Continuous FIFO Matching Engine   │
                  │    - Position & VWAP accounting        │
                  │    - Realized/Unrealized PnL tracker   │
                  │    - Dead Man's Switch / Heartbeat     │
                  └────────────────────────────────────────┘
```

---

## 🛠️ Low-Latency Design Decisions

1. **Zero Heap Allocation on Critical Path:**
   All order nodes, price levels, and message buffers are pre-allocated in continuous memory slabs (`Arena<T>`). No system calls (`malloc`/`new`) occur during live quoting.
2. **Fixed-Point Precision (`Price` & `Qty`):**
   Fixed-point 64-bit integer arithmetic with $10^8$ multiplier (8 decimal places) avoids IEEE 754 floating-point nondeterminism and CPU float unit stalls.
3. **Lock-Free Concurrency with False Sharing Elimination:**
   Cross-thread communication uses single-producer single-consumer (SPSC) ring buffers with 64-byte alignment (`#[repr(align(64))]`) to isolate producer and consumer heads onto independent CPU cache lines.
4. **Queue Priority Preservation:**
   Quotes are maintained at the top of the book and only replaced when the optimal price shifts by at least one exchange tick, preserving FIFO queue priority.

---

## 🚀 Quick Start

### Prerequisites
* Rust 1.75+ (`rustc` and `cargo`)

```bash
# Clone the repository
git clone https://github.com/your-username/hyperion_hft.git
cd hyperion_hft

# Run automated unit and integration tests (100% passing)
cargo test

# Run component-level sub-microsecond microbenchmarks
cargo run --release --bin benchmark

# Run the live high-throughput market making simulation with terminal telemetry
cargo run --release --bin hyperion_hft
```

---

## 🧪 Test Suite

Hyperion HFT includes an automated test suite verifying:
* **Order Book Correctness:** BBO computation, order cancellations, capacity limits, and volume-weighted micro-price calculations (`tests/orderbook_tests.rs`).
* **Matching Engine Execution:** FIFO time priority matching, partial fills, IOC and FOK order semantics (`tests/matching_tests.rs`).
* **Pre-Trade Risk Engine:** Rejection of fat-finger quantities, notional limits, price collar breaches, rate limiting, and kill-switch trips (`tests/risk_tests.rs`).
* **Strategy Inventory Skewing:** Verification that long inventory lowers reservation price and short inventory raises reservation price (`tests/strategy_tests.rs`).

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
