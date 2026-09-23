use crate::core::types::{Price, Qty};

/// Real-time Best Bid & Offer (BBO) from Binance WebSocket `@bookTicker` stream
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct BinanceBookTicker {
    pub update_id: u64,
    pub best_bid_price: Price,
    pub best_bid_qty: Qty,
    pub best_ask_price: Price,
    pub best_ask_qty: Qty,
}

/// Level 2 Depth update diff from Binance `@depth` or `@depth20` stream
#[derive(Debug, Clone)]
pub struct BinanceDepthUpdate {
    pub first_update_id: u64,
    pub final_update_id: u64,
    pub bids: Vec<(Price, Qty)>,
    pub asks: Vec<(Price, Qty)>,
}

/// Zero-copy, high-speed streaming parser for exchange JSON WebSocket feeds
pub struct BinanceFeedParser;

impl BinanceFeedParser {
    /// Parses a raw `@bookTicker` JSON payload into strongly typed `BinanceBookTicker`
    /// Example payload:
    /// `{"u":400900217,"s":"BNBUSDT","b":"25.35190000","B":"31.21000000","a":"25.36520000","A":"40.66000000"}`
    pub fn parse_book_ticker(payload: &str) -> Option<BinanceBookTicker> {
        let u = Self::extract_u64_field(payload, "\"u\":")?;
        let b = Self::extract_f64_field(payload, "\"b\":\"")?;
        let big_b = Self::extract_f64_field(payload, "\"B\":\"")?;
        let a = Self::extract_f64_field(payload, "\"a\":\"")?;
        let big_a = Self::extract_f64_field(payload, "\"A\":\"")?;

        Some(BinanceBookTicker {
            update_id: u,
            best_bid_price: Price::from_f64(b),
            best_bid_qty: Qty::from_f64(big_b),
            best_ask_price: Price::from_f64(a),
            best_ask_qty: Qty::from_f64(big_a),
        })
    }

    /// Fast helper to extract numeric field from JSON without pulling in heavy serde dependencies
    fn extract_u64_field(json: &str, key: &str) -> Option<u64> {
        let pos = json.find(key)?;
        let start = pos + key.len();
        let rest = &json[start..];
        let end = rest.find(|c: char| !c.is_ascii_digit()).unwrap_or(rest.len());
        rest[..end].parse::<u64>().ok()
    }

    fn extract_f64_field(json: &str, key: &str) -> Option<f64> {
        let pos = json.find(key)?;
        let start = pos + key.len();
        let rest = &json[start..];
        let end = rest.find('"').unwrap_or(rest.len());
        rest[..end].parse::<f64>().ok()
    }
}
