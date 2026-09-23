use hyperion_quant::backtest::{BacktestConfig, BacktestEngine, SyntheticDataFeed};
use hyperion_quant::quant::stat_arb::PairsTradingConfig;

fn main() {
    println!("\x1b[1;36m================================================================================\x1b[0m");
    println!("\x1b[1;32m  HYPERION QUANT: STATISTICAL ARBITRAGE & COINTEGRATION BACKTESTING SIMULATOR  \x1b[0m");
    println!("\x1b[1;36m================================================================================\x1b[0m\n");

    println!("\x1b[1;33m[1/3] Generating Cointegrated Asset Pair Series (e.g. BTC/ETH Synthetic Spread)...\x1b[0m");
    let num_bars = 10_000;
    let (bars_a, bars_b) = SyntheticDataFeed::generate_cointegrated_pair(
        num_bars,
        65_000.0, // Asset A baseline (e.g. BTC)
        18.5,     // Hedge ratio Beta (e.g. 18.5x ETH = BTC)
        0.08,     // Ornstein-Uhlenbeck mean-reversion speed
        120.0,    // Spread volatility noise ($120 standard deviation on $65,000 BTC spread)
        1337,     // Seed
    );
    println!("  Generated \x1b[1m10,000\x1b[0m continuous 1-minute historical bars for Pair (A, B).\n");

    println!("\x1b[1;33m[2/3] Executing Event-Driven Backtesting Engine with Realistic Market Friction...\x1b[0m");
    let backtest_config = BacktestConfig {
        initial_capital: 100_000.0, // $100k starting capital
        fee_rate: 0.0004,           // 4 bps taker fee per side
        slippage_bps: 0.0002,       // 2 bps market impact slippage
        trade_size_usd: 25_000.0,   // $25k allocated per spread entry
        risk_free_rate: 0.045,      // 4.5% risk-free rate
    };

    let strategy_config = PairsTradingConfig {
        hedge_ratio: 18.5,
        window_size: 60,            // 60-bar rolling lookback
        z_entry_threshold: 2.0,     // Enter when spread > 2.0 std deviations
        z_exit_threshold: 0.25,     // Exit when spread reverts to 0.25 std dev
        z_stop_loss: 3.8,           // Stop-loss at 3.8 std deviations (structural break)
    };

    let start_time = std::time::Instant::now();
    let engine = BacktestEngine::new(backtest_config);
    let result = engine.run_pairs_backtest(&bars_a, &bars_b, strategy_config);
    let elapsed = start_time.elapsed();

    let m = &result.metrics;
    let final_equity = result.equity_curve.last().copied().unwrap_or(100_000.0);

    println!("  Backtest completed in: \x1b[1;32m{:.2?}\x1b[0m ({:.0} bars/sec)\n", 
        elapsed, num_bars as f64 / elapsed.as_secs_f64());

    println!("\x1b[1;36m================================================================================\x1b[0m");
    println!("\x1b[1;32m                          PERFORMANCE & RISK REPORT                             \x1b[0m");
    println!("\x1b[1;36m================================================================================\x1b[0m");
    println!("  Initial Capital:              \x1b[1m${:.2}\x1b[0m", backtest_config.initial_capital);
    println!("  Final Equity:                 \x1b[1;32m${:.2}\x1b[0m", final_equity);
    println!("  Total Net PnL:                \x1b[1;32m+${:.2}\x1b[0m", m.total_pnl);
    println!("  Cumulative Return:            \x1b[1;32m{:.2}%\x1b[0m", m.total_return_pct);
    println!("  Annualized Return:            \x1b[1;32m{:.2}%\x1b[0m", m.annualized_return_pct);
    println!("  Annualized Volatility:        \x1b[1;33m{:.2}%\x1b[0m", m.annualized_volatility_pct);
    println!("--------------------------------------------------------------------------------");
    println!("  \x1b[1;35mSharpe Ratio (Rf=4.5%):\x1b[0m       \x1b[1;32m{:.2}\x1b[0m", m.sharpe_ratio);
    println!("  \x1b[1;35mSortino Ratio:\x1b[0m                \x1b[1;32m{:.2}\x1b[0m", m.sortino_ratio);
    println!("  \x1b[1;31mMaximum Drawdown (MDD):\x1b[0m       \x1b[1;31m{:.2}%\x1b[0m", m.max_drawdown_pct);
    println!("  \x1b[1;35mCalmar Ratio:\x1b[0m                 \x1b[1;32m{:.2}\x1b[0m", m.calmar_ratio);
    println!("  \x1b[1;35mProfit Factor:\x1b[0m                \x1b[1;32m{:.2}\x1b[0m", m.profit_factor);
    println!("--------------------------------------------------------------------------------");
    println!("  Total Completed Trades:       \x1b[1m{}\x1b[0m", m.total_trades);
    println!("  Win Rate:                     \x1b[1;32m{:.1}%\x1b[0m", m.win_rate_pct);
    println!("  Average Win:                  \x1b[1;32m+${:.2}\x1b[0m", m.avg_win);
    println!("  Average Loss:                 \x1b[1;31m-${:.2}\x1b[0m", m.avg_loss);
    println!("  Win / Loss Ratio:             \x1b[1m{:.2}\x1b[0m", m.win_loss_ratio);
    println!("\x1b[1;36m================================================================================\x1b[0m\n");
}
