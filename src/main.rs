use std::time::Instant;

use hyperion_hft::core::rng::FastRng;
use hyperion_hft::core::types::{Nanoseconds, Price, Qty, Side};
use hyperion_hft::matching::engine::{MatchingEngine, OrderExecutionResult, OrderType};
use hyperion_hft::oms::order_manager::OrderManager;
use hyperion_hft::risk::controller::{RiskController, RiskLimits};
use hyperion_hft::strategy::avellaneda_stoikov::{AsModelParams, AvellanedaStoikov};
use hyperion_hft::strategy::ofi_alpha::OfiAlpha;

fn main() {
    println!("\x1b[1;36m==================================================================\x1b[0m");
    println!("\x1b[1;32m       HYPERION HFT: ULTRA-LOW-LATENCY TRADING ENGINE (RUST)      \x1b[0m");
    println!("\x1b[1;36m==================================================================\x1b[0m");

    // 1. Initialize Matching Engine (Capacity: 100,000 orders)
    let mut engine = MatchingEngine::new(100_000);

    // 2. Initialize Avellaneda-Stoikov Market Maker Strategy
    let as_params = AsModelParams {
        gamma: 0.10,      // Risk aversion
        sigma: 0.01,      // Volatility
        kappa: 120.0,     // High order arrival intensity (tight liquid spread ~1-2 ticks)
        time_horizon: 1.0,
        tick_size: Price::from_f64(0.01),
    };
    let strategy = AvellanedaStoikov::new(as_params, Qty::from_f64(1.0));
    let mut ofi_alpha = OfiAlpha::new(0.0005, 0.25, 0.90);

    // 3. Initialize Pre-Trade Risk Controller (< 50ns latency)
    let risk_limits = RiskLimits {
        max_order_qty: Qty::from_f64(5.0),
        max_order_notional: 100_000.0,
        max_position_qty: 20.0,
        max_price_collar_pct: 0.03,
        max_orders_per_window: 500,
        rate_limit_window_ns: 100_000_000, // 100ms
    };
    let mut risk = RiskController::new(risk_limits);

    // 4. Initialize Order Management System (OMS)
    let mut oms = OrderManager::new(500_000_000); // 500ms heartbeat timeout

    // Seed initial book around $100.00 mid price
    let mut rng = FastRng::new(123456789);
    let base_price = 100.0;
    let mut current_order_id = 1u64;

    println!("\x1b[1;33m[INIT] Pre-allocating zero-heap memory arenas...\x1b[0m");
    println!("\x1b[1;33m[INIT] Initializing L3 Limit Order Book depth...\x1b[0m");

    // Populate initial resting book: 10 bid levels, 10 ask levels
    for i in 1..=10 {
        let bid_p = Price::from_f64(base_price - (i as f64) * 0.02);
        let ask_p = Price::from_f64(base_price + (i as f64) * 0.02);
        let qty = Qty::from_f64(rng.gen_range_f64(2.0, 10.0));

        current_order_id += 1;
        engine.process_order(current_order_id, Side::Bid, bid_p, qty, OrderType::Limit, Nanoseconds::ZERO);

        current_order_id += 1;
        engine.process_order(current_order_id, Side::Ask, ask_p, qty, OrderType::Limit, Nanoseconds::ZERO);
    }

    println!("\x1b[1;32m[INIT] Order book populated with BBO:\x1b[0m");
    if let (Some(b), Some(a)) = (engine.book.best_bid(), engine.book.best_ask()) {
        println!("       Best Bid: ${:.4} | Best Ask: ${:.4} | Spread: ${:.4}", b.to_f64(), a.to_f64(), (a - b).to_f64());
    }

    println!("\n\x1b[1;37m>>> Starting High-Throughput Live Trading Loop (10,000 Event Simulation)...\x1b[0m");

    let total_events = 10_000;
    let mut latencies_ns: Vec<u64> = Vec::with_capacity(total_events);
    let mut our_active_bid_id: Option<u64> = None;
    let mut our_active_ask_id: Option<u64> = None;
    let mut our_bid_price: Option<Price> = None;
    let mut our_ask_price: Option<Price> = None;

    let sim_start = Instant::now();

    for tick in 1..=total_events {
        let now_ns = Nanoseconds::from_raw((tick as u64) * 10_000); // 10µs simulated tick intervals
        oms.heartbeat(now_ns);

        // --- Step 1: External Market Event (Random Taker Flow or Maker Liquidity Shift) ---
        let event_type = rng.gen_range_u32(0, 100);
        if event_type < 40 {
            // Noise trader submitting market order (Taker sweep)
            let taker_side = if rng.gen_bool(0.5) { Side::Bid } else { Side::Ask };
            let taker_qty = Qty::from_f64(rng.gen_range_f64(0.2, 1.5));
            let crossing_price = if taker_side.is_bid() {
                Price::from_f64(base_price + 2.0)
            } else {
                Price::from_f64(base_price - 2.0)
            };

            current_order_id += 1;
            let result = engine.process_order(
                current_order_id,
                taker_side,
                crossing_price,
                taker_qty,
                OrderType::ImmediateOrCancel,
                now_ns,
            );

            // Check if our market maker quotes got filled
            if let OrderExecutionResult::Filled { ref reports } | OrderExecutionResult::PartiallyFilled { ref reports, .. } = result {
                for rep in reports {
                    if Some(rep.maker_order_id) == our_active_bid_id {
                        oms.on_fill(rep, rep.maker_order_id, Side::Bid);
                        if rep.matched_qty >= strategy.default_quote_qty {
                            our_active_bid_id = None;
                            our_bid_price = None;
                        }
                    } else if Some(rep.maker_order_id) == our_active_ask_id {
                        oms.on_fill(rep, rep.maker_order_id, Side::Ask);
                        if rep.matched_qty >= strategy.default_quote_qty {
                            our_active_ask_id = None;
                            our_ask_price = None;
                        }
                    }
                }
            }
        } else if event_type < 70 {
            // External maker adding liquidity
            let side = if rng.gen_bool(0.5) { Side::Bid } else { Side::Ask };
            let offset = rng.gen_range_f64(0.01, 0.10);
            let mid = engine.book.mid_price().unwrap_or(Price::from_f64(base_price)).to_f64();
            let price = Price::from_f64(if side.is_bid() { mid - offset } else { mid + offset });
            let qty = Qty::from_f64(rng.gen_range_f64(0.5, 3.0));

            current_order_id += 1;
            engine.process_order(current_order_id, side, price, qty, OrderType::Limit, now_ns);
        }

        // --- Step 2: High-Frequency Strategy Decision (Measured Latency Hot Path) ---
        let tick_t0 = Instant::now();

        // 2a. Extract book microstructure
        let mid = match engine.book.mid_price() {
            Some(m) => m,
            None => continue,
        };
        let alpha_skew = ofi_alpha.update(&engine.book);

        // 2b. Compute Avellaneda-Stoikov Quotes
        let current_inventory = oms.position.inventory;
        let mut quotes = strategy.compute_quotes(mid, current_inventory, alpha_skew);

        // Clamp quotes to remain passive makers within the spread
        if let (Some(bb), Some(ba)) = (engine.book.best_bid(), engine.book.best_ask()) {
            let tick = 0.01;
            let clamped_bid = quotes.bid_price.to_f64().min(ba.to_f64() - tick);
            let clamped_ask = quotes.ask_price.to_f64().max(bb.to_f64() + tick);
            quotes.bid_price = Price::from_f64(clamped_bid);
            quotes.ask_price = Price::from_f64(clamped_ask);
        }

        // 2c. Pre-Trade Risk Check: Bid Quote
        let bid_risk = risk.check_order(
            Side::Bid,
            quotes.bid_price,
            quotes.bid_qty,
            current_inventory,
            engine.book.best_bid(),
            engine.book.best_ask(),
            now_ns,
        );

        // 2d. Pre-Trade Risk Check: Ask Quote
        let ask_risk = risk.check_order(
            Side::Ask,
            quotes.ask_price,
            quotes.ask_qty,
            current_inventory,
            engine.book.best_bid(),
            engine.book.best_ask(),
            now_ns,
        );

        // 2e. Update / Re-quote active orders only when price shifts (Preserving FIFO Queue Priority)
        if bid_risk.is_ok() {
            if our_active_bid_id.is_none() || Some(quotes.bid_price) != our_bid_price {
                if let Some(old_id) = our_active_bid_id.take() {
                    engine.cancel_order(old_id);
                    oms.on_order_cancelled(old_id);
                }
                current_order_id += 1;
                let new_id = current_order_id;
                engine.process_order(new_id, Side::Bid, quotes.bid_price, quotes.bid_qty, OrderType::Limit, now_ns);
                oms.on_order_submitted(new_id, Side::Bid, quotes.bid_price, quotes.bid_qty, now_ns);
                our_active_bid_id = Some(new_id);
                our_bid_price = Some(quotes.bid_price);
            }
        }

        if ask_risk.is_ok() {
            if our_active_ask_id.is_none() || Some(quotes.ask_price) != our_ask_price {
                if let Some(old_id) = our_active_ask_id.take() {
                    engine.cancel_order(old_id);
                    oms.on_order_cancelled(old_id);
                }
                current_order_id += 1;
                let new_id = current_order_id;
                engine.process_order(new_id, Side::Ask, quotes.ask_price, quotes.ask_qty, OrderType::Limit, now_ns);
                oms.on_order_submitted(new_id, Side::Ask, quotes.ask_price, quotes.ask_qty, now_ns);
                our_active_ask_id = Some(new_id);
                our_ask_price = Some(quotes.ask_price);
            }
        }

        // Measure tick-to-trade elapsed time
        let elapsed_ns = tick_t0.elapsed().as_nanos() as u64;
        latencies_ns.push(elapsed_ns);

        // Periodically print telemetry dashboard
        if tick % 2_500 == 0 {
            print_telemetry(tick, total_events, &engine, &oms, &latencies_ns);
        }
    }

    let total_wall_time = sim_start.elapsed();

    // Final Report
    println!("\n\x1b[1;36m==================================================================\x1b[0m");
    println!("\x1b[1;32m                  FINAL PERFORMANCE SUMMARY                      \x1b[0m");
    println!("\x1b[1;36m==================================================================\x1b[0m");

    latencies_ns.sort_unstable();
    let count = latencies_ns.len();
    let p50 = latencies_ns[count * 50 / 100];
    let p90 = latencies_ns[count * 90 / 100];
    let p99 = latencies_ns[count * 99 / 100];
    let p999 = latencies_ns[count * 999 / 1000];
    let min = latencies_ns[0];
    let max = latencies_ns[count - 1];

    let mid = engine.book.mid_price().unwrap_or(Price::from_f64(base_price));
    let total_pnl = oms.position.total_pnl(mid);

    println!("Total Ticks Processed:   \x1b[1;37m{}\x1b[0m", total_events);
    println!("Elapsed Simulation Time: {:?}", total_wall_time);
    println!("Throughput:              {:.2} ticks/second", (total_events as f64) / total_wall_time.as_secs_f64());
    println!("\n\x1b[1;33m--- Latency Profiling (Tick-to-Trade) ---\x1b[0m");
    println!("  Min Latency:    {:>7} ns ({:.2} µs)", min, min as f64 / 1000.0);
    println!("  p50 (Median):   \x1b[1;32m{:>7} ns ({:.2} µs)\x1b[0m", p50, p50 as f64 / 1000.0);
    println!("  p90 Latency:    {:>7} ns ({:.2} µs)", p90, p90 as f64 / 1000.0);
    println!("  p99 Latency:    \x1b[1;33m{:>7} ns ({:.2} µs)\x1b[0m", p99, p99 as f64 / 1000.0);
    println!("  p99.9 Latency:  {:>7} ns ({:.2} µs)", p999, p999 as f64 / 1000.0);
    println!("  Max Latency:    {:>7} ns ({:.2} µs)", max, max as f64 / 1000.0);

    println!("\n\x1b[1;33m--- Strategy & Risk Telemetry ---\x1b[0m");
    println!("  Fills Executed:        {}", oms.position.total_fills_count);
    println!("  Total Volume Traded:   {:.2} units", oms.position.total_volume_traded);
    println!("  Final Inventory (q):   {:.2} units", oms.position.inventory);
    println!("  Realized PnL:          ${:.2}", oms.position.realized_pnl);
    println!("  Unrealized PnL:        ${:.2}", oms.position.unrealized_pnl(mid));
    println!("  Total PnL:             ${:.2}", total_pnl);
    println!("  Kill Switch Triggered: {}", if risk.kill_switch_active { "\x1b[1;31mYES\x1b[0m" } else { "\x1b[1;32mNO\x1b[0m" });
    println!("\x1b[1;36m==================================================================\x1b[0m");
}

