use crate::core::arena::Arena;
use crate::core::types::{Price, Qty};
use crate::orderbook::order::Order;

/// PriceLevel represents an aggregate price queue in the order book.
/// Orders at the same price are stored in a doubly-linked FIFO queue for O(1) ops.
#[derive(Clone, Debug, PartialEq, Eq)]
#[repr(align(64))]
pub struct PriceLevel {
    pub price: Price,
    pub total_volume: Qty,
    pub order_count: u32,
    pub head_idx: Option<u32>,
    pub tail_idx: Option<u32>,
}

impl PriceLevel {
    pub fn new(price: Price) -> Self {
        Self {
            price,
            total_volume: Qty::ZERO,
            order_count: 0,
            head_idx: None,
            tail_idx: None,
        }
    }

    /// Append order to the tail of the FIFO queue (O(1)).
    #[inline(always)]
    pub fn append_order(&mut self, arena: &mut Arena<Order>, order_idx: u32) {
        let order = arena.get_mut(order_idx);
        let qty = order.qty;
        order.next_idx = None;
        order.prev_idx = self.tail_idx;

        if let Some(tail) = self.tail_idx {
            arena.get_mut(tail).next_idx = Some(order_idx);
        } else {
            self.head_idx = Some(order_idx);
        }
        self.tail_idx = Some(order_idx);

        self.total_volume += qty;
        self.order_count += 1;
    }

    /// Remove order from the price level FIFO queue (O(1)).
    #[inline(always)]
    pub fn remove_order(&mut self, arena: &mut Arena<Order>, order_idx: u32) {
        let (prev, next, qty) = {
            let order = arena.get(order_idx);
            (order.prev_idx, order.next_idx, order.qty)
        };

        if let Some(p) = prev {
            arena.get_mut(p).next_idx = next;
        } else {
            self.head_idx = next;
        }

        if let Some(n) = next {
            arena.get_mut(n).prev_idx = prev;
        } else {
            self.tail_idx = prev;
        }

        self.total_volume -= qty;
        self.order_count = self.order_count.saturating_sub(1);
    }

    /// Partially reduce volume from an existing order.
    #[inline(always)]
    pub fn reduce_order_qty(&mut self, arena: &mut Arena<Order>, order_idx: u32, reduced_by: Qty) {
        let order = arena.get_mut(order_idx);
        order.qty -= reduced_by;
        self.total_volume -= reduced_by;
    }

    #[inline(always)]
    pub fn is_empty(&self) -> bool {
        self.order_count == 0
    }
}
