import pytest

from carry import backtest, config, engine, state

H8 = 8 * 3600 * 1000  # 8h in ms
H4 = 4 * 3600 * 1000  # 4h in ms


def events(rates, spacing_ms=H8, start=1_000_000_000_000):
    """N funding events at fixed spacing with the given per-period rates."""
    return [
        {"funding_time": start + i * spacing_ms, "funding_rate": r,
         "mark_price": 100.0}
        for i, r in enumerate(rates)
    ]


@pytest.fixture(autouse=True)
def shared(monkeypatch, tmp_path):
    monkeypatch.setenv("SHARED_DIR", str(tmp_path))


def test_annualized_pct_matches_hand_computed():
    # 0.0001 per 8h period, 3 periods/day -> 0.0001*3*365*100
    assert engine.annualized_pct(0.0001, 3.0) == pytest.approx(10.95)


def test_periods_per_day_infers_4h_and_8h_cadence():
    assert engine.periods_per_day(events([0.0] * 5, spacing_ms=H8)) == pytest.approx(3.0)
    assert engine.periods_per_day(events([0.0] * 5, spacing_ms=H4)) == pytest.approx(6.0)
    assert engine.periods_per_day(events([0.0])) == 3.0  # <2 events fallback


def test_entry_signal_requires_both_current_and_lookback():
    hist = events([0.0002] * 10)  # positive average, 8h cadence
    # current annualized 0.0002*3*365*100 = 21.9% >= 15, avg > 0 -> True
    assert engine.entry_signal(hist, 0.0002, 15.0, 3.0) is True
    # high current but negative recent average -> False
    neg = events([-0.0005] * 10)
    assert engine.entry_signal(neg, 0.0002, 15.0, 3.0) is False
    # low current rate -> False even with good history
    assert engine.entry_signal(hist, 0.00001, 15.0, 3.0) is False
    # empty history -> conservatively False
    assert engine.entry_signal([], 0.0002, 15.0, 3.0) is False


def test_exit_after_m_consecutive_bad_periods():
    assert engine.exit_signal(3, 3) is True
    assert engine.exit_signal(2, 3) is False


def test_bad_counter_resets_on_good_period():
    # exit_apr threshold 0 -> rates <= 0 are "bad"
    bad = engine.count_bad_periods(0, events([-0.0001, -0.0001]), 0.0)
    assert bad == 2
    # a good (positive) period resets the counter
    reset = engine.count_bad_periods(2, events([-0.0001, 0.0002, -0.0001]), 0.0)
    assert reset == 1


def test_open_position_fee_math():
    pos, event = engine.open_position(
        "BTC/USDT", 2000.0, 100.0, 100.5,
        config.SPOT_FEE_PCT, config.PERP_FEE_PCT, "2026-01-01T00:00:00+00:00")
    # 2000 * (0.10 + 0.05)/100 = 3.00
    assert pos["entry_fees_usdt"] == pytest.approx(3.0)
    assert event["pnl_usdt"] == pytest.approx(-3.0)
    assert event["type"] == "open"


def test_apply_settlements_credits_notional_times_rate():
    pos, _ = engine.open_position(
        "BTC/USDT", 2000.0, 100.0, 100.0, 0.1, 0.05,
        "2026-01-01T00:00:00+00:00", last_funding_time=500_000)
    evs = events([0.0001, 0.0002], start=1_000_000)
    updated, ledger = engine.apply_settlements(pos, evs)
    assert len(ledger) == 2
    assert ledger[0]["pnl_usdt"] == pytest.approx(2000 * 0.0001)
    assert updated["funding_usdt"] == pytest.approx(2000 * (0.0001 + 0.0002))
    assert updated["last_funding_time"] == evs[-1]["funding_time"]

    # Re-polling the same events is idempotent: nothing newer than last time.
    again, ledger2 = engine.apply_settlements(updated, evs)
    assert ledger2 == []
    assert again["funding_usdt"] == pytest.approx(updated["funding_usdt"])


def test_close_position_basis_pnl():
    # spot 100 -> 101 (long gains), perp 100.5 -> 101 (short loses as perp rises)
    pos, _ = engine.open_position(
        "BTC/USDT", 2000.0, 100.0, 100.5, 0.0, 0.0,
        "2026-01-01T00:00:00+00:00")
    _, event = engine.close_position(
        pos, 101.0, 101.0, 0.0, 0.0, "2026-01-02T00:00:00+00:00", "test")
    qty_spot = 2000.0 / 100.0
    qty_perp = 2000.0 / 100.5
    expected = qty_spot * (101.0 - 100.0) + qty_perp * (100.5 - 101.0)
    assert event["pnl_usdt"] == pytest.approx(expected)

    # Sign convention: short perp gains when perp falls.
    _, ev_fall = engine.close_position(
        pos, 100.0, 100.0, 0.0, 0.0, "2026-01-02T00:00:00+00:00", "test")
    assert ev_fall["basis_pnl_usdt"] > 0  # perp 100.5 -> 100 credits the short


def test_ledger_pnl_sums_to_balance_delta():
    balance = config.START_BALANCE_USDT
    pos, open_ev = engine.open_position(
        "BTC/USDT", 2000.0, 100.0, 100.0, 0.1, 0.05,
        "2026-01-01T00:00:00+00:00")
    balance += open_ev["pnl_usdt"]
    state.log_events([open_ev])

    evs = events([0.0001, 0.0002], start=1_000_000)
    pos, fund_evs = engine.apply_settlements(pos, evs)
    for e in fund_evs:
        balance += e["pnl_usdt"]
    state.log_events(fund_evs)

    _, close_ev = engine.close_position(
        pos, 101.0, 100.5, 0.1, 0.05, "2026-01-02T00:00:00+00:00", "test")
    balance += close_ev["pnl_usdt"]
    state.log_events([close_ev])

    ledger = state.read_ledger()
    assert len(ledger) == 4  # open + 2 funding + close
    total = sum(e["pnl_usdt"] for e in ledger)
    assert total == pytest.approx(balance - config.START_BALANCE_USDT)


def test_select_universe_volume_floor_and_ordering():
    tickers = [
        {"pair": "BTC/USDT", "quote_volume_24h": 5e8, "trades_24h": 1},
        {"pair": "LOW/USDT", "quote_volume_24h": 1e6, "trades_24h": 1},
        {"pair": "ETH/USDT", "quote_volume_24h": 9e8, "trades_24h": 1},
    ]
    universe = engine.select_universe(tickers, min_quote_volume=1e8)
    assert universe == ["ETH/USDT", "BTC/USDT"]  # LOW filtered, sorted desc


def test_backtest_simulate_pair_round_trip():
    cfg = {
        "entry_apr_pct": 15.0, "lookback_days": 3.0, "exit_apr_pct": 0.0,
        "exit_bad_periods": 3, "spot_fee_pct": 0.10, "perp_fee_pct": 0.05,
    }
    # Rich funding long enough to trigger entry, then 3 negative periods -> exit.
    rates = [0.0003] * 12 + [-0.0001, -0.0001, -0.0001]
    evs = events(rates)
    res = backtest.simulate_pair(evs, notional=2000.0, cfg=cfg)

    assert res["round_trips"] == 1
    # net == funding collected - round-trip fees (basis PnL excluded).
    assert res["net_usdt"] == pytest.approx(res["funding_usdt"] - res["fees_usdt"])
    # Round-trip fees = entry + exit = 2 * 2000 * (0.10+0.05)/100 = 6.00.
    assert res["fees_usdt"] == pytest.approx(6.0)
    assert res["funding_usdt"] > 0
