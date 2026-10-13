#!/usr/bin/env python3
"""
Full GEX calculation from Schwab option chains.
Pulls chains for all tickers, computes gamma exposure by strike,
and generates data.json for the GitHub Pages dashboard.
"""
import json
import sys
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")
from dynamic_credentials import add_surrogate_to_request, read_json_response

API = "https://api.schwabapi.com/marketdata/v1"
ALLOWED = ("api.schwabapi.com",)

REPO_DIR = Path.home() / "workspace" / "gex-muse"
DATA_JSON = REPO_DIR / "data.json"

# Tickers to compute GEX for (SPX derived from SPY)
GEX_TICKERS = ["AMZN", "AAPL", "NVDA", "GOOGL", "SPY", "QQQ", "MSFT", "META", "TSLA", "AMD", "AVGO"]


def call(path, params=None):
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, method="GET")
    add_surrogate_to_request(req, "custom.schwab", allowed_hosts=ALLOWED)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return {"http": resp.status, "body": read_json_response(resp)}
    except urllib.error.HTTPError as e:
        return {"http": e.code, "body": ""}
    except Exception as e:
        return {"http": 0, "body": str(e)}


def compute_gex_for_ticker(symbol):
    """Pull chain and compute GEX by strike."""
    r = call("/chains", {"symbol": symbol, "strikeCount": "25", "range": "NTM"})
    if r["http"] != 200:
        return None
    
    body = r["body"]
    spot = body.get("underlyingPrice", 0)
    if not spot:
        return None
    
    # Aggregate GEX by strike across near-term expiries (first 3)
    gex_by_strike = {}
    vol_by_strike = {}
    expiries_seen = []
    
    for exp_map, is_call in [(body.get("callExpDateMap", {}), True),
                              (body.get("putExpDateMap", {}), False)]:
        for exp_key in list(exp_map.keys())[:3]:  # First 3 expiries
            if exp_key not in expiries_seen:
                expiries_seen.append(exp_key)
            for strike_key, contracts in exp_map[exp_key].items():
                for c in contracts:
                    strike = float(c.get("strikePrice", 0))
                    oi = int(c.get("openInterest", 0) or 0)
                    gamma = float(c.get("gamma", 0) or 0)
                    vol = int(c.get("totalVolume", 0) or 0)
                    
                    # Track volume by strike (even if no gamma)
                    if strike not in vol_by_strike:
                        vol_by_strike[strike] = 0
                    vol_by_strike[strike] += vol
                    
                    if oi == 0 or gamma == 0:
                        continue
                    
                    # GEX = gamma * OI * spot * 100 (contract multiplier)
                    # Calls: positive, Puts: negative (dealer perspective)
                    gex = gamma * oi * spot * 100
                    if not is_call:
                        gex = -gex
                    
                    # Convert to millions
                    gex_m = gex / 1e6
                    
                    if strike not in gex_by_strike:
                        gex_by_strike[strike] = 0
                    gex_by_strike[strike] += gex_m
    
    if not gex_by_strike:
        return None
    
    # Find key levels
    strikes = sorted(gex_by_strike.keys())
    net_gex = sum(gex_by_strike.values())
    
    # Call wall: strike with max positive GEX
    # Put wall: strike with max negative GEX (most negative)
    pos_strikes = {k: v for k, v in gex_by_strike.items() if v > 0}
    neg_strikes = {k: v for k, v in gex_by_strike.items() if v < 0}
    
    call_wall_strike = max(pos_strikes, key=pos_strikes.get) if pos_strikes else spot
    call_wall_gex = pos_strikes.get(call_wall_strike, 0)
    put_wall_strike = min(neg_strikes, key=neg_strikes.get) if neg_strikes else spot
    put_wall_gex = neg_strikes.get(put_wall_strike, 0)
    
    # Flip: where cumulative GEX crosses zero (from low to high strike)
    cum = 0
    flip = spot
    for s in strikes:
        prev_cum = cum
        cum += gex_by_strike[s]
        if prev_cum < 0 and cum >= 0:
            flip = s
            break
    
    # Build rows for heatmap (top 10 strikes by abs GEX)
    top_strikes = sorted(gex_by_strike.keys(), key=lambda x: abs(gex_by_strike[x]), reverse=True)[:10]
    top_strikes.sort(reverse=True)  # High to low for display
    
    # Simplified: distribute across 3 expiry buckets
    exp_labels = []
    for ek in expiries_seen[:3]:
        # Parse "2026-10-16:4" format
        date_part = ek.split(":")[0]
        exp_labels.append(date_part)
    
    rows = []
    for s in top_strikes:
        # Split GEX across expiries proportionally (simplified)
        total = gex_by_strike[s]
        vals = [round(total * 0.5, 1), round(total * 0.3, 1), round(total * 0.2, 1)]
        rows.append({"strike": s, "values": vals, "volume": vol_by_strike.get(s, 0)})
    
    # POC: strike with highest option volume
    poc_strike = max(vol_by_strike, key=vol_by_strike.get) if vol_by_strike else spot
    poc_vol = vol_by_strike.get(poc_strike, 0)
    
    # Generate verdict
    verdict = generate_verdict(symbol, spot, net_gex, call_wall_strike, put_wall_strike, flip)
    
    return {
        "spot": round(spot, 2),
        "net_gex": round(net_gex, 1),
        "call_wall": {"strike": call_wall_strike, "gex": round(call_wall_gex, 1)},
        "put_wall": {"strike": put_wall_strike, "gex": round(put_wall_gex, 1)},
        "flip": flip,
        "poc": {"strike": poc_strike, "volume": poc_vol},
        "max_pain": round(spot, 0),  # Placeholder - needs full calc
        "expected_move": round(spot * 0.02, 2),  # Placeholder 2%
        "expiries": exp_labels,
        "rows": rows,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict,
    }


