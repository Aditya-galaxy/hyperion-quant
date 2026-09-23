use hyperion_quant::core::types::Price;
use hyperion_quant::quant::basis_arb::{BasisArbConfig, BasisArbEngine, BasisSignal};

fn main() {
    println!("\x1b[1;36m================================================================================\x1b[0m");
    println!("\x1b[1;32m   HYPERION QUANT: CASH-AND-CARRY BASIS & FUNDING ARBITRAGE SIMULATOR         \x1b[0m");
    println!("\x1b[1;36m================================================================================\x1b[0m\n");

    let config = BasisArbConfig {
        min_apr_hurdle: 0.12,       // 12.0% APR minimum required carry yield
        min_basis_bps: 0.0004,      // 4 bps basis premium
        funding_period_hours: 8.0,  // Standard 8-hr funding payment cycle
        financing_cost_apr: 0.035,  // 3.5% borrowing/cost of capital
    };

    let engine = BasisArbEngine::new(config);

    // Simulated market regimes across a high-volatility 24-hour cycle
    let scenarios = [
        ("Bull Frenzy (Heavy Perp Premium)", 64_000.0, 64_160.0, 0.00065), // 71.1% APR
        ("Normal Bull Market", 64_200.0, 64_240.0, 0.00025),               // 27.3% APR
        ("Neutral Consolidation", 64_150.0, 64_155.0, 0.00004),            // 4.3% APR
        ("Bear Capitulation (Perp Discount)", 63_500.0, 63_200.0, -0.00085),// -93.0% APR
    ];

    println!("  Hurdle APR: \x1b[1m{:.1}%\x1b[0m | Financing Cost: \x1b[1m{:.1}%\x1b[0m\n", 
        config.min_apr_hurdle * 100.0, config.financing_cost_apr * 100.0);

    for (regime, spot_f, perp_f, funding_rate) in scenarios {
        let spot = Price::from_f64(spot_f);
        let perp = Price::from_f64(perp_f);
        let (signal, summary) = engine.evaluate(spot, perp, funding_rate);

        let signal_str = match signal {
            BasisSignal::OpenLongBasis => "\x1b[1;32m[OPEN LONG BASIS] (Long Spot + Short Perp)\x1b[0m",
            BasisSignal::OpenReverseBasis => "\x1b[1;35m[OPEN REVERSE BASIS] (Short Spot + Long Perp)\x1b[0m",
            BasisSignal::UnwindBasis => "\x1b[1;33m[UNWIND BASIS]\x1b[0m",
            BasisSignal::NoOpportunity => "\x1b[1;30m[NO TRADE]\x1b[0m",
        };

        println!("\x1b[1mRegime:\x1b[0m \x1b[1;36m{}\x1b[0m", regime);
        println!("  Spot Price:            ${:.2}", summary.spot_price);
        println!("  Perp Price:            ${:.2} (Basis: ${:+.2} / {:+.2} bps)", 
            summary.perp_price, summary.basis_absolute, summary.basis_percent * 10_000.0);
        println!("  8hr Funding Rate:      {:.4}%", summary.funding_rate_per_period * 100.0);
        println!("  Annualized Yield (APR):\x1b[1m {:+.2}%\x1b[0m", summary.annualized_funding_apr * 100.0);
        println!("  Net Carry Yield:       \x1b[1;32m{:+.2}%\x1b[0m", summary.net_carry_yield_apr * 100.0);
        println!("  Action Triggered:      {}\n", signal_str);
    }
    println!("\x1b[1;36m================================================================================\x1b[0m\n");
}