fn print_telemetry(
    tick: usize,
    total: usize,
    engine: &MatchingEngine,
    oms: &OrderManager,
    latencies: &[u64],
) {
    let recent = &latencies[latencies.len().saturating_sub(1000)..];
    let avg_ns = if recent.is_empty() { 0 } else { recent.iter().sum::<u64>() / recent.len() as u64 };

    let best_bid = engine.book.best_bid().map(|p| p.to_f64()).unwrap_or(0.0);
    let best_ask = engine.book.best_ask().map(|p| p.to_f64()).unwrap_or(0.0);
    let mid = engine.book.mid_price().map(|p| p.to_f64()).unwrap_or(0.0);
    let micro = engine.book.micro_price().map(|p| p.to_f64()).unwrap_or(0.0);
    let spread = best_ask - best_bid;

    let pnl = oms.position.total_pnl(Price::from_f64(mid));
    let pnl_str = if pnl >= 0.0 {
        format!("\x1b[1;32m+${:.2}\x1b[0m", pnl)
    } else {
        format!("\x1b[1;31m-${:.2}\x1b[0m", pnl.abs())
    };

    println!(
        "[{:>5}/{}] BBO: ${:.2}/${:.2} | Spread: ${:.3} | Mid: ${:.2} | Micro: ${:.2} | Inv: {:>5.2} | PnL: {} | Latency: {:>4}ns ({:.2}µs)",
        tick,
        total,
        best_bid,
        best_ask,
        spread,
        mid,
        micro,
        oms.position.inventory,
        pnl_str,
        avg_ns,
        avg_ns as f64 / 1000.0
    );
}
