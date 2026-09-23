pub mod analytics;
pub mod backtest;
pub mod core;
pub mod feed;
pub mod matching;
pub mod oms;
pub mod orderbook;
pub mod quant;
pub mod risk;
pub mod strategy;

pub use analytics::{PerformanceMetrics, QuantMetricsCalculator, TradeRecord};
pub use backtest::{BacktestConfig, BacktestEngine, BacktestResult, HistoricalBar, Position, SyntheticDataFeed};
pub use core::types::*;
pub use core::{Arena, SpscRingBuffer};
pub use feed::{BinanceBookTicker, BinanceDepthUpdate, BinanceFeedParser};
pub use matching::{ExecutionReport, MatchingEngine, OrderExecutionResult, OrderType};
pub use oms::{ActiveOrder, OrderManager, OrderState, PositionAccount};
pub use orderbook::{AddOrderResult, Level2Entry, LimitOrderBook, Order, PriceLevel};
pub use quant::{
    BasisArbConfig, BasisArbEngine, BasisSignal, DecisionTreeEnsemble, MicrostructureFeatures,
    MlAlphaEngine, PairSpreadState, PairsTradingConfig, StatArbAction, StatArbEngine, TreeNode,
    YieldSummary,
};
pub use risk::{RiskController, RiskLimits, RiskRejection};
pub use strategy::{AsModelParams, AvellanedaStoikov, OfiAlpha, QuoteProposal};
