"""
Institutional Quantitative Data Pipeline: Real Binance Tick Data Downloader
Downloads, extracts, and parses multi-day institutional tick-by-tick trade files
from public Binance Vision S3 repositories with zero authentication required.
"""

import io
import os
import sys
import zipfile
import requests
import polars as pl

BINANCE_VISION_BASE = "https://data.binance.vision/data/spot/daily/trades"

def download_daily_trades(symbol: str, date_str: str, out_dir: str = "data/raw") -> str:
    """
    Downloads and extracts a single day of institutional tick trades.
    Example date_str: '2025-01-15'
    """
    os.makedirs(out_dir, exist_ok=True)
    filename = f"{symbol}-trades-{date_str}.zip"
    csv_filename = f"{symbol}-trades-{date_str}.csv"
    csv_path = os.path.join(out_dir, csv_filename)

    if os.path.exists(csv_path):
        print(f"[*] Found cached trade data: {csv_path}")
        return csv_path

    url = f"{BINANCE_VISION_BASE}/{symbol}/{filename}"
    print(f"[*] Fetching real market tick trades from Binance Vision S3:\n    {url}")
    
    resp = requests.get(url, stream=True)
    if resp.status_code == 200:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            z.extractall(out_dir)
        print(f"[+] Successfully downloaded and unzipped: {csv_path}")
        return csv_path
    elif resp.status_code == 404:
        print(f"[-] Data not found on remote for date {date_str} (HTTP 404). Falling back to synthetic high-fidelity ticks.")
        return ""
    else:
        print(f"[-] Download error HTTP {resp.status_code}")
        return ""

def load_trades_polars(csv_path: str) -> pl.DataFrame:
    """
    Ingests CSV tick data using Polars multi-threaded SIMD parser.
    Columns: [trade_id, price, qty, quote_qty, time_ms, is_buyer_maker, is_best_match]
    """
    schema = {
        "trade_id": pl.Int64,
        "price": pl.Float64,
        "qty": pl.Float64,
        "quote_qty": pl.Float64,
        "time_ms": pl.Int64,
        "is_buyer_maker": pl.Boolean,
        "is_best_match": pl.Boolean,
    }
    
    print(f"[*] Parsing trades via Polars Apache Arrow engine...")
    df = pl.read_csv(csv_path, has_header=False, schema=schema)
    print(f"[+] Loaded {len(df):,} real trades into memory.")
    return df

if __name__ == "__main__":
    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    date = sys.argv[2] if len(sys.argv) > 2 else "2025-01-15"
    download_daily_trades(symbol, date)
