use hyperion_quant::analytics::{QuantMetricsCalculator, TradeRecord};
use hyperion_quant::backtest::{BacktestConfig, BacktestEngine, SyntheticDataFeed};
use hyperion_quant::quant::stat_arb::PairsTradingConfig;

#[test]
fn test_backtest_engine_and_quant_metrics() {
    // 1. Test QuantMetricsCalculator standalone
    let initial_capital = 100_000.0;
    let equity_curve = vec![100_000.0, 101_000.0, 102_500.0, 101_800.0, 104_000.0];
    let trades = vec![
        TradeRecord { pnl: 1000.0, return_pct: 1.0, duration_seconds: 300.0, is_win: true },
        TradeRecord { pnl: 1500.0, return_pct: 1.5, duration_seconds: 400.0, is_win: true },
        TradeRecord { pnl: -700.0, return_pct: -0.7, duration_seconds: 250.0, is_win: false },
        TradeRecord { pnl: 2200.0, return_pct: 2.2, duration_seconds: 500.0, is_win: true },
    ];

    let metrics = QuantMetricsCalculator::compute(initial_capital, &equity_curve, &trades, 0.04, 252.0);
    assert_eq!(metrics.total_trades, 4);
    assert_eq!(metrics.win_rate_pct, 75.0);
    assert!(metrics.total_pnl > 3000.0);
    assert!(metrics.profit_factor > 6.0);
    assert!(metrics.max_drawdown_pct > 0.0);

    // 2. Test full BacktestEngine execution over synthetic cointegrated series
    let (bars_a, bars_b) = SyntheticDataFeed::generate_cointegrated_pair(
        500,    // 500 bars
        100.0,  // Base price A
        1.0,    // Beta = 1.0
        0.15,   // Reversion rate
        0.8,    // Noise
        42,     // Seed
    );

    let backtest_cfg = BacktestConfig {
        initial_capital: 100_000.0,
        fee_rate: 0.0002, // 2 bps fee
        slippage_bps: 0.0001,
        trade_size_usd: 20_000.0,
        risk_free_rate: 0.04,
    };

    let strat_cfg = PairsTradingConfig {
        hedge_ratio: 1.0,
        window_size: 30,
        z_entry_threshold: 1.8,
        z_exit_threshold: 0.2,
        z_stop_loss: 4.0,
    };

    let engine = BacktestEngine::new(backtest_cfg);
    let result = engine.run_pairs_backtest(&bars_a, &bars_b, strat_cfg);

    assert_eq!(result.equity_curve.len(), 500);
    assert!(!result.trades.is_empty());
    assert!(result.metrics.win_rate_pct >= 0.0);
}
