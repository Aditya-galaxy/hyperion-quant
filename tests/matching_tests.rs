use hyperion_hft::core::types::{Nanoseconds, Price, Qty, Side};
use hyperion_hft::matching::engine::{MatchingEngine, OrderExecutionResult, OrderType};

#[test]
fn test_matching_engine_fifo_priority() {
    let mut engine = MatchingEngine::new(100);

    // Place two resting sell orders at the same price $100.00
    // Order 1 arrives first (Qty: 2.0), Order 2 arrives second (Qty: 3.0)
    engine.process_order(1, Side::Ask, Price::from_f64(100.0), Qty::from_f64(2.0), OrderType::Limit, Nanoseconds::ZERO);
    engine.process_order(2, Side::Ask, Price::from_f64(100.0), Qty::from_f64(3.0), OrderType::Limit, Nanoseconds::ZERO);

    // Incoming aggressive Buy order for 3.0 units at $100.00
    let result = engine.process_order(
        3,
        Side::Bid,
        Price::from_f64(100.0),
        Qty::from_f64(3.0),
        OrderType::Limit,
        Nanoseconds::ZERO,
    );

    match result {
        OrderExecutionResult::Filled { reports } => {
            assert_eq!(reports.len(), 2);
            // First fill must be Order 1 for 2.0 units (FIFO time priority)
            assert_eq!(reports[0].maker_order_id, 1);
            assert_eq!(reports[0].matched_qty, Qty::from_f64(2.0));

            // Second fill must be Order 2 for 1.0 unit
            assert_eq!(reports[1].maker_order_id, 2);
            assert_eq!(reports[1].matched_qty, Qty::from_f64(1.0));
        }
        _ => panic!("Expected filled result"),
    }

    // Order 2 should still have 2.0 units resting on the book
    assert_eq!(engine.book.best_ask(), Some(Price::from_f64(100.0)));
    let top_asks = engine.book.get_top_asks(1);
    assert_eq!(top_asks[0].volume, Qty::from_f64(2.0));
}

#[test]
fn test_immediate_or_cancel_unfilled_portion_dropped() {
    let mut engine = MatchingEngine::new(100);

    // Resting ask: 1.0 unit at $100.00
    engine.process_order(1, Side::Ask, Price::from_f64(100.0), Qty::from_f64(1.0), OrderType::Limit, Nanoseconds::ZERO);

    // IOC Buy order for 5.0 units at $100.00
    let result = engine.process_order(
        2,
        Side::Bid,
        Price::from_f64(100.0),
        Qty::from_f64(5.0),
        OrderType::ImmediateOrCancel,
        Nanoseconds::ZERO,
    );

    match result {
        OrderExecutionResult::PartiallyFilled { reports, resting_order_id } => {
            assert_eq!(reports.len(), 1);
            assert_eq!(reports[0].matched_qty, Qty::from_f64(1.0));
            // Unfilled 4.0 units must NOT rest on the book
            assert_eq!(resting_order_id, None);
        }
        _ => panic!("Expected partially filled IOC result"),
    }

    // Book should now have no bids and no asks
    assert_eq!(engine.book.best_bid(), None);
    assert_eq!(engine.book.best_ask(), None);
}
