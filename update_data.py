#!/usr/bin/env python3
"""
Generate data.json for the GitHub Pages GEX dashboard from live Schwab data.
Called by gex-heatmap-feed after successful snapshot ingestion.
"""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_DIR = Path.home() / "workspace" / "gex-muse"
DATA_JSON = REPO_DIR / "data.json"

# Tickers with live Schwab data (SPX derived from SPY)
LIVE_TICKERS = ["AMZN", "AAPL", "NVDA", "GOOGL", "SPY", "QQQ"]

def schwab_quote(symbol):
    """Get live quote from Schwab."""
    result = subprocess.run(
        ["python3", str(Path.home() / "workspace/skills/schwab/bin/schwab_api.py"),
         "quotes", symbol],
        capture_output=True, text=True, timeout=30
    )
    data = json.loads(result.stdout)
    return data["quotes"][symbol]

def schwab_chain(symbol, strike_count=10):
    """Get option chain from Schwab."""
    result = subprocess.run(
        ["python3", str(Path.home() / "workspace/skills/schwab/bin/schwab_api.py"),
         "chain", symbol, str(strike_count)],
        capture_output=True, text=True, timeout=60
    )
    return json.loads(result.stdout)

def compute_gex(chain_data, spot):
    """Compute simplified GEX levels from chain data."""
    # This is a placeholder - real GEX calc needs full chain with OI
    # For now, extract key levels from the chain
    return {
        "net_gex": 0,
        "call_wall": {"strike": 0, "gex": 0},
        "put_wall": {"strike": 0, "gex": 0},
        "flip": spot,
        "max_pain": spot,
        "expected_move": 0,
    }

def main():
    # Load existing data.json to preserve structure
    with open(DATA_JSON) as f:
        data = json.load(f)
    
    timestamp = datetime.now(timezone.utc).isoformat()
    
    for ticker in LIVE_TICKERS:
        try:
            quote = schwab_quote(ticker)
            spot = quote["last"]
            
            # Update spot price
            if ticker in data["tickers"]:
                data["tickers"][ticker]["spot"] = spot
                data["tickers"][ticker]["timestamp"] = timestamp
                # Clear placeholder flag if it was set
                data["tickers"][ticker].pop("_placeholder", None)
                print(f"{ticker}: spot={spot:.2f}")
            else:
                print(f"{ticker}: not in data.json, skipping")
                
        except Exception as e:
            print(f"{ticker}: FAILED - {e}", file=sys.stderr)
    
    # SPX derived from SPY (10x)
    if "SPY" in data["tickers"] and "SPX" in data["tickers"]:
        spy_spot = data["tickers"]["SPY"]["spot"]
        data["tickers"]["SPX"]["spot"] = round(spy_spot * 10, 1)
        data["tickers"]["SPX"]["timestamp"] = timestamp
        print(f"SPX: spot={spy_spot * 10:.1f} (derived from SPY)")
    
    # Write updated data.json
    with open(DATA_JSON, 'w') as f:
        json.dump(data, f)
    
    print(f"Updated {DATA_JSON}")

if __name__ == "__main__":
    main()
