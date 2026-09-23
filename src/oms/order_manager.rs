use std::collections::HashMap;

use crate::core::types::{Nanoseconds, OrderId, Price, Qty, Side};
use crate::matching::engine::ExecutionReport;

/// Order State in the Order Management System (OMS).
#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum OrderState {
    New,
    PartiallyFilled,
    Filled,
    Cancelled,
    Rejected,
}

/// Active Order Record in the OMS.
#[derive(Copy, Clone, Debug)]
pub struct ActiveOrder {
    pub order_id: OrderId,
    pub side: Side,
    pub price: Price,
    pub original_qty: Qty,
    pub executed_qty: Qty,
    pub state: OrderState,
    pub submit_time: Nanoseconds,
}

/// Real-time Position & PnL Accounting.
#[derive(Copy, Clone, Debug, Default)]
pub struct PositionAccount {
    /// Net inventory in base units (positive = long, negative = short)
    pub inventory: f64,
    /// Volume Weighted Average Price (VWAP) of current open inventory
    pub vwap_entry_price: f64,
    /// Total realized PnL in quote currency (USD)
    pub realized_pnl: f64,
    /// Total traded volume in base units
    pub total_volume_traded: f64,
    /// Total number of fills
    pub total_fills_count: u64,
}

impl PositionAccount {
    /// Compute unrealized PnL based on current mid-price.
    pub fn unrealized_pnl(&self, current_mid: Price) -> f64 {
        if self.inventory == 0.0 {
            0.0
        } else {
            let mid = current_mid.to_f64();
            self.inventory * (mid - self.vwap_entry_price)
        }
    }

    /// Total portfolio PnL (Realized + Unrealized).
    pub fn total_pnl(&self, current_mid: Price) -> f64 {
        self.realized_pnl + self.unrealized_pnl(current_mid)
    }

    /// Apply an execution fill to the position account.
    pub fn apply_fill(&mut self, side: Side, fill_price: Price, fill_qty: Qty) {
        let price = fill_price.to_f64();
        let qty = fill_qty.to_f64();
        self.total_volume_traded += qty;
        self.total_fills_count += 1;

        match side {
            Side::Bid => {
                // Buying
                if self.inventory >= 0.0 {
                    // Increasing existing long position
                    let new_inv = self.inventory + qty;
                    self.vwap_entry_price = (self.inventory * self.vwap_entry_price + qty * price) / new_inv;
                    self.inventory = new_inv;
                } else {
                    // Covering short position
                    let covered = qty.min(-self.inventory);
                    let profit_per_unit = self.vwap_entry_price - price;
                    self.realized_pnl += covered * profit_per_unit;
                    self.inventory += qty;

                    if self.inventory > 0.0 {
                        // Flipped to net long
                        self.vwap_entry_price = price;
                    }
                }
            }
            Side::Ask => {
                // Selling
                if self.inventory <= 0.0 {
                    // Increasing existing short position
                    let new_inv = self.inventory - qty;
                    let abs_inv = -new_inv;
                    self.vwap_entry_price = ((-self.inventory) * self.vwap_entry_price + qty * price) / abs_inv;
                    self.inventory = new_inv;
                } else {
                    // Selling down long position
                    let sold = qty.min(self.inventory);
                    let profit_per_unit = price - self.vwap_entry_price;
                    self.realized_pnl += sold * profit_per_unit;
                    self.inventory -= qty;

                    if self.inventory < 0.0 {
                        // Flipped to net short
                        self.vwap_entry_price = price;
                    }
                }
            }
        }
    }
}

/// Order Management System tracking orders, fills, and Dead Man's Switch.
pub struct OrderManager {
    pub active_orders: HashMap<OrderId, ActiveOrder>,
    pub position: PositionAccount,
    last_heartbeat_ns: Nanoseconds,
    heartbeat_timeout_ns: u64,
}

impl OrderManager {
    pub fn new(heartbeat_timeout_ns: u64) -> Self {
        Self {
            active_orders: HashMap::with_capacity(1024),
            position: PositionAccount::default(),
            last_heartbeat_ns: Nanoseconds::ZERO,
            heartbeat_timeout_ns,
        }
    }

    /// Record a newly submitted active order.
    pub fn on_order_submitted(
        &mut self,
        order_id: OrderId,
        side: Side,
        price: Price,
        qty: Qty,
        submit_time: Nanoseconds,
    ) {
        self.active_orders.insert(
            order_id,
            ActiveOrder {
                order_id,
                side,
                price,
                original_qty: qty,
                executed_qty: Qty::ZERO,
                state: OrderState::New,
                submit_time,
            },
        );
        self.last_heartbeat_ns = submit_time;
    }

    /// Process fill execution report.
    pub fn on_fill(&mut self, report: &ExecutionReport, our_order_id: OrderId, our_side: Side) {
        self.position.apply_fill(our_side, report.price, report.matched_qty);

        if let Some(order) = self.active_orders.get_mut(&our_order_id) {
            order.executed_qty += report.matched_qty;
            if order.executed_qty >= order.original_qty {
                order.state = OrderState::Filled;
            } else {
                order.state = OrderState::PartiallyFilled;
            }
        }

        // Clean up filled orders
        if let Some(order) = self.active_orders.get(&our_order_id) {
            if order.state == OrderState::Filled {
                self.active_orders.remove(&our_order_id);
            }
        }
    }

    /// Cancel order tracking.
    pub fn on_order_cancelled(&mut self, order_id: OrderId) {
        self.active_orders.remove(&order_id);
    }

    /// Heartbeat signal for Dead Man's Switch.
    pub fn heartbeat(&mut self, timestamp: Nanoseconds) {
        self.last_heartbeat_ns = timestamp;
    }

    /// Checks if the Dead Man's Switch (Cancel-On-Disconnect) threshold has expired.
    pub fn is_dead_mans_switch_triggered(&self, current_time: Nanoseconds) -> bool {
        if self.last_heartbeat_ns.0 == 0 {
            return false;
        }
        let elapsed = current_time.as_nanos().saturating_sub(self.last_heartbeat_ns.as_nanos());
        elapsed >= self.heartbeat_timeout_ns
    }
}
