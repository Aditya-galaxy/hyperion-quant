use crate::core::types::{Nanoseconds, Price, Qty, Side};

/// Pre-Trade Risk Configuration Parameters.
#[derive(Clone, Debug)]
pub struct RiskLimits {
    /// Maximum quantity for a single order
    pub max_order_qty: Qty,
    /// Maximum notional value (Price * Qty) for a single order in dollars
    pub max_order_notional: f64,
    /// Maximum absolute inventory position in base units
    pub max_position_qty: f64,
    /// Price collar percentage (e.g., 0.02 = 2.0% away from BBO)
    pub max_price_collar_pct: f64,
    /// Maximum allowed orders within the rate limit window
    pub max_orders_per_window: usize,
    /// Window duration in nanoseconds (e.g., 100ms = 100_000_000 ns)
    pub rate_limit_window_ns: u64,
}

impl Default for RiskLimits {
    fn default() -> Self {
        Self {
            max_order_qty: Qty::from_f64(10.0),
            max_order_notional: 500_000.0,
            max_position_qty: 25.0,
            max_price_collar_pct: 0.02,
            max_orders_per_window: 100,
            rate_limit_window_ns: 100_000_000, // 100 ms
        }
    }
}

/// Pre-Trade Risk Check Rejection Reasons.
#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum RiskRejection {
    KillSwitchActive,
    OrderQtyExceedsLimit,
    OrderNotionalExceedsLimit,
    PositionLimitExceeded,
    PriceCollarViolation,
    RateLimitExceeded,
}

/// Ultra-low-latency Pre-Trade Risk Controller.
/// Evaluates proposed orders in under 50 nanoseconds on the hot path.
pub struct RiskController {
    pub limits: RiskLimits,
    pub kill_switch_active: bool,
    order_timestamps: Vec<u64>,
}

impl RiskController {
    pub fn new(limits: RiskLimits) -> Self {
        let max_window = limits.max_orders_per_window;
        Self {
            limits,
            kill_switch_active: false,
            order_timestamps: Vec::with_capacity(max_window + 16),
        }
    }

    /// Primary Pre-Trade Risk Evaluation.
    /// Returns Ok(()) if the order passes all risk checks, or Err(RiskRejection).
    #[inline(always)]
    pub fn check_order(
        &mut self,
        side: Side,
        price: Price,
        qty: Qty,
        current_inventory: f64,
        best_bid: Option<Price>,
        best_ask: Option<Price>,
        current_timestamp: Nanoseconds,
    ) -> Result<(), RiskRejection> {
        // 1. Hardware/Software Kill Switch Check
        if self.kill_switch_active {
            return Err(RiskRejection::KillSwitchActive);
        }

        // 2. Single Order Quantity Limit
        if qty > self.limits.max_order_qty {
            return Err(RiskRejection::OrderQtyExceedsLimit);
        }

        // 3. Single Order Notional Limit
        let notional = price.to_f64() * qty.to_f64();
        if notional > self.limits.max_order_notional {
            return Err(RiskRejection::OrderNotionalExceedsLimit);
        }

        // 4. Position Limit Check
        let delta = if side.is_bid() { qty.to_f64() } else { -qty.to_f64() };
        let projected_inventory = (current_inventory + delta).abs();
        if projected_inventory > self.limits.max_position_qty {
            return Err(RiskRejection::PositionLimitExceeded);
        }

        // 5. Price Collar Check (Prevents aggressive fills and fat-finger book sweeps)
        let price_f = price.to_f64();
        let collar_pct = self.limits.max_price_collar_pct;

        match side {
            Side::Bid => {
                if let Some(ask) = best_ask {
                    let max_allowed_bid = ask.to_f64() * (1.0 + collar_pct);
                    if price_f > max_allowed_bid {
                        return Err(RiskRejection::PriceCollarViolation);
                    }
                }
            }
            Side::Ask => {
                if let Some(bid) = best_bid {
                    let min_allowed_ask = bid.to_f64() * (1.0 - collar_pct);
                    if price_f < min_allowed_ask {
                        return Err(RiskRejection::PriceCollarViolation);
                    }
                }
            }
        }

        // 6. Sliding Window Rate Limiter
        let now_ns = current_timestamp.as_nanos();
        let window_start = now_ns.saturating_sub(self.limits.rate_limit_window_ns);

        // Evict expired timestamps from front
        self.order_timestamps.retain(|&t| t >= window_start);

        if self.order_timestamps.len() >= self.limits.max_orders_per_window {
            return Err(RiskRejection::RateLimitExceeded);
        }

        self.order_timestamps.push(now_ns);
        Ok(())
    }

    /// Trigger Emergency Kill Switch
    pub fn trip_kill_switch(&mut self) {
        self.kill_switch_active = true;
    }

    /// Reset Kill Switch
    pub fn reset_kill_switch(&mut self) {
        self.kill_switch_active = false;
    }
}
