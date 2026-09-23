use hyperion_quant::core::types::Price;
use hyperion_quant::feed::binance_feed::BinanceFeedParser;

#[test]
fn test_binance_book_ticker_parser() {
    let raw_ws_msg = r#"{"u":400900217,"s":"BTCUSDT","b":"65230.50000000","B":"12.45000000","a":"65231.00000000","A":"8.22000000"}"#;

    let parsed = BinanceFeedParser::parse_book_ticker(raw_ws_msg).expect("Failed to parse ticker");

    assert_eq!(parsed.update_id, 400900217);
    assert_eq!(parsed.best_bid_price, Price::from_f64(65230.50));
    assert_eq!(parsed.best_ask_price, Price::from_f64(65231.00));
}
