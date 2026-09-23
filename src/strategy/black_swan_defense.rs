/// Severity of market liquidity stress
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ShockRegime {
    Normal,
    ElevatedRisk,
    FlashCrashWarning,
    EmergencyCircuitBreaker,
}

/// Real-time Black-Swan & Catastrophic Liquidity Defense Controller
#[derive(Debug, Clone)]
pub struct BlackSwanDefense {
    /// Rolling baseline depth (in base currency, e.g. BTC)
    pub baseline_depth: f64,
    /// Threshold ratio below which liquidity collapse is flagged (e.g. 0.20 = 80% drop)
    pub liquidity_collapse_threshold: f64,
    /// Volatility spike threshold ratio vs baseline (e.g. 3.0x normal vol)
    pub vol_spike_threshold: f64,
    /// Rolling baseline volatility
    pub baseline_vol: f64,
    /// Alpha decay factor for rolling exponential moving average
    pub ema_alpha: f64,
}

impl Default for BlackSwanDefense {
    fn default() -> Self {
        Self {
            baseline_depth: 10.0,
            liquidity_collapse_threshold: 0.25, // 75% depth drop
            vol_spike_threshold: 2.5,          // 2.5x volatility spike
            baseline_vol: 0.015,
            ema_alpha: 0.05,
        }
    }
}

impl BlackSwanDefense {
    pub fn new(
        initial_depth: f64,
        initial_vol: f64,
        liquidity_collapse_threshold: f64,
        vol_spike_threshold: f64,
    ) -> Self {
        Self {
            baseline_depth: initial_depth.max(1.0),
            liquidity_collapse_threshold,
            vol_spike_threshold,
            baseline_vol: initial_vol.max(0.001),
            ema_alpha: 0.05,
        }
    }

    /// Evaluates current real-time order book state and returns:
    /// (ShockRegime, SpreadMultiplier, ShouldPullQuotes)
    pub fn evaluate_shock(
        &mut self,
        current_bbo_depth: f64,
        current_volatility: f64,
        micro_spread_bps: f64,
    ) -> (ShockRegime, f64, bool) {
        // Update baseline EMA during normal periods
        if current_bbo_depth > 0.0 {
            self.baseline_depth = (1.0 - self.ema_alpha) * self.baseline_depth + self.ema_alpha * current_bbo_depth;
        }
        if current_volatility > 0.0 {
            self.baseline_vol = (1.0 - self.ema_alpha) * self.baseline_vol + self.ema_alpha * current_volatility;
        }

        let depth_ratio = current_bbo_depth / self.baseline_depth.max(0.001);
        let vol_ratio = current_volatility / self.baseline_vol.max(0.0001);

        // Emergency Condition 1: Total liquidity air-pocket (depth drops below 15% of baseline)
        // or spread blows out by > 15 bps
        if depth_ratio < 0.15 || (micro_spread_bps > 15.0 && vol_ratio > 3.5) {
            return (
                ShockRegime::EmergencyCircuitBreaker,
                4.0,  // 4x spread widening or pull
                true, // Pull all passive limit orders immediately
            );
        }

        // Warning Condition 2: Flash crash cascade (depth < 25% or vol > 2.5x)
        if depth_ratio < self.liquidity_collapse_threshold || vol_ratio >= self.vol_spike_threshold {
            let mult = 2.0 + (self.vol_spike_threshold.min(vol_ratio) - 1.0) * 0.8;
            return (ShockRegime::FlashCrashWarning, mult.min(3.5), false);
        }

        // Elevated Risk: Slight liquidity thinning or widening
        if depth_ratio < 0.50 || vol_ratio > 1.6 {
            return (ShockRegime::ElevatedRisk, 1.4, false);
        }

        (ShockRegime::Normal, 1.0, false)
    }
}
