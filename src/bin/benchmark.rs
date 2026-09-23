use std::hint::black_box;
use std::time::Instant;

use hyperion_hft::core::rng::FastRng;
use hyperion_hft::core::types::{Nanoseconds, Price, Qty, Side};
use hyperion_hft::matching::engine::{MatchingEngine, OrderType};
use hyperion_hft::orderbook::lob::LimitOrderBook;
use hyperion_hft::risk::controller::{RiskController, RiskLimits};
use hyperion_hft::strategy::avellaneda_stoikov::{AsModelParams, AvellanedaStoikov};

fn main() {
    println!("\x1b[1;36m==================================================================\x1b[0m");
    println!("\x1b[1;32m      HYPERION HFT: LOW-LATENCY MICROBENCHMARK SUITE (PURE RUST)  \x1b[0m");
    println!("\x1b[1;36m==================================================================\x1b[0m");

    bench_orderbook();
    bench_risk_controller();
    bench_avellaneda_stoikov();
    bench_matching_engine();

    println!("\x1b[1;36m==================================================================\x1b[0m");
    println!("\x1b[1;32m                   BENCHMARK SUITE COMPLETE                       \x1b[0m");
    println!("\x1b[1;36m==================================================================\x1b[0m");
}

fn bench_orderbook() {
    println!("\n\x1b[1;33m[BENCH 1/4] Limit Order Book: Add & Cancel Order (100,000 ops)\x1b[0m");
    let mut book = LimitOrderBook::new(200_000);
    let iterations = 100_000;
    let mut latencies = Vec::with_capacity(iterations);

    for id in 1..=iterations as u64 {
        let price = Price::from_f64(100.0 + (id % 50) as f64 * 0.01);
        let qty = Qty::from_f64(1.0);

        let t0 = Instant::now();
        book.add_order(black_box(id), price, qty, Side::Bid, Nanoseconds::ZERO);
        book.cancel_order(black_box(id));
        latencies.push(t0.elapsed().as_nanos() as u64);
    }

    print_stats("OrderBook Add+Cancel", &mut latencies);
}

fn bench_risk_controller() {
    println!("\n\x1b[1;33m[BENCH 2/4] Pre-Trade Risk Controller: check_order (100,000 ops)\x1b[0m");
    let limits = RiskLimits::default();
    let mut risk = RiskController::new(limits);
    let bbo_bid = Some(Price::from_f64(100.0));
    let bbo_ask = Some(Price::from_f64(100.05));
    let iterations = 100_000;
    let mut latencies = Vec::with_capacity(iterations);

    for i in 1..=iterations as u64 {
        let ts = Nanoseconds::from_raw(i * 1_000_000);
        let t0 = Instant::now();
        let res = risk.check_order(
            black_box(Side::Bid),
            black_box(Price::from_f64(100.0)),
            black_box(Qty::from_f64(1.0)),
            black_box(0.0),
            bbo_bid,
            bbo_ask,
            ts,
        );
        let _ = black_box(res);
        latencies.push(t0.elapsed().as_nanos() as u64);
    }

    print_stats("Pre-Trade Risk Check", &mut latencies);
}

fn bench_avellaneda_stoikov() {
    println!("\n\x1b[1;33m[BENCH 3/4] Avellaneda-Stoikov Quote Calculation (100,000 ops)\x1b[0m");
    let params = AsModelParams::default();
    let strategy = AvellanedaStoikov::new(params, Qty::from_f64(1.0));
    let mid = Price::from_f64(100.0);
    let iterations = 100_000;
    let mut latencies = Vec::with_capacity(iterations);
    let mut inv = 0.0;

    for _ in 0..iterations {
        inv = (inv + 0.05) % 10.0;
        let t0 = Instant::now();
        let quotes = strategy.compute_quotes(mid, black_box(inv), black_box(0.002));
        black_box(quotes);
        latencies.push(t0.elapsed().as_nanos() as u64);
    }

    print_stats("Avellaneda-Stoikov Compute", &mut latencies);
}

fn bench_matching_engine() {
    println!("\n\x1b[1;33m[BENCH 4/4] Matching Engine Limit Order Ingestion (100,000 ops)\x1b[0m");
    let mut engine = MatchingEngine::new(200_000);
    let mut rng = FastRng::new(42);
    let iterations = 100_000;
    let mut latencies = Vec::with_capacity(iterations);

    for id in 1..=iterations as u64 {
        let price = Price::from_f64(95.0 + rng.gen_range_f64(0.0, 4.0));
        let qty = Qty::from_f64(1.0);

        let t0 = Instant::now();
        let res = engine.process_order(
            black_box(id),
            Side::Bid,
            price,
            qty,
            OrderType::Limit,
            Nanoseconds::ZERO,
        );
        black_box(res);
        latencies.push(t0.elapsed().as_nanos() as u64);
    }

    print_stats("Matching Engine Limit Ingest", &mut latencies);
}

fn print_stats(name: &str, latencies: &mut [u64]) {
    latencies.sort_unstable();
    let len = latencies.len();
    let p50 = latencies[len * 50 / 100];
    let p90 = latencies[len * 90 / 100];
    let p99 = latencies[len * 99 / 100];
    let p999 = latencies[len * 999 / 1000];
    let min = latencies[0];
    let max = latencies[len - 1];
    let avg = latencies.iter().sum::<u64>() / len as u64;

    println!("  Benchmark: \x1b[1;37m{}\x1b[0m", name);
    println!("  Avg:  {:>6} ns ({:.2} µs) | Min: {:>6} ns", avg, avg as f64 / 1000.0, min);
    println!("  p50:  \x1b[1;32m{:>6} ns ({:.2} µs)\x1b[0m", p50, p50 as f64 / 1000.0);
    println!("  p90:  {:>6} ns ({:.2} µs)", p90, p90 as f64 / 1000.0);
    println!("  p99:  \x1b[1;33m{:>6} ns ({:.2} µs)\x1b[0m", p99, p99 as f64 / 1000.0);
    println!("  p99.9:{:>6} ns ({:.2} µs) | Max: {:>6} ns ({:.2} µs)", p999, p999 as f64 / 1000.0, max, max as f64 / 1000.0);
}
