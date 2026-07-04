# trading-bots — LLM-assisted crypto paper trading (ADA-first)

An experiment comparing two LLM-assisted trading approaches against each other
and against simply holding ADA. **Everything runs in paper-trading (dry-run)
mode against live Binance market data — no real money is at risk.**

## Honest expectations

No bot reliably produces daily income. In the one public real-money experiment
of LLM traders (Nof1 Alpha Arena), six frontier models ranged from **+23% to
−40%** over the same period. Our own baseline backtest (EMA/RSI trend on
2024-01 → 2026-07 data) returned **−15.95% vs a market change of −15.37%** —
i.e. it merely tracked the market. The deliverable of this project is
*evidence*: weeks of forward paper-trading data that shows whether either
approach beats buy-and-hold after fees. Only then is real capital worth
discussing.

## Architecture

| Service | What it does |
|---|---|
| `freqtrade-quant` (Bot A) | Freqtrade dry-run, EMA/RSI trend strategy gated by the advisor's market posture (`AdvisorGatedTrend`). FreqUI at http://127.0.0.1:8080 |
| `freqtrade-llm` (Bot B) | Freqtrade dry-run shell (`LlmSignalStrategy`): all entries come from the llm-trader; hard −5% stoploss and 7-day max hold enforced regardless. FreqUI at http://127.0.0.1:8081 |
| `advisor` | Every 4h asks Claude for a market posture (`risk_on/neutral/risk_off`, stake multiplier, veto pairs) → `shared/posture.json`. Fails safe to neutral. |
| `llm-trader` | Every hour asks Claude for up to 3 buy/sell/hold actions for Bot B. Every action passes code-level guardrails (max stake, max trades/day, daily loss cap, kill switch) before execution. |
| `comparison` | On-demand report: daily/weekly/monthly PnL of both bots vs ADA buy-and-hold, plus LLM API cost. |

Pairs traded: ADA/USDT (primary), BTC/USDT, ETH/USDT, SOL/USDT for context.

## Running it

```bash
cp .env.example .env        # add your ANTHROPIC_API_KEY
docker compose up -d --build
docker compose logs -f advisor llm-trader
```

FreqUI login: user `freqtrader`, password from `FT_API_PASSWORD` in `.env`.
Without an `ANTHROPIC_API_KEY`, Bot A trades with a neutral posture and the
llm-trader idles — the stack still runs.

**Kill switch:** `touch shared/KILL` stops the llm-trader from taking any
action; `rm shared/KILL` re-enables it.

### Reports

```bash
docker compose run --rm comparison        # CLI table + comparison-output/report.html
```

### Backtesting / data

```bash
docker compose run --rm --no-deps freqtrade-quant download-data \
  --config /freqtrade/user_data/config.json -t 1h --timerange 20240101-
docker compose run --rm --no-deps freqtrade-quant backtesting \
  --config /freqtrade/user_data/config.json --strategy BaselineTrend --timerange 20240101-
```

### Tests

```bash
cd services && python -m pytest tests/ -q
```

## Go-live gate (do not skip)

Real money only when **all** of these hold:

1. ≥ 2 months of continuous paper trading evidence.
2. At least one bot beats ADA buy-and-hold after fees over that window, with
   max drawdown you can articulate and accept.
3. Start with ≤ €500, on exchange API keys with **withdrawals disabled**.
4. Kill switch tested and monitoring/alerting in place.
5. You have read the CFTC advisory on AI trading bots and still want to.

## Costs

With the default `claude-opus-4-8`: the advisor runs 6×/day and the trader
24×/day with ~2-4k tokens per call — expect roughly $1–3/day. Set
`CLAUDE_MODEL=claude-haiku-4-5` in `.env` for a cheaper (weaker) experiment,
and adjust `CLAUDE_PRICE_INPUT_PER_MTOK`/`CLAUDE_PRICE_OUTPUT_PER_MTOK` so the
comparison report's cost estimate stays accurate.
