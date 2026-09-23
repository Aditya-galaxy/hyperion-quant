use std::hint::black_box;
use std::time::Instant;

use hyperion_quant::core::types::Price;
use hyperion_quant::feed::binance_feed::BinanceFeedParser;
use hyperion_quant::quant::ml_model::{DecisionTreeEnsemble, MicrostructureFeatures, MlAlphaEngine};
use hyperion_quant::strategy::avellaneda_stoikov::{AsModelParams, AvellanedaStoikov};

fn main() {
    println!("\x1b[1;36m================================================================================\x1b[0m");
    println!("\x1b[1;32m   HYPERION QUANT: SUB-MICROSECOND EMBEDDED ML ADVERSE SELECTION ENGINE      \x1b[0m");
    println!("\x1b[1;36m================================================================================\x1b[0m\n");

    println!("\x1b[1;33m[1/3] Loading Pre-Trained Embedded Gradient Boosted Decision Tree Model...\x1b[0m");
    let model = DecisionTreeEnsemble::default_production_model();
    let ml_engine = MlAlphaEngine::new(model, 0.35); // 35% toxicity hurdle
    println!("  Model: \x1b[1mHyperionLOBAdverseSelectionModel v1.0.0\x1b[0m (3 compiled trees)\n");

    println!("\x1b[1;33m[2/3] Benchmarking Pure Rust ML Forward-Pass Latency (1,000,000 evaluations)...\x1b[0m");
    let features = MicrostructureFeatures {
        spread_bps: 1.5,
        micro_price_bias_bps: 1.8,
        imbalance_l0: 0.75,
        imbalance_l1: 0.60,
        ofi: 35.0,
        returns: 0.0003,
        volatility: 0.03,
        trade_imbalance: 4.0,
    };

    let warmup = 10_000;
    for _ in 0..warmup {
        black_box(ml_engine.evaluate(black_box(&features)));
    }

    let iterations = 1_000_000;
    let start = Instant::now();
    for _ in 0..iterations {
        black_box(ml_engine.evaluate(black_box(&features)));
    }
    let elapsed = start.elapsed();
    let avg_ns = elapsed.as_nanos() as f64 / iterations as f64;

    println!("  Total Time:                   \x1b[1;32m{:.2?}\x1b[0m for {} iterations", elapsed, iterations);
    println!("  \x1b[1;35mAverage ML Forward Pass:\x1b[0m      \x1b[1;32m{:.2} nanoseconds\x1b[0m", avg_ns);
    println!("  \x1b[1;35mML Inference Throughput:\x1b[0m      \x1b[1;32m{:.2} Million evals/sec\x1b[0m\n", 
        iterations as f64 / elapsed.as_secs_f64() / 1_000_000.0);

    println!("\x1b[1;33m[3/3] Live Order Book Ingestion & Dynamic Avellaneda-Stoikov Quote Defense...\x1b[0m");
    let as_params = AsModelParams {
        gamma: 0.1,
        sigma: 0.5,
        kappa: 1.5,
        time_horizon: 1.0,
        tick_size: Price::from_f64(0.01),
    };
    let as_model = AvellanedaStoikov::new(as_params, hyperion_quant::core::types::Qty::from_f64(1.0));

    // Simulate incoming streaming Binance book updates
    let raw_packets = [
        ("Normal Balanced Book", r#"{"u":1001,"s":"BTCUSDT","b":"65000.0","B":"10.0","a":"65001.0","A":"10.0"}"#, 0.0, 0.0),
        ("Institutional Bid Sweep", r#"{"u":1002,"s":"BTCUSDT","b":"65001.0","B":"85.0","a":"65001.5","A":"2.0"}"#, 45.0, 8.0),
        ("Toxic Dump / Ask Wall", r#"{"u":1003,"s":"BTCUSDT","b":"64998.0","B":"1.5","a":"64999.0","A":"120.0"}"#, -70.0, -12.0),
    ];

    for (desc, json_msg, ofi, trade_imb) in raw_packets {
        let ticker = BinanceFeedParser::parse_book_ticker(json_msg).unwrap();
        let mid = (ticker.best_bid_price.to_f64() + ticker.best_ask_price.to_f64()) / 2.0;

        // Extract features
        let current_features = MicrostructureFeatures {
            spread_bps: (ticker.best_ask_price.to_f64() - ticker.best_bid_price.to_f64()) / mid * 10_000.0,
            micro_price_bias_bps: (ofi / 100.0) * 2.0,
            imbalance_l0: if ofi > 0.0 { 0.85 } else if ofi < 0.0 { -0.85 } else { 0.0 },
            imbalance_l1: 0.5,
            ofi,
            returns: 0.0001,
            volatility: 0.02,
            trade_imbalance: trade_imb,
        };

        let (spread_mult, directional_skew, is_toxic) = ml_engine.evaluate(&current_features);
        
        // Base AS quotes
        let base_quote = as_model.compute_quotes(Price::from_f64(mid), 0.0, 0.0);
        let base_half_spread = (base_quote.ask_price.to_f64() - base_quote.bid_price.to_f64()) / 2.0;

        // ML Defense adjusted quotes: widen spread under toxicity and skew reservation price
        let defense_half_spread = base_half_spread * spread_mult;
        let reservation = mid + directional_skew * 0.5;
        let defended_bid = reservation - defense_half_spread;
        let defended_ask = reservation + defense_half_spread;

        println!("  \x1b[1mEvent:\x1b[0m \x1b[1;36m{}\x1b[0m", desc);
        println!("    Raw BBO:            Bid ${:.2} x {} | Ask ${:.2} x {}", 
            ticker.best_bid_price.to_f64(), ticker.best_bid_qty.to_f64(),
            ticker.best_ask_price.to_f64(), ticker.best_ask_qty.to_f64());
        println!("    ML Toxicity Alert:  {}", 
            if is_toxic { "\x1b[1;31m[TOXIC FLOW DETECTED - DEFENSE ACTIVE]\x1b[0m" } else { "\x1b[1;32m[NORMAL LIQUIDITY]\x1b[0m" });
        println!("    Spread Multiplier:  \x1b[1m{:.2}x\x1b[0m", spread_mult);
        println!("    Directional Skew:   {:+.3}", directional_skew);
        println!("    Base Quoted Spread: ${:.2}", base_half_spread * 2.0);
        println!("    Defended Quotes:    \x1b[1;32mBid: ${:.2}\x1b[0m | \x1b[1;31mAsk: ${:.2}\x1b[0m (Spread: ${:.2})\n", 
            defended_bid, defended_ask, defended_ask - defended_bid);
    }

    println!("\x1b[1;36m================================================================================\x1b[0m\n");
}
