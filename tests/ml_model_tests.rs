use hyperion_quant::quant::ml_model::{
    DecisionTreeEnsemble, MicrostructureFeatures, MlAlphaEngine,
};

#[test]
fn test_tree_inference_direction_and_probability() {
    let model = DecisionTreeEnsemble::default_production_model();
    let engine = MlAlphaEngine::new(model, 0.40); // 40% toxicity cutoff

    // Case 1: Extreme Bullish Order Flow Imbalance & Micro-price upward skew
    let bull_features = MicrostructureFeatures {
        spread_bps: 1.2,
        micro_price_bias_bps: 2.5,
        imbalance_l0: 0.85,
        imbalance_l1: 0.70,
        ofi: 45.0,
        returns: 0.0005,
        volatility: 0.02,
        trade_imbalance: 5.0,
    };

    let (mult_bull, skew_bull, is_toxic_bull) = engine.evaluate(&bull_features);
    assert!(skew_bull > 0.0, "Expected positive skew for bullish order flow");
    assert!(is_toxic_bull, "Expected toxic flag for extreme directional sweep");
    assert!(mult_bull > 1.0, "Expected quote spread widening on toxic flow");

    // Case 2: Calm, balanced neutral market
    let calm_features = MicrostructureFeatures {
        spread_bps: 1.0,
        micro_price_bias_bps: 0.0,
        imbalance_l0: 0.05,
        imbalance_l1: -0.05,
        ofi: 0.0,
        returns: 0.0,
        volatility: 0.01,
        trade_imbalance: 0.0,
    };

    let (mult_calm, skew_calm, is_toxic_calm) = engine.evaluate(&calm_features);
    assert!(!is_toxic_calm);
    assert_eq!(mult_calm, 1.0);
    assert!(skew_calm.abs() < 0.3);

    // Case 3: Extreme Bearish Order Flow (Massive sell wall & negative OFI)
    let bear_features = MicrostructureFeatures {
        spread_bps: 3.5, // wide spread
        micro_price_bias_bps: -3.0,
        imbalance_l0: -0.90,
        imbalance_l1: -0.80,
        ofi: -60.0,
        returns: -0.001,
        volatility: 0.08,
        trade_imbalance: -10.0,
    };

    let (mult_bear, skew_bear, is_toxic_bear) = engine.evaluate(&bear_features);
    assert!(skew_bear < 0.0);
    assert!(is_toxic_bear);
    assert!(mult_bear > 1.2);
}
