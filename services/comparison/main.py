"""Nightly comparison report: Bot A vs Bot B vs ADA buy-and-hold.

Reads closed trades straight from each bot's dry-run SQLite database,
aggregates realized PnL per day/week/month, benchmarks against simply holding
ADA over the same window, and estimates the LLM API cost from the decision
logs. Prints a CLI table and writes a self-contained HTML report.
"""
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from common.market import fetch_klines
from common.util import read_jsonl, shared_dir

# Claude Opus 4.8 pricing per million tokens, override for other models.
PRICE_IN = float(os.environ.get("CLAUDE_PRICE_INPUT_PER_MTOK", "5.0"))
PRICE_OUT = float(os.environ.get("CLAUDE_PRICE_OUTPUT_PER_MTOK", "25.0"))

STARTING_BALANCE = 10_000.0


def load_closed_trades(db_path: str) -> pd.DataFrame:
    if not Path(db_path).exists():
        return pd.DataFrame(columns=["close_date", "close_profit_abs"])
    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql_query(
            "SELECT close_date, close_profit_abs FROM trades "
            "WHERE is_open = 0 AND close_date IS NOT NULL", conn)
    df["close_date"] = pd.to_datetime(df["close_date"])
    return df


def _coerce_at(value) -> pd.Timestamp:
    """Carry ledger 'at' is mixed: funding events use int ms (fundingTime),
    open/close events use ISO strings. Coerce both to UTC timestamps."""
    if isinstance(value, (int, float)):
        return pd.to_datetime(value, unit="ms", utc=True)
    return pd.to_datetime(value, utc=True)


def load_carry_trades() -> pd.DataFrame:
    """shared/carry_ledger.jsonl -> DataFrame[close_date, close_profit_abs, type].
    Every event's pnl_usdt contributes to realized PnL; 'type' is kept so the
    summary can count only 'close' rows as trades. Empty frame if absent."""
    records = read_jsonl(shared_dir() / "carry_ledger.jsonl")
    if not records:
        return pd.DataFrame(columns=["close_date", "close_profit_abs", "type"])
    df = pd.DataFrame({
        "close_date": [_coerce_at(r["at"]) for r in records],
        "close_profit_abs": [r["pnl_usdt"] for r in records],
        "type": [r.get("type") for r in records],
    })
    return df


def pnl_by_period(trades: pd.DataFrame, freq: str) -> pd.Series:
    if trades.empty:
        return pd.Series(dtype=float)
    return trades.set_index("close_date")["close_profit_abs"].resample(freq).sum()


