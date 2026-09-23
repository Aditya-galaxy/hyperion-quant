use std::collections::{BTreeMap, HashMap};

use crate::core::arena::Arena;
use crate::core::types::{Nanoseconds, OrderId, Price, Qty, Side};
use crate::orderbook::order::Order;
use crate::orderbook::price_level::PriceLevel;

/// Level 2 Depth Entry (Price and aggregate quantity)
#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub struct Level2Entry {
    pub price: Price,
    pub volume: Qty,
    pub order_count: u32,
}

/// Result of adding an order into the Limit Order Book.
#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum AddOrderResult {
    Success { order_idx: u32 },
    BookFull,
    DuplicateId,
    CrossesSpread, // If passive book rejected crossed orders
}

/// Ultra-low-latency Level 3 Limit Order Book (LOB).
/// Pre-allocates order storage in an Arena and manages price queues.
pub struct LimitOrderBook {
    pub arena: Arena<Order>,
    pub bids: BTreeMap<Price, PriceLevel>,
    pub asks: BTreeMap<Price, PriceLevel>,
    /// OrderId -> (Price, Side, ArenaIndex) for O(1) lookup during cancel/modify
    order_index: HashMap<OrderId, (Price, Side, u32)>,
    
    // Cached BBO
    pub best_bid: Option<Price>,
    pub best_ask: Option<Price>,
    
    // Microstructure metrics
    pub prev_best_bid: Option<Price>,
    pub prev_best_ask: Option<Price>,
    pub prev_bid_qty: Qty,
    pub prev_ask_qty: Qty,
    pub ofi_accumulator: i64,
}

impl LimitOrderBook {
    pub fn new(capacity: usize) -> Self {
        Self {
            arena: Arena::with_capacity(capacity),
            bids: BTreeMap::new(),
            asks: BTreeMap::new(),
            order_index: HashMap::with_capacity(capacity),
            best_bid: None,
            best_ask: None,
            prev_best_bid: None,
            prev_best_ask: None,
            prev_bid_qty: Qty::ZERO,
            prev_ask_qty: Qty::ZERO,
            ofi_accumulator: 0,
        }
    }

    /// Add an order to the book.
    pub fn add_order(
        &mut self,
        id: OrderId,
        price: Price,
        qty: Qty,
        side: Side,
        timestamp_ns: Nanoseconds,
    ) -> AddOrderResult {
        if self.order_index.contains_key(&id) {
            return AddOrderResult::DuplicateId;
        }

        let order_idx = match self.arena.alloc() {
            Some(idx) => idx,
            None => return AddOrderResult::BookFull,
        };

        // Write order into pre-allocated arena
        *self.arena.get_mut(order_idx) = Order::new(id, price, qty, side, timestamp_ns);

        match side {
            Side::Bid => {
                let level = self.bids.entry(price).or_insert_with(|| PriceLevel::new(price));
                level.append_order(&mut self.arena, order_idx);
            }
            Side::Ask => {
                let level = self.asks.entry(price).or_insert_with(|| PriceLevel::new(price));
                level.append_order(&mut self.arena, order_idx);
            }
        }

        self.order_index.insert(id, (price, side, order_idx));
        self.update_bbo();
        AddOrderResult::Success { order_idx }
    }

    /// Cancel an existing order by its unique OrderId.
    pub fn cancel_order(&mut self, id: OrderId) -> Option<Order> {
        let (price, side, order_idx) = self.order_index.remove(&id)?;
        let order = *self.arena.get(order_idx);

        match side {
            Side::Bid => {
                if let Some(level) = self.bids.get_mut(&price) {
                    level.remove_order(&mut self.arena, order_idx);
                    if level.is_empty() {
                        self.bids.remove(&price);
                    }
                }
            }
            Side::Ask => {
                if let Some(level) = self.asks.get_mut(&price) {
                    level.remove_order(&mut self.arena, order_idx);
                    if level.is_empty() {
                        self.asks.remove(&price);
                    }
                }
            }
        }

        self.arena.dealloc(order_idx);
        self.update_bbo();
        Some(order)
    }

    /// Reduce the quantity of an existing order.
    pub fn reduce_order(&mut self, id: OrderId, reduced_by: Qty) -> bool {
        let &(price, side, order_idx) = match self.order_index.get(&id) {
            Some(val) => val,
            None => return false,
        };

        let current_qty = self.arena.get(order_idx).qty;
        if reduced_by >= current_qty {
            self.cancel_order(id);
            return true;
        }

        match side {
            Side::Bid => {
                if let Some(level) = self.bids.get_mut(&price) {
                    level.reduce_order_qty(&mut self.arena, order_idx, reduced_by);
                }
            }
            Side::Ask => {
                if let Some(level) = self.asks.get_mut(&price) {
                    level.reduce_order_qty(&mut self.arena, order_idx, reduced_by);
                }
            }
        }

        self.update_bbo();
        true
    }

