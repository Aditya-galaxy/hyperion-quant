use hyperion_quant::strategy::black_swan_defense::{BlackSwanDefense, ShockRegime};

#[test]
fn test_black_swan_defense_regimes_and_circuit_breaker() {
    let mut defense = BlackSwanDefense::new(
        10.0,  // 10 BTC initial depth baseline
        0.01,  // 1% normal vol
        0.25,  // 75% depth drop
        2.5,   // 2.5x vol spike
    );

    // 1. Normal market liquidity (10 BTC depth, 0.01 vol, 1 bps spread)
    let (regime, mult, pull) = defense.evaluate_shock(10.0, 0.01, 1.0);
    assert_eq!(regime, ShockRegime::Normal);
    assert_eq!(mult, 1.0);
    assert!(!pull);

    // 2. Warning: Moderate liquidity thinning (depth drops to 2.0 BTC, vol jumps to 0.026)
    let (regime_warn, mult_warn, pull_warn) = defense.evaluate_shock(2.0, 0.026, 3.5);
    assert_eq!(regime_warn, ShockRegime::FlashCrashWarning);
    assert!(mult_warn >= 2.0);
    assert!(!pull_warn);

    // 3. Catastrophic Black-Swan: Total liquidity air-pocket (depth collapses to 0.5 BTC = 5% of baseline)
    let (regime_emergency, mult_emergency, pull_emergency) = defense.evaluate_shock(0.5, 0.08, 20.0);
    assert_eq!(regime_emergency, ShockRegime::EmergencyCircuitBreaker);
    assert_eq!(mult_emergency, 4.0);
    assert!(pull_emergency, "Expected circuit breaker pull under liquidity collapse");
}
