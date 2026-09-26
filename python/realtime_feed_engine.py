"""
HYPERION QUANT: REAL-TIME UNIFIED MARKET DATA INGESTION & ALPHA/DEFENSE ENGINE
==============================================================================
Connects directly to Binance live exchange WebSocket streams:
  1. `<symbol>@bookTicker` (Streaming Best Bid & Offer + Depth)
  2. `<symbol>@trade`      (Streaming Signed Taker Trades)

Integrates:
  - Feature A: Multilingual News Alpha Engine (Korean, Chinese, Japanese, English news skew)
  - Feature B: Black-Swan Liquidity & Flash Crash Defense (circuit breakers & depth collapse defense)
  - Adverse-selection rule: three hand-set decision stumps over order-book features
  - Dynamic Avellaneda-Stoikov reservation price and defended quote spread calculation

Simultaneously records clean real-time tick datasets to disk for continuous learning.
"""

import asyncio
import collections
import csv
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
import websockets

from multilingual_alpha_engine import MultilingualNewsAlphaEngine

class RealTimeMLEngine:
    def __init__(self, weights_path="python/lob_model_weights.json", toxicity_threshold=0.35):
        self.toxicity_threshold = toxicity_threshold
        with open(weights_path, "r") as f:
            self.model = json.load(f)
        self.trees = self.model["trees"]
        self.learning_rate = self.model.get("learning_rate", 1.0)
        self.base_score = self.model.get("base_score", 0.0)

    def eval_node(self, node, features):
        if "value" in node:
            return node["value"]
        f_idx = node["feature_index"]
        thresh = node["threshold"]
        if features[f_idx] <= thresh:
            return self.eval_node(node["left"], features)
        else:
            return self.eval_node(node["right"], features)

    def evaluate(self, features):
        raw = self.base_score + sum(self.eval_node(tree, features) for tree in self.trees) * self.learning_rate
        prob_up = 1.0 / (1.0 + math.exp(-raw)) if -100 < raw < 100 else (1.0 if raw >= 100 else 0.0)
        toxicity = abs(prob_up - 0.5) * 2.0
        is_toxic = toxicity >= self.toxicity_threshold

        spread_multiplier = 1.0 + (toxicity - self.toxicity_threshold) * 3.0 if is_toxic else 1.0
        return raw, prob_up, toxicity, is_toxic, spread_multiplier

class PythonBlackSwanDefense:
    def __init__(self, initial_depth=10.0, initial_vol=0.015):
        self.baseline_depth = max(1.0, initial_depth)
        self.baseline_vol = max(0.001, initial_vol)
        self.ema_alpha = 0.05

    def evaluate_shock(self, current_depth, current_vol, spread_bps):
        if current_depth > 0:
            self.baseline_depth = (1.0 - self.ema_alpha) * self.baseline_depth + self.ema_alpha * current_depth
        if current_vol > 0:
            self.baseline_vol = (1.0 - self.ema_alpha) * self.baseline_vol + self.ema_alpha * current_vol

        depth_ratio = current_depth / max(0.001, self.baseline_depth)
        vol_ratio = current_vol / max(0.0001, self.baseline_vol)

        # Emergency 1: Depth collapses below 15% of baseline or spread > 15 bps with vol spike
        if depth_ratio < 0.15 or (spread_bps > 15.0 and vol_ratio > 3.0):
            return "CIRCUIT_BREAKER", 4.0, True

        # Warning 2: Flash crash cascade (depth < 30% or vol > 2.5x)
        if depth_ratio < 0.30 or vol_ratio >= 2.5:
            mult = 2.0 + min(1.5, (vol_ratio - 1.0) * 0.8)
            return "FLASH_CRASH_WARNING", min(3.5, mult), False

        # Elevated Risk
        if depth_ratio < 0.50 or vol_ratio > 1.6:
            return "ELEVATED_RISK", 1.4, False

        return "NORMAL", 1.0, False