def buy_and_hold_series(since: datetime) -> pd.Series:
    """Daily value of STARTING_BALANCE fully in ADA since `since`."""
    days = max((datetime.now(timezone.utc) - since).days + 2, 2)
    df = fetch_klines("ADA/USDT", interval="1d", limit=min(days, 1000))
    df["date"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df[df["date"] >= since]
    if df.empty:
        return pd.Series(dtype=float)
    closes = df.set_index("date")["close"]
    return STARTING_BALANCE * closes / closes.iloc[0]


def llm_cost_usd() -> float:
    tokens_in = tokens_out = 0
    for log in ("advisor_log.jsonl", "llm_trader_decisions.jsonl",
                "scout_log.jsonl", "postmortem_log.jsonl"):
        for rec in read_jsonl(shared_dir() / log):
            usage = rec.get("usage") or {}
            tokens_in += usage.get("input_tokens", 0)
            tokens_out += usage.get("output_tokens", 0)
    return tokens_in / 1e6 * PRICE_IN + tokens_out / 1e6 * PRICE_OUT


def summarize(name: str, trades: pd.DataFrame) -> dict:
    total = trades["close_profit_abs"].sum() if not trades.empty else 0.0
    # Ledger-style frames (carry) mix event types; count only closes as trades.
    closed = (int((trades["type"] == "close").sum()) if "type" in trades.columns
              else len(trades))
    return {
        "bot": name,
        "closed_trades": closed,
        "realized_pnl_usdt": round(total, 2),
        "realized_pnl_pct": round(total / STARTING_BALANCE * 100, 2),
    }


def render_html(daily: pd.DataFrame, summary: list[dict], cost: float,
                out_path: Path) -> None:
    cumulative = daily.fillna(0).cumsum()
    w, h, pad = 900, 380, 50
    lo = min(cumulative.min().min(), 0) if not cumulative.empty else -1
    hi = max(cumulative.max().max(), 0) if not cumulative.empty else 1
    span = (hi - lo) or 1
    colors = {"quant-bot": "#2f81f7", "llm-bot": "#d29922", "freqai-bot": "#a371f7",
              "carry-bot": "#f778ba", "ADA hold": "#3fb950"}

    def polyline(series: pd.Series) -> str:
        n = max(len(series) - 1, 1)
        pts = [
            f"{pad + i / n * (w - 2 * pad):.1f},"
            f"{h - pad - (v - lo) / span * (h - 2 * pad):.1f}"
            for i, v in enumerate(series.values)
        ]
        return " ".join(pts)

    lines = "".join(
        f'<polyline fill="none" stroke="{colors.get(col, "#999")}" stroke-width="2" '
        f'points="{polyline(cumulative[col])}"/>' for col in cumulative.columns
    )
    legend = " &nbsp; ".join(
        f'<span style="color:{colors.get(c, "#999")}">&#9632; {c}</span>'
        for c in cumulative.columns)
    rows = "".join(
        f"<tr><td>{s['bot']}</td><td>{s['closed_trades']}</td>"
        f"<td>{s['realized_pnl_usdt']}</td><td>{s['realized_pnl_pct']}%</td></tr>"
        for s in summary)

    out_path.write_text(f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Trading bots comparison</title>
<style>body{{font-family:system-ui;background:#0d1117;color:#e6edf3;padding:2rem}}
table{{border-collapse:collapse;margin:1rem 0}}td,th{{border:1px solid #30363d;padding:.4rem .8rem}}
</style></head><body>
<h1>Paper-trading comparison</h1>
<p>Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC &middot;
Estimated LLM API cost so far: ${cost:.2f}</p>
<table><tr><th>Bot</th><th>Closed trades</th><th>Realized PnL (USDT)</th><th>PnL %</th></tr>
{rows}</table>
<h2>Cumulative PnL (USDT)</h2><p>{legend}</p>
<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}">
<line x1="{pad}" y1="{h - pad - (0 - lo) / span * (h - 2 * pad):.1f}"
      x2="{w - pad}" y2="{h - pad - (0 - lo) / span * (h - 2 * pad):.1f}"
      stroke="#30363d"/>{lines}</svg>
</body></html>""")


def main() -> None:
    quant = load_closed_trades(os.environ.get("QUANT_DB", "/data/quant/tradesv3.dryrun.sqlite"))
    llm = load_closed_trades(os.environ.get("LLM_DB", "/data/llm/tradesv3.dryrun.sqlite"))
    freqai = load_closed_trades(os.environ.get("FREQAI_DB", "/data/freqai/tradesv3.dryrun.sqlite"))
    carry = load_carry_trades()
    if not carry.empty:  # align tz with the (naive) freqtrade close_dates
        carry["close_date"] = carry["close_date"].dt.tz_localize(None)

    all_dates = pd.concat([
        quant["close_date"], llm["close_date"], freqai["close_date"],
        carry["close_date"],
    ])
    start = (all_dates.min().tz_localize("UTC") if not all_dates.empty
             and all_dates.min().tzinfo is None else all_dates.min()) \
        if not all_dates.empty else datetime.now(timezone.utc)

    daily = pd.DataFrame({
        "quant-bot": pnl_by_period(quant, "D"),
        "llm-bot": pnl_by_period(llm, "D"),
        "freqai-bot": pnl_by_period(freqai, "D"),
        "carry-bot": pnl_by_period(carry, "D"),
    })
    try:
        hold = buy_and_hold_series(start if isinstance(start, datetime) else start.to_pydatetime())
        if not hold.empty:
            # benchmark expressed as daily PnL so it composes with bot PnL columns
            daily["ADA hold"] = hold.diff().reindex(daily.index.tz_localize("UTC")
                                                    if daily.index.tz is None else daily.index)
    except Exception as exc:
        print(f"(benchmark unavailable: {exc})")

    summary = [summarize("quant-bot (Bot A)", quant), summarize("llm-bot (Bot B)", llm),
               summarize("freqai-bot (Bot C)", freqai),
               summarize("carry-bot (Bot D)", carry)]
    cost = llm_cost_usd()

    print("\n=== Paper-trading comparison ===")
    for s in summary:
        print(f"{s['bot']:22} trades={s['closed_trades']:<4} "
              f"PnL={s['realized_pnl_usdt']:>10} USDT ({s['realized_pnl_pct']}%)")
    print(f"Estimated LLM API cost: ${cost:.2f}")

    for freq, label in (("D", "daily"), ("W", "weekly"), ("ME", "monthly")):
        table = pd.DataFrame({
            "quant-bot": pnl_by_period(quant, freq),
            "llm-bot": pnl_by_period(llm, freq),
            "freqai-bot": pnl_by_period(freqai, freq),
            "carry-bot": pnl_by_period(carry, freq),
        }).round(2)
        if not table.empty:
            print(f"\n--- {label} realized PnL (USDT) ---")
            print(table.tail(12).to_string())

    out_dir = Path(os.environ.get("OUTPUT_DIR", "/output"))
    out_dir.mkdir(parents=True, exist_ok=True)
    render_html(daily, summary, cost, out_dir / "report.html")
    print(f"\nHTML report written to {out_dir / 'report.html'}")


if __name__ == "__main__":
    main()
