"""
High-Performance Quantitative Feature Engineering using Polars.
Computes real-time market microstructure signals:
1. Multi-Level Order Flow Imbalance (OFI)
2. Micro-Price Skew vs Mid-Price
3. Bid/Ask Depth Ratio & Spread
4. Realized Volatility
5. Volume-Synchronized Toxicity (VPIN proxy)
"""

import numpy as np

def compute_microstructure_features(df_ticks):
    """
    Given a DataFrame of tick and book snapshot data:
    Columns expected: ['timestamp_ms', 'bid_p0', 'bid_q0', 'ask_p0', 'ask_q0', 'bid_q1', 'ask_q1', 'trade_qty', 'is_buyer_maker']
    """
    # 1. Mid-Price and Spread
    mid_price = (df_ticks['bid_p0'] + df_ticks['ask_p0']) / 2.0
    spread = (df_ticks['ask_p0'] - df_ticks['bid_p0'])
    spread_bps = (spread / mid_price) * 10_000.0

    # 2. Micro-Price
    total_depth_l0 = df_ticks['bid_q0'] + df_ticks['ask_q0'] + 1e-9
    micro_price = (df_ticks['bid_q0'] * df_ticks['ask_p0'] + df_ticks['ask_q0'] * df_ticks['bid_p0']) / total_depth_l0
    micro_price_bias_bps = ((micro_price - mid_price) / mid_price) * 10_000.0

    # 3. Order Book Depth Imbalance (Level 0 and Level 1)
    imbalance_l0 = (df_ticks['bid_q0'] - df_ticks['ask_q0']) / total_depth_l0
    total_depth_l1 = df_ticks['bid_q1'] + df_ticks['ask_q1'] + 1e-9
    imbalance_l1 = (df_ticks['bid_q1'] - df_ticks['ask_q1']) / total_depth_l1

    # 4. Order Flow Imbalance (OFI)
    # OFI_t = I(bid_p >= prev_bid_p)*bid_q - I(bid_p <= prev_bid_p)*prev_bid_q - ...
    prev_bid_p0 = np.roll(df_ticks['bid_p0'], 1)
    prev_bid_q0 = np.roll(df_ticks['bid_q0'], 1)
    prev_ask_p0 = np.roll(df_ticks['ask_p0'], 1)
    prev_ask_q0 = np.roll(df_ticks['ask_q0'], 1)

    delta_bid = np.where(df_ticks['bid_p0'] > prev_bid_p0, df_ticks['bid_q0'],
                np.where(df_ticks['bid_p0'] == prev_bid_p0, df_ticks['bid_q0'] - prev_bid_q0, -prev_bid_q0))
    delta_ask = np.where(df_ticks['ask_p0'] < prev_ask_p0, df_ticks['ask_q0'],
                np.where(df_ticks['ask_p0'] == prev_ask_p0, df_ticks['ask_q0'] - prev_ask_q0, -prev_ask_q0))
    ofi = delta_bid - delta_ask
    ofi[0] = 0.0

    # 5. Short-horizon log returns & Volatility
    returns = np.zeros(len(mid_price))
    returns[1:] = np.diff(np.log(mid_price))
    
    # Feature matrix: 8 standard inputs
    features = np.column_stack([
        spread_bps,             # F0: Bid-Ask Spread (bps)
        micro_price_bias_bps,   # F1: Micro-Price Skew (bps)
        imbalance_l0,           # F2: Top Level Queue Imbalance [-1, 1]
        imbalance_l1,           # F3: Level 1 Queue Imbalance [-1, 1]
        ofi,                    # F4: Order Flow Imbalance
        returns,                # F5: Instantaneous return
        np.abs(returns),        # F6: Instantaneous volatility proxy
        df_ticks.get('trade_imbalance', np.zeros(len(mid_price))), # F7: Trade signed volume
    ])

    return features, mid_price