    /// Updates cached Best Bid and Best Offer and computes Order Flow Imbalance.
    #[inline(always)]
    fn update_bbo(&mut self) {
        let new_best_bid = self.bids.keys().next_back().copied();
        let new_best_ask = self.asks.keys().next().copied();

        let new_bid_qty = new_best_bid
            .and_then(|p| self.bids.get(&p))
            .map(|l| l.total_volume)
            .unwrap_or(Qty::ZERO);

        let new_ask_qty = new_best_ask
            .and_then(|p| self.asks.get(&p))
            .map(|l| l.total_volume)
            .unwrap_or(Qty::ZERO);

        // Compute Order Flow Imbalance (OFI) delta:
        // Cont, Kukanov & Stoikov (2014) OFI formulation
        let delta_bid = match (new_best_bid, self.prev_best_bid) {
            (Some(nb), Some(pb)) if nb > pb => new_bid_qty.0 as i64,
            (Some(nb), Some(pb)) if nb == pb => (new_bid_qty.0 as i64) - (self.prev_bid_qty.0 as i64),
            (Some(_), Some(_)) => -(self.prev_bid_qty.0 as i64),
            (Some(_), None) => new_bid_qty.0 as i64,
            _ => 0,
        };

        let delta_ask = match (new_best_ask, self.prev_best_ask) {
            (Some(na), Some(pa)) if na < pa => new_ask_qty.0 as i64,
            (Some(na), Some(pa)) if na == pa => (new_ask_qty.0 as i64) - (self.prev_ask_qty.0 as i64),
            (Some(_), Some(_)) => -(self.prev_ask_qty.0 as i64),
            (Some(_), None) => new_ask_qty.0 as i64,
            _ => 0,
        };

        let ofi_step = delta_bid - delta_ask;
        self.ofi_accumulator = self.ofi_accumulator.saturating_add(ofi_step);

        self.prev_best_bid = new_best_bid;
        self.prev_best_ask = new_best_ask;
        self.prev_bid_qty = new_bid_qty;
        self.prev_ask_qty = new_ask_qty;

        self.best_bid = new_best_bid;
        self.best_ask = new_best_ask;
    }

    /// Best Bid price.
    #[inline(always)]
    pub fn best_bid(&self) -> Option<Price> {
        self.best_bid
    }

    /// Best Ask price.
    #[inline(always)]
    pub fn best_ask(&self) -> Option<Price> {
        self.best_ask
    }

    /// Mid-price calculation: (Best Bid + Best Ask) / 2
    #[inline(always)]
    pub fn mid_price(&self) -> Option<Price> {
        match (self.best_bid, self.best_ask) {
            (Some(bid), Some(ask)) => Some(Price::from_raw((bid.raw() + ask.raw()) / 2)),
            _ => None,
        }
    }

    /// Micro-price calculation weighted by opposite top-of-book volume:
    /// P_micro = (V_bid * P_ask + V_ask * P_bid) / (V_bid + V_ask)
    #[inline(always)]
    pub fn micro_price(&self) -> Option<Price> {
        let bid_p = self.best_bid?.to_f64();
        let ask_p = self.best_ask?.to_f64();
        let bid_v = self.prev_bid_qty.to_f64();
        let ask_v = self.prev_ask_qty.to_f64();

        let total_v = bid_v + ask_v;
        if total_v == 0.0 {
            return self.mid_price();
        }

        let micro = (bid_v * ask_p + ask_v * bid_p) / total_v;
        Some(Price::from_f64(micro))
    }

    /// Bid-Ask Spread in raw price units.
    #[inline(always)]
    pub fn spread(&self) -> Option<Price> {
        match (self.best_bid, self.best_ask) {
            (Some(bid), Some(ask)) => Some(ask - bid),
            _ => None,
        }
    }

    /// Retrieve Top-N price depth for Bids.
    pub fn get_top_bids(&self, n: usize) -> Vec<Level2Entry> {
        self.bids
            .iter()
            .rev()
            .take(n)
            .map(|(&price, level)| Level2Entry {
                price,
                volume: level.total_volume,
                order_count: level.order_count,
            })
            .collect()
    }

    /// Retrieve Top-N price depth for Asks.
    pub fn get_top_asks(&self, n: usize) -> Vec<Level2Entry> {
        self.asks
            .iter()
            .take(n)
            .map(|(&price, level)| Level2Entry {
                price,
                volume: level.total_volume,
                order_count: level.order_count,
            })
            .collect()
    }

    #[inline(always)]
    pub fn active_orders_count(&self) -> usize {
        self.order_index.len()
    }
}