class BinanceRealTimeStream:
    def __init__(self, symbol="btcusdt", max_ticks=100, record_to_csv=True, simulate_news=True):
        self.symbol = symbol.lower()
        self.max_ticks = max_ticks
        self.record_to_csv = record_to_csv
        self.simulate_news = simulate_news

        self.ml_engine = RealTimeMLEngine()
        self.news_engine = MultilingualNewsAlphaEngine(default_half_life=45.0)
        self.black_swan = PythonBlackSwanDefense()

        # Seed sample breaking regional news if simulation mode is active
        if self.simulate_news:
            self.news_engine.evaluate_headline("업비트(Upbit) 신규 디지털 자산 거래 지원 안내", "ko", "UpbitWire")
            self.news_engine.evaluate_headline("某机构大额增持BTC完成链上确认", "zh", "WeChatAlpha")

        # Microstructure state tracking
        self.prev_bid_p = None
        self.prev_bid_q = None
        self.prev_ask_p = None
        self.prev_ask_q = None
        self.prev_mid = None
        self.rolling_ofi = 0.0
        self.recent_returns = collections.deque(maxlen=30)
        self.recent_trades = collections.deque(maxlen=50)

        # Output data storage
        os.makedirs("data", exist_ok=True)
        self.csv_path = f"data/realtime_{self.symbol}_ticks.csv"
        self.csv_file = None
        self.csv_writer = None
        if self.record_to_csv:
            file_exists = os.path.exists(self.csv_path)
            self.csv_file = open(self.csv_path, "a", newline="")
            self.csv_writer = csv.writer(self.csv_file)
            if not file_exists:
                self.csv_writer.writerow([
                    "timestamp_ns", "symbol", "bid_price", "bid_qty", "ask_price", "ask_qty",
                    "mid_price", "spread_bps", "micro_price_bias_bps", "imbalance_l0",
                    "ofi", "returns", "volatility", "trade_imbalance",
                    "raw_score", "prob_up", "toxicity", "is_toxic", "news_skew", "shock_regime", "final_spread_mult"
                ])

    async def run(self):
        ws_uri = f"wss://stream.binance.com:9443/stream?streams={self.symbol}@bookTicker/{self.symbol}@trade"
        print("=" * 105)
        print(f"  HYPERION QUANT: LIVE REAL-TIME FEED INGESTION + MULTILINGUAL NEWS ALPHA + BLACK SWAN DEFENSE [{self.symbol.upper()}]")
        print("=" * 105)
        print(f"[*] Connecting to live exchange WebSocket: {ws_uri}")
        print(f"[*] Target live ticks to process: {self.max_ticks if self.max_ticks else 'Continuous'}")
        print(f"[*] Recording clean microstructure features to: {self.csv_path}\n")

        tick_count = 0
        async with websockets.connect(ws_uri, ping_interval=20, ping_timeout=20) as ws:
            print("\x1b[1;32m[+] Connected successfully. Streaming live exchange data in real time...\x1b[0m\n")
            print(f"{'TIME (UTC)':<12} | {'BBO SPREAD':<18} | {'IMB L0':<7} | {'OFI':<6} | {'NEWS SKEW':<10} | {'SHOCK REGIME':<18} | {'DEFENDED QUOTES'}")
            print("-" * 125)

            while True:
                msg = await ws.recv()
                event = json.loads(msg)
                stream_name = event.get("stream", "")
                data = event.get("data", {})

                if "trade" in stream_name:
                    is_buyer_maker = data.get("m", False)
                    qty = float(data.get("q", 0.0))
                    signed_qty = -qty if is_buyer_maker else qty
                    self.recent_trades.append((time.time(), signed_qty))
                    continue

                if "bookTicker" in stream_name:
                    tick_count += 1
                    now_ns = time.time_ns()
                    bid_p = float(data["b"])
                    bid_q = float(data["B"])
                    ask_p = float(data["a"])
                    ask_q = float(data["A"])
                    mid = (bid_p + ask_p) / 2.0
                    bbo_depth = bid_q + ask_q

                    # 1. Spread (bps)
                    spread_bps = ((ask_p - bid_p) / mid) * 10_000.0

                    # 2. Level 0 Depth Imbalance
                    total_q = bid_q + ask_q
                    imbalance_l0 = (bid_q - ask_q) / total_q if total_q > 0 else 0.0

                    # 3. Micro-Price Bias (bps)
                    micro_price = (bid_q * ask_p + ask_q * bid_p) / total_q if total_q > 0 else mid
                    micro_price_bias_bps = ((micro_price - mid) / mid) * 10_000.0

                    # 4. Order Flow Imbalance (OFI)
                    delta_ofi = 0.0
                    if self.prev_bid_p is not None:
                        if bid_p > self.prev_bid_p:
                            delta_ofi += bid_q
                        elif bid_p == self.prev_bid_p:
                            delta_ofi += (bid_q - self.prev_bid_q)
                        else:
                            delta_ofi -= self.prev_bid_q

                        if ask_p < self.prev_ask_p:
                            delta_ofi -= ask_q
                        elif ask_p == self.prev_ask_p:
                            delta_ofi -= (ask_q - self.prev_ask_q)
                        else:
                            delta_ofi += self.prev_ask_q

                    self.rolling_ofi = 0.85 * self.rolling_ofi + 0.15 * delta_ofi

                    # 5. Returns & Instantaneous Volatility
                    ret = 0.0
                    if self.prev_mid is not None and self.prev_mid > 0:
                        ret = (mid - self.prev_mid) / self.prev_mid
                    self.recent_returns.append(ret)
                    vol = (math.sqrt(sum(r*r for r in self.recent_returns) / len(self.recent_returns)) * 100.0) if self.recent_returns else 0.01

                    # 6. Trade Flow Imbalance over past 2 seconds
                    now = time.time()
                    trade_imb = sum(vol for t, vol in self.recent_trades if now - t <= 2.0)

                    # Update history
                    self.prev_bid_p = bid_p
                    self.prev_bid_q = bid_q
                    self.prev_ask_p = ask_p
                    self.prev_ask_q = ask_q
                    self.prev_mid = mid

                    # Microstructure feature vector
                    features = [
                        spread_bps, micro_price_bias_bps, imbalance_l0, 0.0,
                        self.rolling_ofi, ret, vol, trade_imb
                    ]

                    # 1. Real-time ML Evaluation
                    raw_score, prob_up, toxicity, is_toxic, ml_spread_mult = self.ml_engine.evaluate(features)

                    # 2. Feature A: Multilingual News Alpha Reservation Skew
                    news_skew = self.news_engine.compute_current_alpha_skew(now_ns, current_volatility=vol)

                    # 3. Feature B: Real-time Black-Swan Liquidity Collapse Detection
                    shock_regime, shock_mult, emergency_pull = self.black_swan.evaluate_shock(bbo_depth, vol, spread_bps)

                    # Combined spread multiplier (widens to the maximum necessary defense)
                    final_spread_mult = max(ml_spread_mult, shock_mult)

                    # Avellaneda-Stoikov quoting defense with news skew
                    base_half_spread = (ask_p - bid_p) / 2.0
                    defense_half_spread = base_half_spread * final_spread_mult
                    reservation_price = mid + (raw_score * 0.15) + (news_skew * 0.20)
                    quoted_bid = reservation_price - defense_half_spread
                    quoted_ask = reservation_price + defense_half_spread

                    # Format display
                    time_str = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
                    bbo_str = f"${bid_p:.2f} x ${ask_p:.2f}"
                    
                    if emergency_pull:
                        quotes_str = "\x1b[1;31m[CIRCUIT BREAKER: ALL QUOTES PULLED]\x1b[0m"
                    else:
                        quotes_str = f"Bid ${quoted_bid:.2f} | Ask ${quoted_ask:.2f} (\x1b[1m{final_spread_mult:.2f}x\x1b[0m)"

                    shock_color = "\x1b[1;32mNORMAL\x1b[0m" if shock_regime == "NORMAL" else (
                        "\x1b[1;33m" + shock_regime + "\x1b[0m" if shock_regime != "CIRCUIT_BREAKER" else "\x1b[1;31m" + shock_regime + "\x1b[0m"
                    )

                    print(f"{time_str} | {bbo_str:<18} | {imbalance_l0:+6.2f} | {self.rolling_ofi:+5.1f} | {news_skew:+8.3f}$ | {shock_color:<27} | {quotes_str}")

                    # Record to CSV
                    if self.csv_writer:
                        self.csv_writer.writerow([
                            now_ns, self.symbol.upper(), bid_p, bid_q, ask_p, ask_q,
                            mid, spread_bps, micro_price_bias_bps, imbalance_l0,
                            self.rolling_ofi, ret, vol, trade_imb,
                            raw_score, prob_up, toxicity, int(is_toxic), news_skew, shock_regime, final_spread_mult
                        ])
                        if tick_count % 10 == 0:
                            self.csv_file.flush()

                    if self.max_ticks and tick_count >= self.max_ticks:
                        print("\n" + "=" * 105)
                        print(f"[+] Successfully captured & evaluated {tick_count} live real-time market ticks.")
                        print(f"[+] Microstructure dataset with News Alpha & Black-Swan Defense saved to: {self.csv_path}")
                        print("=" * 105 + "\n")
                        break

        if self.csv_file:
            self.csv_file.close()

if __name__ == "__main__":
    ticks_to_collect = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    streamer = BinanceRealTimeStream(symbol="btcusdt", max_ticks=ticks_to_collect, record_to_csv=True, simulate_news=True)
    asyncio.run(streamer.run())
