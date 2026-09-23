use hyperion_hft::core::types::{Nanoseconds, Price, Qty, Side};
use hyperion_hft::risk::controller::{RiskController, RiskLimits, RiskRejection};

#[test]
fn test_risk_fat_finger_and_collar() {
    let limits = RiskLimits {
        max_order_qty: Qty::from_f64(10.0),
        max_order_notional: 100_000.0,
        max_position_qty: 20.0,
        max_price_collar_pct: 0.05, // 5% collar
        max_orders_per_window: 10,
        rate_limit_window_ns: 100_000_000,
    };
    let mut risk = RiskController::new(limits);

    let bbo_bid = Some(Price::from_f64(100.0));
    let bbo_ask = Some(Price::from_f64(101.0));

    // Normal order: Passes
    let res = risk.check_order(Side::Bid, Price::from_f64(100.0), Qty::from_f64(1.0), 0.0, bbo_bid, bbo_ask, Nanoseconds::ZERO);
    assert!(res.is_ok());

    // 1. Fat finger quantity: 15.0 > 10.0
    let res_qty = risk.check_order(Side::Bid, Price::from_f64(100.0), Qty::from_f64(15.0), 0.0, bbo_bid, bbo_ask, Nanoseconds::ZERO);
    assert_eq!(res_qty, Err(RiskRejection::OrderQtyExceedsLimit));

    // 2. Fat finger price collar: Buy at $110 when ask is $101 (> 5% above ask)
    let res_collar = risk.check_order(Side::Bid, Price::from_f64(110.0), Qty::from_f64(1.0), 0.0, bbo_bid, bbo_ask, Nanoseconds::ZERO);
    assert_eq!(res_collar, Err(RiskRejection::PriceCollarViolation));

    // 3. Position limit: current inventory is 19.5, trying to buy 2.0 -> projected 21.5 > 20.0
    let res_pos = risk.check_order(Side::Bid, Price::from_f64(100.0), Qty::from_f64(2.0), 19.5, bbo_bid, bbo_ask, Nanoseconds::ZERO);
    assert_eq!(res_pos, Err(RiskRejection::PositionLimitExceeded));
}

#[test]
fn test_risk_kill_switch_and_rate_limiter() {
    let limits = RiskLimits {
        max_order_qty: Qty::from_f64(10.0),
        max_order_notional: 100_000.0,
        max_position_qty: 20.0,
        max_price_collar_pct: 0.05,
        max_orders_per_window: 3, // Allow only 3 orders per 100ms
        rate_limit_window_ns: 100_000_000,
    };
    let mut risk = RiskController::new(limits);

    let bbo_bid = Some(Price::from_f64(100.0));
    let bbo_ask = Some(Price::from_f64(101.0));

    // Submit 3 orders rapidly within 10ms
    assert!(risk.check_order(Side::Bid, Price::from_f64(100.0), Qty::from_f64(1.0), 0.0, bbo_bid, bbo_ask, Nanoseconds::from_raw(1_000_000)).is_ok());
    assert!(risk.check_order(Side::Bid, Price::from_f64(100.0), Qty::from_f64(1.0), 0.0, bbo_bid, bbo_ask, Nanoseconds::from_raw(2_000_000)).is_ok());
    assert!(risk.check_order(Side::Bid, Price::from_f64(100.0), Qty::from_f64(1.0), 0.0, bbo_bid, bbo_ask, Nanoseconds::from_raw(3_000_000)).is_ok());

    // 4th order should violate rate limit
    let res_rate = risk.check_order(Side::Bid, Price::from_f64(100.0), Qty::from_f64(1.0), 0.0, bbo_bid, bbo_ask, Nanoseconds::from_raw(4_000_000));
    assert_eq!(res_rate, Err(RiskRejection::RateLimitExceeded));

    // Now after window expires (150ms later), it should succeed
    let res_later = risk.check_order(Side::Bid, Price::from_f64(100.0), Qty::from_f64(1.0), 0.0, bbo_bid, bbo_ask, Nanoseconds::from_raw(150_000_000));
    assert!(res_later.is_ok());

    // Trip kill switch: everything rejected immediately
    risk.trip_kill_switch();
    let res_kill = risk.check_order(Side::Bid, Price::from_f64(100.0), Qty::from_f64(1.0), 0.0, bbo_bid, bbo_ask, Nanoseconds::from_raw(200_000_000));
    assert_eq!(res_kill, Err(RiskRejection::KillSwitchActive));
}
