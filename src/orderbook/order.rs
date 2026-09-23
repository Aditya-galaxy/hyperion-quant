use crate::core::types::{Nanoseconds, OrderId, Price, Qty, Side};

/// Intrusive order node stored in pre-allocated Arena slab.
#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub struct Order {
    pub id: OrderId,
    pub price: Price,
    pub qty: Qty,
    pub side: Side,
    pub timestamp_ns: Nanoseconds,
    /// Index of previous order in the same price level FIFO queue
    pub prev_idx: Option<u32>,
    /// Index of next order in the same price level FIFO queue
    pub next_idx: Option<u32>,
}

impl Default for Order {
    fn default() -> Self {
        Self {
            id: 0,
            price: Price::ZERO,
            qty: Qty::ZERO,
            side: Side::Bid,
            timestamp_ns: Nanoseconds::ZERO,
            prev_idx: None,
            next_idx: None,
        }
    }
}

impl Order {
    #[inline(always)]
    pub fn new(id: OrderId, price: Price, qty: Qty, side: Side, timestamp_ns: Nanoseconds) -> Self {
        Self {
            id,
            price,
            qty,
            side,
            timestamp_ns,
            prev_idx: None,
            next_idx: None,
        }
    }
}