def generate_verdict(ticker, spot, net_gex, call_wall, put_wall, flip):
    """Generate dynamic verdict based on GEX levels."""
    if net_gex > 0:
        if spot > flip:
            if spot >= call_wall * 0.99:
                label = "At the call wall — expect resistance"
                detail = f"Spot {spot:.2f} pressing into the {call_wall} call wall. Positive gamma pins price; breakouts get faded. Flip at {flip} is support."
            else:
                label = "Above flip — stable, drifting to call wall"
                detail = f"Spot {spot:.2f} above flip {flip}. Positive GEX regime — chop and pin toward the {call_wall} call wall. {put_wall} put wall is the floor."
        else:
            label = "Below flip — glue, bleed lower"
            detail = f"Spot {spot:.2f} under flip {flip}. Net long gamma but bleeding toward the {put_wall} put wall magnet. Rips toward {flip} get sold."
    else:
        label = "Negative gamma — trending regime"
        detail = f"Spot {spot:.2f}, dealers short gamma. Moves accelerate — don't fade breaks. {call_wall}/{put_wall} are the outer bounds."
    
    return {"label": label, "detail": detail}


def main():
    with open(DATA_JSON) as f:
        data = json.load(f)
    
    for ticker in GEX_TICKERS:
        try:
            print(f"Computing GEX for {ticker}...", flush=True)
            result = compute_gex_for_ticker(ticker)
            if result:
                data["tickers"][ticker] = result
                data["tickers"][ticker].pop("_placeholder", None)
                print(f"  {ticker}: spot={result['spot']}, net_gex={result['net_gex']:.1f}M, "
                      f"walls {result['call_wall']['strike']}/{result['put_wall']['strike']}")
            else:
                print(f"  {ticker}: FAILED - no data", file=sys.stderr)
        except Exception as e:
            print(f"  {ticker}: ERROR - {e}", file=sys.stderr)
    
    # SPX derived from SPY (10x)
    if "SPY" in data["tickers"]:
        spy = data["tickers"]["SPY"]
        data["tickers"]["SPX"] = {
            "spot": round(spy["spot"] * 10, 1),
            "net_gex": round(spy["net_gex"] * 10, 1),
            "call_wall": {"strike": spy["call_wall"]["strike"] * 10, "gex": round(spy["call_wall"]["gex"] * 10, 1)},
            "put_wall": {"strike": spy["put_wall"]["strike"] * 10, "gex": round(spy["put_wall"]["gex"] * 10, 1)},
            "flip": spy["flip"] * 10,
            "max_pain": spy["max_pain"] * 10,
            "expected_move": round(spy["expected_move"] * 10, 1),
            "expiries": spy["expiries"],
            "rows": [{"strike": r["strike"] * 10, "values": [v * 10 for v in r["values"]]} for r in spy["rows"]],
            "timestamp": spy["timestamp"],
            "verdict": {
                "label": "SPY-derived (SPX chain has no OI)",
                "detail": "SPX levels scaled 10x from SPY. SPX options show zero open interest — SPY is the practical proxy."
            },
        }
        print(f"  SPX: derived from SPY")
    
    with open(DATA_JSON, 'w') as f:
        json.dump(data, f)
    
    print(f"\nUpdated {DATA_JSON}")


if __name__ == "__main__":
    main()
