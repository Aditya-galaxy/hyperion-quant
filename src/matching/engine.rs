use crate::core::types::{Nanoseconds, OrderId, Price, Qty, Side};
use crate::orderbook::lob::{AddOrderResult, LimitOrderBook};

/// Execution Report emitted when a trade fill occurs.
#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub struct ExecutionReport {
    pub match_id: u64,
    pub maker_order_id: OrderId,
    pub taker_order_id: OrderId,
    pub price: Price,
    pub matched_qty: Qty,
    pub maker_side: Side,
    pub timestamp_ns: Nanoseconds,
}

/// Order type for incoming orders into the matching engine.
#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum OrderType {
    /// Limit order rests on book if not fully matched immediately.
    Limit,
    /// Immediate-Or-Cancel: matches what is available, cancels remaining.
    ImmediateOrCancel,
    /// Fill-Or-Kill: must fill completely or execute nothing.
    FillOrKill,
}

/// Result of submitting an order to the Matching Engine.
#[derive(Debug)]
pub enum OrderExecutionResult {
    Filled {
        reports: Vec<ExecutionReport>,
    },
    PartiallyFilled {
        reports: Vec<ExecutionReport>,
        resting_order_id: Option<OrderId>,
    },
    PlacedOnBook {
        order_id: OrderId,
    },
    Rejected(&'static str),
}

/// High-performance deterministic matching engine core.
pub struct MatchingEngine {
    pub book: LimitOrderBook,
    match_counter: u64,
}

impl MatchingEngine {
    pub fn new(capacity: usize) -> Self {
        Self {
            book: LimitOrderBook::new(capacity),
            match_counter: 0,
        }
    }

    /// Process an incoming order with price-time priority matching.
    pub fn process_order(
        &mut self,
        order_id: OrderId,
        side: Side,
        price: Price,
        mut remaining_qty: Qty,
        order_type: OrderType,
        timestamp_ns: Nanoseconds,
    ) -> OrderExecutionResult {
        let mut reports = Vec::new();

        // Check if FOK can be completely filled first
        if order_type == OrderType::FillOrKill {
            if !self.can_fill_completely(side, price, remaining_qty) {
                return OrderExecutionResult::Rejected("FOK order cannot be completely filled");
            }
        }

        // Matching loop against resting opposite side
        match side {
            Side::Bid => {
                // Taker is BUYING -> matches against resting ASKS (lowest ask first)
                while !remaining_qty.is_zero() {
                    let best_ask_price = match self.book.best_ask() {
                        Some(p) if p <= price => p,
                        _ => break, // No more matching asks within limit price
                    };

                    let level = self.book.asks.get_mut(&best_ask_price).unwrap();
                    let head_idx = match level.head_idx {
                        Some(idx) => idx,
                        None => break,
                    };

                    let (maker_id, maker_qty) = {
                        let maker_order = self.book.arena.get(head_idx);
                        (maker_order.id, maker_order.qty)
                    };

                    let match_qty = std::cmp::min(remaining_qty.0, maker_qty.0);
                    let matched = Qty::from_raw(match_qty);

                    self.match_counter += 1;
                    reports.push(ExecutionReport {
                        match_id: self.match_counter,
                        maker_order_id: maker_id,
                        taker_order_id: order_id,
                        price: best_ask_price,
                        matched_qty: matched,
                        maker_side: Side::Ask,
                        timestamp_ns,
                    });

                    remaining_qty -= matched;

                    if matched == maker_qty {
                        // Resting order completely consumed -> cancel from book
                        self.book.cancel_order(maker_id);
                    } else {
                        // Resting order partially filled -> reduce in place
                        self.book.reduce_order(maker_id, matched);
                    }
                }
            }
            Side::Ask => {
                // Taker is SELLING -> matches against resting BIDS (highest bid first)
                while !remaining_qty.is_zero() {
                    let best_bid_price = match self.book.best_bid() {
                        Some(p) if p >= price => p,
                        _ => break, // No more matching bids within limit price
                    };

                    let level = self.book.bids.get_mut(&best_bid_price).unwrap();
                    let head_idx = match level.head_idx {
                        Some(idx) => idx,
                        None => break,
                    };

                    let (maker_id, maker_qty) = {
                        let maker_order = self.book.arena.get(head_idx);
                        (maker_order.id, maker_order.qty)
                    };

                    let match_qty = std::cmp::min(remaining_qty.0, maker_qty.0);
                    let matched = Qty::from_raw(match_qty);

                    self.match_counter += 1;
                    reports.push(ExecutionReport {
                        match_id: self.match_counter,
                        maker_order_id: maker_id,
                        taker_order_id: order_id,
                        price: best_bid_price,
                        matched_qty: matched,
                        maker_side: Side::Bid,
                        timestamp_ns,
                    });

                    remaining_qty -= matched;

                    if matched == maker_qty {
                        self.book.cancel_order(maker_id);
                    } else {
                        self.book.reduce_order(maker_id, matched);
                    }
                }
            }
        }

        // Post-matching handling of remaining quantity
        if remaining_qty.is_zero() {
            OrderExecutionResult::Filled { reports }
        } else {
            match order_type {
                OrderType::Limit => {
                    // Place remaining quantity onto the book as passive liquidity
                    match self.book.add_order(order_id, price, remaining_qty, side, timestamp_ns) {
                        AddOrderResult::Success { .. } => {
                            if reports.is_empty() {
                                OrderExecutionResult::PlacedOnBook { order_id }
                            } else {
                                OrderExecutionResult::PartiallyFilled {
                                    reports,
                                    resting_order_id: Some(order_id),
                                }
                            }
                        }
                        AddOrderResult::BookFull => OrderExecutionResult::Rejected("Order arena capacity exceeded"),
                        AddOrderResult::DuplicateId => OrderExecutionResult::Rejected("Duplicate Order ID"),
                        AddOrderResult::CrossesSpread => OrderExecutionResult::Rejected("Order unexpectedly crosses spread"),
                    }
                }
                OrderType::ImmediateOrCancel | OrderType::FillOrKill => {
                    // Unfilled portion is cancelled
                    OrderExecutionResult::PartiallyFilled {
                        reports,
                        resting_order_id: None,
                    }
                }
            }
        }
    }

    /// Check if an incoming order can be completely filled without modifying the book.
    fn can_fill_completely(&self, side: Side, limit_price: Price, target_qty: Qty) -> bool {
        let mut available = 0u64;

        match side {
            Side::Bid => {
                for (&ask_p, level) in self.book.asks.iter() {
                    if ask_p > limit_price {
                        break;
                    }
                    available += level.total_volume.0;
                    if available >= target_qty.0 {
                        return true;
                    }
                }
            }
            Side::Ask => {
                for (&bid_p, level) in self.book.bids.iter().rev() {
                    if bid_p < limit_price {
                        break;
                    }
                    available += level.total_volume.0;
                    if available >= target_qty.0 {
                        return true;
                    }
                }
            }
        }
        false
    }

    #[inline(always)]
    pub fn cancel_order(&mut self, order_id: OrderId) -> bool {
        self.book.cancel_order(order_id).is_some()
    }
}
