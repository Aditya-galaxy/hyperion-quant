use crate::core::types::Price;

/// Configuration for Statistical Arbitrage (Pairs Trading)
#[derive(Debug, Clone, Copy)]
pub struct PairsTradingConfig {
    /// Hedge ratio beta: Spread = Price_A - beta * Price_B
    pub hedge_ratio: f64,
    /// Rolling lookback window size for online z-score estimation
    pub window_size: usize,
    /// Z-score threshold to enter mean-reversion trade (e.g. 2.0 std deviations)
    pub z_entry_threshold: f64,
    /// Z-score threshold to exit trade (e.g. 0.2 std deviations)
    pub z_exit_threshold: f64,
    /// Z-score stop-loss threshold (e.g. 3.5 std deviations, indicates regime shift / break of cointegration)
    pub z_stop_loss: f64,
}

impl Default for PairsTradingConfig {
    fn default() -> Self {
        Self {
            hedge_ratio: 1.0,
            window_size: 60,
            z_entry_threshold: 2.0,
            z_exit_threshold: 0.25,
            z_stop_loss: 3.5,
        }
    }
}

/// Action signal output by the Stat Arb Engine
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StatArbAction {
    Hold,
    /// Spread is too high (+Z): Short Asset A, Long Asset B
    ShortSpread,
    /// Spread is too low (-Z): Long Asset A, Short Asset B
    LongSpread,
    /// Mean reversion achieved or stop loss triggered: Close all open pair positions
    ExitPositions,
}

/// Real-time state of the pairs spread
#[derive(Debug, Clone, Copy)]
pub struct PairSpreadState {
    pub current_spread: f64,
    pub rolling_mean: f64,
    pub rolling_std: f64,
    pub z_score: f64,
    pub count: usize,
}

/// High-performance, zero-allocation online Statistical Arbitrage engine.
/// Computes rolling mean and standard deviation via a circular buffer and Welford's formulation.
#[derive(Debug)]
pub struct StatArbEngine {
    config: PairsTradingConfig,
    history: Vec<f64>,
    head_idx: usize,
    count: usize,
    running_sum: f64,
    running_sq_sum: f64,
    in_position: bool,
}

impl StatArbEngine {
    pub fn new(config: PairsTradingConfig) -> Self {
        let capacity = config.window_size.max(10);
        Self {
            config,
            history: vec![0.0; capacity],
            head_idx: 0,
            count: 0,
            running_sum: 0.0,
            running_sq_sum: 0.0,
            in_position: false,
        }
    }

    /// Ingest the latest prices for Asset A and Asset B and compute trading action
    pub fn update(&mut self, price_a: Price, price_b: Price) -> (StatArbAction, PairSpreadState) {
        let pa = price_a.to_f64();
        let pb = price_b.to_f64();

        // Calculate synthetic pair spread: S_t = P_a - beta * P_b
        let spread = pa - self.config.hedge_ratio * pb;

        let capacity = self.history.len();
        if self.count >= capacity {
            // Subtract the evicted oldest element
            let old_val = self.history[self.head_idx];
            self.running_sum -= old_val;
            self.running_sq_sum -= old_val * old_val;
        } else {
            self.count += 1;
        }

        // Insert newest spread
        self.history[self.head_idx] = spread;
        self.running_sum += spread;
        self.running_sq_sum += spread * spread;
        self.head_idx = (self.head_idx + 1) % capacity;

        // Compute rolling mean and sample variance
        let n = self.count as f64;
        let mean = self.running_sum / n;
        
        let variance = if self.count > 1 {
            let var_num = self.running_sq_sum - (self.running_sum * self.running_sum) / n;
            (var_num / (n - 1.0)).max(0.0)
        } else {
            0.0
        };

        let std_dev = variance.sqrt();
        let z_score = if std_dev > 1e-8 {
            (spread - mean) / std_dev
        } else {
            0.0
        };

        let state = PairSpreadState {
            current_spread: spread,
            rolling_mean: mean,
            rolling_std: std_dev,
            z_score,
            count: self.count,
        };

        // If not enough warmup periods, hold
        if self.count < 10 {
            return (StatArbAction::Hold, state);
        }

        // Determine action based on z-score and active position
        let action = if self.in_position {
            if z_score >= self.config.z_stop_loss || z_score <= -self.config.z_stop_loss {
                // Stop loss triggered (cointegration breakdown)
                self.in_position = false;
                StatArbAction::ExitPositions
            } else if z_score.abs() <= self.config.z_exit_threshold {
                // Reverted to mean
                self.in_position = false;
                StatArbAction::ExitPositions
            } else {
                StatArbAction::Hold
            }
        } else {
            if z_score >= self.config.z_entry_threshold {
                // Spread elevated: Short spread (Short A, Long B)
                self.in_position = true;
                StatArbAction::ShortSpread
            } else if z_score <= -self.config.z_entry_threshold {
                // Spread depressed: Long spread (Long A, Short B)
                self.in_position = true;
                StatArbAction::LongSpread
            } else {
                StatArbAction::Hold
            }
        };

        (action, state)
    }

    pub fn config(&self) -> &PairsTradingConfig {
        &self.config
    }
}
