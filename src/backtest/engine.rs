use crate::analytics::{PerformanceMetrics, QuantMetricsCalculator, TradeRecord};
use crate::backtest::data_feed::HistoricalBar;
use crate::quant::stat_arb::{PairsTradingConfig, StatArbAction, StatArbEngine};

/// Configuration for backtest execution realism
#[derive(Debug, Clone, Copy)]
pub struct BacktestConfig {
    pub initial_capital: f64,
    /// Transaction fee rate in basis points (e.g., 0.0005 = 5 bps taker fee)
    pub fee_rate: f64,
    /// Bid-ask slippage in basis points per fill (e.g., 0.0002 = 2 bps)
    pub slippage_bps: f64,
    /// Position size in USD per pair trade
    pub trade_size_usd: f64,
    /// Annualized risk-free rate for Sharpe calculation (e.g. 0.04 = 4%)
    pub risk_free_rate: f64,
}

impl Default for BacktestConfig {
    fn default() -> Self {
        Self {
            initial_capital: 100_000.0,
            fee_rate: 0.0004,      // 4 bps
            slippage_bps: 0.0002,  // 2 bps
            trade_size_usd: 20_000.0,
            risk_free_rate: 0.04,
        }
    }
}

/// Active pairs trading position
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Position {
    Flat,
    /// Long Asset A, Short Asset B
    LongSpread,
    /// Short Asset A, Long Asset B
    ShortSpread,
}

/// Output of a completed backtest run
#[derive(Debug)]
pub struct BacktestResult {
    pub metrics: PerformanceMetrics,
    pub equity_curve: Vec<f64>,
    pub trades: Vec<TradeRecord>,
}

/// High-speed Event-Driven Backtesting Engine
pub struct BacktestEngine {
    config: BacktestConfig,
}

impl BacktestEngine {
    pub fn new(config: BacktestConfig) -> Self {
        Self { config }
    }

    /// Run full historical event-driven backtest for Statistical Arbitrage
    pub fn run_pairs_backtest(
        &self,
        bars_a: &[HistoricalBar],
        bars_b: &[HistoricalBar],
        strategy_config: PairsTradingConfig,
    ) -> BacktestResult {
        let n = bars_a.len().min(bars_b.len());
        let mut engine = StatArbEngine::new(strategy_config);

        let mut capital = self.config.initial_capital;
        let mut equity_curve = Vec::with_capacity(n);
        let mut trades = Vec::new();

        let mut current_pos = Position::Flat;
        let mut entry_price_a = 0.0;
        let mut entry_price_b = 0.0;
        let mut qty_a = 0.0;
        let mut qty_b = 0.0;
        let mut entry_time = 0.0;

        for i in 0..n {
            let bar_a = &bars_a[i];
            let bar_b = &bars_b[i];

            let (action, _state) = engine.update(bar_a.close, bar_b.close);
            let pa = bar_a.close.to_f64();
            let pb = bar_b.close.to_f64();
            let now_sec = bar_a.timestamp_ms as f64 / 1000.0;

            match action {
                StatArbAction::LongSpread if current_pos == Position::Flat => {
                    // Enter Long Spread: Long A, Short B
                    let effective_pa = pa * (1.0 + self.config.slippage_bps);
                    let effective_pb = pb * (1.0 - self.config.slippage_bps);

                    // Dollar allocation and hedge-ratio neutral sizing:
                    // S = P_a - beta * P_b -> Trade 1 unit of A vs beta units of B
                    qty_a = (self.config.trade_size_usd / 2.0) / effective_pa;
                    qty_b = qty_a * strategy_config.hedge_ratio;

                    entry_price_a = effective_pa;
                    entry_price_b = effective_pb;
                    entry_time = now_sec;
                    current_pos = Position::LongSpread;
                }
                StatArbAction::ShortSpread if current_pos == Position::Flat => {
                    // Enter Short Spread: Short A, Long B
                    let effective_pa = pa * (1.0 - self.config.slippage_bps);
                    let effective_pb = pb * (1.0 + self.config.slippage_bps);

                    qty_a = (self.config.trade_size_usd / 2.0) / effective_pa;
                    qty_b = qty_a * strategy_config.hedge_ratio;

                    entry_price_a = effective_pa;
                    entry_price_b = effective_pb;
                    entry_time = now_sec;
                    current_pos = Position::ShortSpread;
                }
                StatArbAction::ExitPositions if current_pos != Position::Flat => {
                    // Unwind positions
                    let (pnl_a, pnl_b) = if current_pos == Position::LongSpread {
                        let exit_pa = pa * (1.0 - self.config.slippage_bps);
                        let exit_pb = pb * (1.0 + self.config.slippage_bps);
                        let a_pnl = qty_a * (exit_pa - entry_price_a);
                        let b_pnl = qty_b * (entry_price_b - exit_pb);
                        (a_pnl, b_pnl)
                    } else {
                        let exit_pa = pa * (1.0 + self.config.slippage_bps);
                        let exit_pb = pb * (1.0 - self.config.slippage_bps);
                        let a_pnl = qty_a * (entry_price_a - exit_pa);
                        let b_pnl = qty_b * (exit_pb - entry_price_b);
                        (a_pnl, b_pnl)
                    };

                    let total_traded_notional = (qty_a * entry_price_a + qty_b * entry_price_b) 
                        + (qty_a * pa + qty_b * pb);
                    let roundtrip_fees = total_traded_notional * self.config.fee_rate;
                    let net_pnl = pnl_a + pnl_b - roundtrip_fees;

                    capital += net_pnl;
                    let ret_pct = (net_pnl / self.config.trade_size_usd) * 100.0;

                    trades.push(TradeRecord {
                        pnl: net_pnl,
                        return_pct: ret_pct,
                        duration_seconds: now_sec - entry_time,
                        is_win: net_pnl > 0.0,
                    });

                    current_pos = Position::Flat;
                }
                _ => {}
            }

            // Calculate instantaneous Mark-to-Market equity
            let mtm_pnl = match current_pos {
                Position::LongSpread => qty_a * (pa - entry_price_a) + qty_b * (entry_price_b - pb),
                Position::ShortSpread => qty_a * (entry_price_a - pa) + qty_b * (pb - entry_price_b),
                Position::Flat => 0.0,
            };

            equity_curve.push(capital + mtm_pnl);
        }

        // 24/7 continuous crypto markets have 365 * 1440 = 525,600 minutes per year
        let minutes_per_year = 365.0 * 1440.0;
        let metrics = QuantMetricsCalculator::compute(
            self.config.initial_capital,
            &equity_curve,
            &trades,
            self.config.risk_free_rate,
            minutes_per_year,
        );

        BacktestResult {
            metrics,
            equity_curve,
            trades,
        }
    }
}
