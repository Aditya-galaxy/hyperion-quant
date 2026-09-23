use hyperion_hft::core::types::{Nanoseconds, Price, Qty, Side};
use hyperion_hft::orderbook::lob::{AddOrderResult, LimitOrderBook};

#[test]
fn test_orderbook_bbo_and_cancel() {
    let mut book = LimitOrderBook::new(100);

    assert_eq!(book.best_bid(), None);
    assert_eq!(book.best_ask(), None);

    // Add bids
    book.add_order(1, Price::from_f64(99.0), Qty::from_f64(1.0), Side::Bid, Nanoseconds::ZERO);
    book.add_order(2, Price::from_f64(99.5), Qty::from_f64(2.0), Side::Bid, Nanoseconds::ZERO);

    // Add asks
    book.add_order(3, Price::from_f64(100.5), Qty::from_f64(1.5), Side::Ask, Nanoseconds::ZERO);
    book.add_order(4, Price::from_f64(101.0), Qty::from_f64(3.0), Side::Ask, Nanoseconds::ZERO);

    assert_eq!(book.best_bid(), Some(Price::from_f64(99.5)));
    assert_eq!(book.best_ask(), Some(Price::from_f64(100.5)));
    assert_eq!(book.mid_price(), Some(Price::from_f64(100.0)));
    assert_eq!(book.spread(), Some(Price::from_f64(1.0)));

    // Cancel best bid (order 2)
    let cancelled = book.cancel_order(2);
    assert!(cancelled.is_some());
    assert_eq!(cancelled.unwrap().id, 2);

    // Best bid should now drop to 99.0
    assert_eq!(book.best_bid(), Some(Price::from_f64(99.0)));
    assert_eq!(book.spread(), Some(Price::from_f64(1.5)));
}

#[test]
fn test_micro_price_weighting() {
    let mut book = LimitOrderBook::new(100);

    // Bid at 100 with heavy volume (10 units), Ask at 102 with thin volume (1 unit)
    book.add_order(1, Price::from_f64(100.0), Qty::from_f64(10.0), Side::Bid, Nanoseconds::ZERO);
    book.add_order(2, Price::from_f64(102.0), Qty::from_f64(1.0), Side::Ask, Nanoseconds::ZERO);

    // Mid price is 101.0
    assert_eq!(book.mid_price(), Some(Price::from_f64(101.0)));

    // Micro price should be pulled closer to Ask (102.0) because bid volume dominates:
    // P_micro = (10 * 102 + 1 * 100) / 11 = 1120 / 11 = 101.818
    let micro = book.micro_price().unwrap().to_f64();
    assert!(micro > 101.5);
    assert!(micro < 102.0);
}

#[test]
fn test_orderbook_duplicate_id_and_capacity() {
    let mut book = LimitOrderBook::new(2);

    let res1 = book.add_order(1, Price::from_f64(100.0), Qty::from_f64(1.0), Side::Bid, Nanoseconds::ZERO);
    assert!(matches!(res1, AddOrderResult::Success { .. }));

    // Duplicate ID rejected
    let res_dup = book.add_order(1, Price::from_f64(101.0), Qty::from_f64(1.0), Side::Ask, Nanoseconds::ZERO);
    assert_eq!(res_dup, AddOrderResult::DuplicateId);

    // Fill capacity
    let res2 = book.add_order(2, Price::from_f64(101.0), Qty::from_f64(1.0), Side::Ask, Nanoseconds::ZERO);
    assert!(matches!(res2, AddOrderResult::Success { .. }));

    // 3rd order exceeds capacity
    let res3 = book.add_order(3, Price::from_f64(102.0), Qty::from_f64(1.0), Side::Ask, Nanoseconds::ZERO);
    assert_eq!(res3, AddOrderResult::BookFull);
}
