use crate::core::types::Price;

/// Represents an OHLCV historical bar or tick summary
#[derive(Debug, Clone, Copy)]
pub struct HistoricalBar {
    pub timestamp_ms: u64,
    pub open: Price,
    pub high: Price,
    pub low: Price,
    pub close: Price,
    pub volume: f64,
}

/// Generator for synthetic cointegrated asset pairs and trending markets
pub struct SyntheticDataFeed;

impl SyntheticDataFeed {
    /// Generates cointegrated price paths for Asset A and Asset B
    /// S_t = P_a - beta * P_b follows an Ornstein-Uhlenbeck (mean-reverting) process.
    pub fn generate_cointegrated_pair(
        num_bars: usize,
        base_price_a: f64,
        hedge_ratio: f64,
        theta_reversion: f64, // Mean reversion speed
        sigma_noise: f64,     // Spread volatility
        seed: u64,
    ) -> (Vec<HistoricalBar>, Vec<HistoricalBar>) {
        let mut bars_a = Vec::with_capacity(num_bars);
        let mut bars_b = Vec::with_capacity(num_bars);

        let mut rng_state = seed;
        let mut next_rand = || -> f64 {
            rng_state ^= rng_state << 13;
            rng_state ^= rng_state >> 7;
            rng_state ^= rng_state << 17;
            // Map u64 to standard normal approx (Box-Muller or Central Limit sum)
            let u1 = ((rng_state & 0xFFFF_FFFF) as f64 + 1.0) / 4294967297.0;
            rng_state ^= rng_state << 13;
            rng_state ^= rng_state >> 7;
            rng_state ^= rng_state << 17;
            let u2 = ((rng_state & 0xFFFF_FFFF) as f64 + 1.0) / 4294967297.0;
            (-2.0 * u1.ln()).sqrt() * (2.0 * std::f64::consts::PI * u2).cos()
        };

        let mut p_b = base_price_a / hedge_ratio;
        let mut spread = 0.0;
        let mean_spread = 0.0;

        for i in 0..num_bars {
            // Random walk for Asset B
            let drift_b = 0.0001;
            let shock_b = 0.005 * next_rand();
            p_b *= 1.0 + drift_b + shock_b;
            if p_b < 1.0 { p_b = 1.0; }

            // Mean-reverting Ornstein-Uhlenbeck process for spread: dS = theta * (mu - S) * dt + sigma * dW
            let d_spread = theta_reversion * (mean_spread - spread) + sigma_noise * next_rand();
            spread += d_spread;

            // Cointegrated relationship: Asset A trades as beta * Asset B + S_t
            let p_a = (hedge_ratio * p_b + spread).max(0.1);

            let ts = (i as u64) * 60_000; // 1-minute steps

            bars_a.push(HistoricalBar {
                timestamp_ms: ts,
                open: Price::from_f64(p_a),
                high: Price::from_f64(p_a * 1.001),
                low: Price::from_f64(p_a * 0.999),
                close: Price::from_f64(p_a),
                volume: 100.0,
            });

            bars_b.push(HistoricalBar {
                timestamp_ms: ts,
                open: Price::from_f64(p_b),
                high: Price::from_f64(p_b * 1.001),
                low: Price::from_f64(p_b * 0.999),
                close: Price::from_f64(p_b),
                volume: 100.0,
            });
        }

        (bars_a, bars_b)
    }
}
