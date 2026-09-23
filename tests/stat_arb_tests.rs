use hyperion_quant::core::types::Price;
use hyperion_quant::quant::stat_arb::{PairsTradingConfig, StatArbAction, StatArbEngine};

#[test]
fn test_stat_arb_spread_and_zscore_calculation() {
    let config = PairsTradingConfig {
        hedge_ratio: 1.0,
        window_size: 20,
        z_entry_threshold: 2.0,
        z_exit_threshold: 0.5,
        z_stop_loss: 3.5,
    };

    let mut engine = StatArbEngine::new(config);

    // 1. Warm up with random noise around mean so std dev is non-zero (std dev ~ 0.5)
    for i in 0..25 {
        let offset = if i % 2 == 0 { 0.5 } else { -0.5 };
        let (action, _state) = engine.update(Price::from_f64(100.0 + offset), Price::from_f64(100.0));
        assert_eq!(action, StatArbAction::Hold);
    }

    // 2. Sharp upward spike in Asset A (+4.0) -> Spread expands heavily positive -> Short spread
    let (action_spike, state_spike) = engine.update(Price::from_f64(104.0), Price::from_f64(100.0));
    assert!(state_spike.z_score > 2.0);
    assert_eq!(action_spike, StatArbAction::ShortSpread);

    // 3. While in ShortSpread position, small continuation holds position
    let (action_hold, _) = engine.update(Price::from_f64(103.5), Price::from_f64(100.0));
    assert_eq!(action_hold, StatArbAction::Hold);

    // 4. Reversion back to mean -> Exit position
    let (action_rev, state_rev) = engine.update(Price::from_f64(100.0), Price::from_f64(100.0));
    assert!(state_rev.z_score.abs() < 1.0);
    assert_eq!(action_rev, StatArbAction::ExitPositions);

    // 5. From flat, sharp downward plunge in Asset A (-4.0) -> Long spread
    let (action_drop, state_drop) = engine.update(Price::from_f64(96.0), Price::from_f64(100.0));
    assert!(state_drop.z_score < -2.0);
    assert_eq!(action_drop, StatArbAction::LongSpread);
}
