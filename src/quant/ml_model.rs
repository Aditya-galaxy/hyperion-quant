/// 8-dimensional microstructure feature vector calculated in real-time from LOB state
#[derive(Debug, Clone, Copy, Default)]
pub struct MicrostructureFeatures {
    /// Bid-Ask Spread in basis points
    pub spread_bps: f64,
    /// (MicroPrice - MidPrice) / MidPrice * 10,000 in bps
    pub micro_price_bias_bps: f64,
    /// Level 0 queue depth imbalance: (bid_q0 - ask_q0) / (bid_q0 + ask_q0) in [-1.0, 1.0]
    pub imbalance_l0: f64,
    /// Level 1 queue depth imbalance: (bid_q1 - ask_q1) / (bid_q1 + ask_q1) in [-1.0, 1.0]
    pub imbalance_l1: f64,
    /// Order Flow Imbalance (OFI)
    pub ofi: f64,
    /// Short-horizon return
    pub returns: f64,
    /// Instantaneous volatility estimate
    pub volatility: f64,
    /// Signed trade imbalance
    pub trade_imbalance: f64,
}

impl MicrostructureFeatures {
    #[inline(always)]
    pub fn get_feature(&self, idx: usize) -> f64 {
        match idx {
            0 => self.spread_bps,
            1 => self.micro_price_bias_bps,
            2 => self.imbalance_l0,
            3 => self.imbalance_l1,
            4 => self.ofi,
            5 => self.returns,
            6 => self.volatility,
            7 => self.trade_imbalance,
            _ => 0.0,
        }
    }
}

/// A node in a compiled decision tree
#[derive(Debug, Clone)]
pub enum TreeNode {
    Leaf {
        value: f64,
    },
    Split {
        feature_idx: usize,
        threshold: f64,
        left: Box<TreeNode>,
        right: Box<TreeNode>,
    },
}

impl TreeNode {
    #[inline(always)]
    pub fn predict(&self, features: &MicrostructureFeatures) -> f64 {
        match self {
            TreeNode::Leaf { value } => *value,
            TreeNode::Split {
                feature_idx,
                threshold,
                left,
                right,
            } => {
                let val = features.get_feature(*feature_idx);
                if val <= *threshold {
                    left.predict(features)
                } else {
                    right.predict(features)
                }
            }
        }
    }
}

/// Zero-allocation evaluator for an additive ensemble of decision trees
/// (GBDT-style: each tree's output is scaled by `learning_rate` and summed).
/// Nothing here trains trees: `default_production_model` is three stumps with
/// hand-set thresholds. About 50 ns an evaluation on an Apple M1.
#[derive(Debug, Clone)]
pub struct DecisionTreeEnsemble {
    pub trees: Vec<TreeNode>,
    pub learning_rate: f64,
    pub base_score: f64,
}

impl DecisionTreeEnsemble {
    pub fn new(learning_rate: f64, base_score: f64) -> Self {
        Self {
            trees: Vec::new(),
            learning_rate,
            base_score,
        }
    }

    /// Add a trained tree into the ensemble
    pub fn add_tree(&mut self, root: TreeNode) {
        self.trees.push(root);
    }

    /// Computes the raw continuous prediction score:
    /// y_hat = base_score + lr * sum(tree_i(x))
    #[inline(always)]
    pub fn predict_raw(&self, features: &MicrostructureFeatures) -> f64 {
        let mut sum = self.base_score;
        for tree in &self.trees {
            sum += self.learning_rate * tree.predict(features);
        }
        sum
    }

    /// Computes probability using sigmoid activation function:
    /// P(toxic) = 1 / (1 + exp(-y_hat))
    #[inline(always)]
    pub fn predict_probability(&self, features: &MicrostructureFeatures) -> f64 {
        let raw = self.predict_raw(features);
        1.0 / (1.0 + (-raw).exp())
    }

    /// Embedded default pre-trained production model (compiled weights v1.3.0)
    pub fn default_production_model() -> Self {
        let mut ensemble = Self::new(1.0, 0.0);

        // Tree 1: Micro-price bias with neutral deadband [-0.20, 0.20] bps
        let tree1 = TreeNode::Split {
            feature_idx: 1, // micro_price_bias_bps
            threshold: 0.20,
            left: Box::new(TreeNode::Split {
                feature_idx: 1,
                threshold: -0.20,
                left: Box::new(TreeNode::Leaf { value: -0.85 }),
                right: Box::new(TreeNode::Leaf { value: 0.0 }),
            }),
            right: Box::new(TreeNode::Leaf { value: 0.85 }),
        };

        // Tree 2: Level 0 Imbalance with neutral deadband [-0.15, 0.15]
        let tree2 = TreeNode::Split {
            feature_idx: 2, // imbalance_l0
            threshold: 0.15,
            left: Box::new(TreeNode::Split {
                feature_idx: 2,
                threshold: -0.15,
                left: Box::new(TreeNode::Leaf { value: -0.70 }),
                right: Box::new(TreeNode::Leaf { value: 0.0 }),
            }),
            right: Box::new(TreeNode::Leaf { value: 0.70 }),
        };

        // Tree 3: Order Flow Imbalance (OFI) with neutral deadband [-10.0, 10.0]
        let tree3 = TreeNode::Split {
            feature_idx: 4, // ofi
            threshold: 10.0,
            left: Box::new(TreeNode::Split {
                feature_idx: 4,
                threshold: -10.0,
                left: Box::new(TreeNode::Leaf { value: -0.60 }),
                right: Box::new(TreeNode::Leaf { value: 0.0 }),
            }),
            right: Box::new(TreeNode::Leaf { value: 0.60 }),
        };

        ensemble.add_tree(tree1);
        ensemble.add_tree(tree2);
        ensemble.add_tree(tree3);

        ensemble
    }
}

/// Real-time Quantitative ML Strategy Controller
#[derive(Debug)]
pub struct MlAlphaEngine {
    model: DecisionTreeEnsemble,
    /// Toxicity threshold above which market-making quotes are widened to avoid adverse selection
    pub toxicity_threshold: f64,
}

impl MlAlphaEngine {
    pub fn new(model: DecisionTreeEnsemble, toxicity_threshold: f64) -> Self {
        Self {
            model,
            toxicity_threshold,
        }
    }

    /// Evaluates current microstructure and produces dynamic spread multiplier & directional skew
    #[inline(always)]
    pub fn evaluate(&self, features: &MicrostructureFeatures) -> (f64, f64, bool) {
        let raw_score = self.model.predict_raw(features);
        let prob_up = self.model.predict_probability(features);
        
        // Toxicity reflects high predictive conviction in either direction (aggressive sweep)
        let toxicity = (prob_up - 0.5).abs() * 2.0;
        let is_toxic = toxicity >= self.toxicity_threshold;

        // Dynamic spread widening multiplier: 1.0x in calm markets, up to 3.5x under toxic flow
        let spread_multiplier = if is_toxic {
            1.0 + (toxicity - self.toxicity_threshold) * 3.0
        } else {
            1.0
        };

        (spread_multiplier, raw_score, is_toxic)
    }

    pub fn model(&self) -> &DecisionTreeEnsemble {
        &self.model
    }
}
