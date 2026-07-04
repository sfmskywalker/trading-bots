"""Minimal client for the Freqtrade REST API (dry-run bot control)."""
import logging
import os

import requests

logger = logging.getLogger(__name__)


class FreqtradeClient:

    def __init__(self, base_url: str | None = None, username: str | None = None,
                 password: str | None = None):
        self.base_url = (base_url or os.environ["FT_API_URL"]).rstrip("/")
        self.username = username or os.environ.get("FT_API_USERNAME", "freqtrader")
        self.password = password or os.environ["FT_API_PASSWORD"]
        self._token: str | None = None

    def _login(self) -> None:
        resp = requests.post(
            f"{self.base_url}/api/v1/token/login",
            auth=(self.username, self.password),
            timeout=15,
        )
        resp.raise_for_status()
        self._token = resp.json()["access_token"]

    def _request(self, method: str, path: str, retry: bool = True, **kwargs):
        if self._token is None:
            self._login()
        resp = requests.request(
            method,
            f"{self.base_url}/api/v1/{path}",
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=30,
            **kwargs,
        )
        if resp.status_code == 401 and retry:
            self._token = None
            return self._request(method, path, retry=False, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def status(self) -> list[dict]:
        """Open trades."""
        return self._request("GET", "status")

    def profit(self) -> dict:
        return self._request("GET", "profit")

    def balance(self) -> dict:
        return self._request("GET", "balance")

    def force_enter(self, pair: str, stake_amount: float) -> dict:
        # Market orders so LLM decisions fill immediately instead of resting
        # as limit orders that may expire unfilled.
        return self._request("POST", "forceenter", json={
            "pair": pair,
            "side": "long",
            "stakeamount": stake_amount,
            "ordertype": "market",
        })

    def force_exit(self, trade_id: int) -> dict:
        return self._request("POST", "forceexit", json={
            "tradeid": str(trade_id),
            "ordertype": "market",
        })
