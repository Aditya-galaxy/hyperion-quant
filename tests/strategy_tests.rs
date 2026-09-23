use hyperion_hft::core::types::{Price, Qty};
use hyperion_hft::strategy::avellaneda_stoikov::{AsModelParams, AvellanedaStoikov};

#[test]
fn test_avellaneda_stoikov_inventory_skewing() {
    let params = AsModelParams {
        gamma: 0.5,
        sigma: 0.2, // 20% vol so inventory penalty is well above 1 tick
        kappa: 1.5,
        time_horizon: 1.0,
        tick_size: Price::from_f64(0.01),
    };
    let strategy = AvellanedaStoikov::new(params, Qty::from_f64(1.0));
    let mid = Price::from_f64(100.0);

    // 1. Neutral inventory (q = 0)
    let q_neutral = strategy.compute_quotes(mid, 0.0, 0.0);
    assert_eq!(q_neutral.reservation_price, mid);

    // 2. Heavy Long inventory (q = +10.0) -> Must lower reservation price to encourage selling
    let q_long = strategy.compute_quotes(mid, 10.0, 0.0);
    assert!(q_long.reservation_price < mid, "Long inventory must lower reservation price");
    assert!(q_long.bid_price < q_neutral.bid_price, "Bid must be lower to discourage buying");
    assert!(q_long.ask_price < q_neutral.ask_price, "Ask must be lower to attract sellers/fills");

    // 3. Heavy Short inventory (q = -10.0) -> Must raise reservation price to encourage buying
    let q_short = strategy.compute_quotes(mid, -10.0, 0.0);
    assert!(q_short.reservation_price > mid, "Short inventory must raise reservation price");
    assert!(q_short.bid_price > q_neutral.bid_price, "Bid must be higher to attract fills");
    assert!(q_short.ask_price > q_neutral.ask_price, "Ask must be higher to discourage selling");
}
