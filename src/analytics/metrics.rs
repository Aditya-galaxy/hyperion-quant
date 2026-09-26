/// A completed trade record for performance accounting
#[derive(Debug, Clone, Copy)]
pub struct TradeRecord {
    pub pnl: f64,
    pub return_pct: f64,
    pub duration_seconds: f64,
    pub is_win: bool,
}

/// Performance metrics of a backtest
#[derive(Debug, Clone, Copy)]
pub struct PerformanceMetrics {
    pub total_trades: usize,
    pub win_rate_pct: f64,
    pub total_pnl: f64,
    pub total_return_pct: f64,
    pub annualized_return_pct: f64,
    pub annualized_volatility_pct: f64,
    pub sharpe_ratio: f64,
    pub sortino_ratio: f64,
    pub max_drawdown_pct: f64,
    pub calmar_ratio: f64,
    pub profit_factor: f64,
    pub avg_win: f64,
    pub avg_loss: f64,
    pub win_loss_ratio: f64,
}

/// Computes performance metrics from an equity curve and trade list
pub struct QuantMetricsCalculator;

impl QuantMetricsCalculator {
    /// Calculate Sharpe, Sortino, Calmar, Max Drawdown, and trade statistics
    pub fn compute(
        initial_capital: f64,
        equity_curve: &[f64],
        trades: &[TradeRecord],
        risk_free_rate_annual: f64,
        periods_per_year: f64, // e.g., 252.0 for daily, 252.0 * 390.0 for 1-minute bars
    ) -> PerformanceMetrics {
        if equity_curve.is_empty() {
            return PerformanceMetrics {
                total_trades: 0,
                win_rate_pct: 0.0,
                total_pnl: 0.0,
                total_return_pct: 0.0,
                annualized_return_pct: 0.0,
                annualized_volatility_pct: 0.0,
                sharpe_ratio: 0.0,
                sortino_ratio: 0.0,
                max_drawdown_pct: 0.0,
                calmar_ratio: 0.0,
                profit_factor: 0.0,
                avg_win: 0.0,
                avg_loss: 0.0,
                win_loss_ratio: 0.0,
            };
        }

        // 1. Returns and Equity Curve Analysis
        let mut returns = Vec::with_capacity(equity_curve.len().saturating_sub(1));
        for i in 1..equity_curve.len() {
            let prev = equity_curve[i - 1];
            if prev > 0.0 {
                returns.push((equity_curve[i] - prev) / prev);
            }
        }

        let n = returns.len() as f64;
        let mean_return = if n > 0.0 {
            returns.iter().sum::<f64>() / n
        } else {
            0.0
        };

        // Standard Deviation
        let variance = if n > 1.0 {
            returns
                .iter()
                .map(|r| (r - mean_return).powi(2))
                .sum::<f64>()
                / (n - 1.0)
        } else {
            0.0
        };
        let std_dev = variance.sqrt();

        // Downside deviation for Sortino Ratio (only negative returns)
        let downside_variance = if n > 1.0 {
            returns
                .iter()
                .filter(|&&r| r < 0.0)
                .map(|r| r.powi(2))
                .sum::<f64>()
                / (n - 1.0)
        } else {
            0.0
        };
        let downside_dev = downside_variance.sqrt();

        // Annualization using actual periods_per_year
        let annual_factor = periods_per_year.sqrt();
        let annualized_return = mean_return * periods_per_year;
        let annualized_vol = std_dev * annual_factor;

        let sharpe_ratio = if annualized_vol > 1e-8 {
            (annualized_return - risk_free_rate_annual) / annualized_vol
        } else {
            0.0
        };

        let sortino_ratio = if downside_dev > 1e-8 {
            (annualized_return - risk_free_rate_annual) / (downside_dev * annual_factor)
        } else {
            0.0
        };

        // 2. Maximum Drawdown Calculation
        let mut peak = initial_capital;
        let mut max_drawdown_pct = 0.0;

        for &val in equity_curve {
            if val > peak {
                peak = val;
            }
            if peak > 0.0 {
                let dd = (peak - val) / peak;
                if dd > max_drawdown_pct {
                    max_drawdown_pct = dd;
                }
            }
        }

        let calmar_ratio = if max_drawdown_pct > 1e-6 {
            annualized_return / max_drawdown_pct
        } else {
            0.0
        };

        // 3. Trade Statistics
        let total_trades = trades.len();
        let mut wins = 0;
        let mut gross_profit = 0.0;
        let mut gross_loss = 0.0;
        let mut total_pnl = 0.0;

        for t in trades {
            total_pnl += t.pnl;
            if t.pnl > 0.0 {
                wins += 1;
                gross_profit += t.pnl;
            } else {
                gross_loss += t.pnl.abs();
            }
        }

        let win_rate = if total_trades > 0 {
            (wins as f64 / total_trades as f64) * 100.0
        } else {
            0.0
        };

        let losses = total_trades - wins;
        let avg_win = if wins > 0 { gross_profit / wins as f64 } else { 0.0 };
        let avg_loss = if losses > 0 { gross_loss / losses as f64 } else { 0.0 };
        let win_loss_ratio = if avg_loss > 1e-8 { avg_win / avg_loss } else { 0.0 };

        let profit_factor = if gross_loss > 1e-8 {
            gross_profit / gross_loss
        } else if gross_profit > 0.0 {
            f64::INFINITY
        } else {
            0.0
        };

        let final_equity = equity_curve.last().copied().unwrap_or(initial_capital);
        let total_return_pct = if initial_capital > 0.0 {
            ((final_equity - initial_capital) / initial_capital) * 100.0
        } else {
            0.0
        };

        PerformanceMetrics {
            total_trades,
            win_rate_pct: win_rate,
            total_pnl,
            total_return_pct,
            annualized_return_pct: annualized_return * 100.0,
            annualized_volatility_pct: annualized_vol * 100.0,
            sharpe_ratio,
            sortino_ratio,
            max_drawdown_pct: max_drawdown_pct * 100.0,
            calmar_ratio,
            profit_factor,
            avg_win,
            avg_loss,
            win_loss_ratio,
        }
    }
}
