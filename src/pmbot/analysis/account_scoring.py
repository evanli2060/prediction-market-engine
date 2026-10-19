"""Score public accounts by how often their positioning matched the outcome.

This is the offline half of the order-flow signal: run it over resolved
history, keep the accounts whose edge survives shrinkage, and hand the result
to `OrderFlowSignal.set_weights`. Accounts that are reliably wrong come out with
negative weights, which is how "fade this account" falls out of the same code.

Each account is scored once per market (its first trade), so a single large
position can't dominate the estimate.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass

from ..models import Event, MarketResolved, PublicTrade, Side


@dataclass(frozen=True)
class AccountScore:
    account: str
    markets: int
    hits: int
    accuracy: float
    shrunk_accuracy: float
    weight: float
    off_hours_share: float


def score_accounts(
    trades: Iterable[PublicTrade],
    outcomes: dict[str, Side],
    *,
    min_markets: int = 30,
    prior_strength: float = 30.0,
    is_off_hours: Callable[[float], bool] | None = None,
) -> dict[str, AccountScore]:
    first: dict[tuple[str, str], PublicTrade] = {}
    for t in trades:
        if t.market_id in outcomes:
            first.setdefault((t.account, t.market_id), t)

    per_acct: dict[str, list[PublicTrade]] = {}
    for (acct, _), t in first.items():
        per_acct.setdefault(acct, []).append(t)

    scores: dict[str, AccountScore] = {}
    for acct, ts in per_acct.items():
        n = len(ts)
        hits = sum(1 for t in ts if t.side is outcomes[t.market_id])
        shrunk = (hits + 0.5 * prior_strength) / (n + prior_strength)
        weight = max(-1.0, min(1.0, 2 * (shrunk - 0.5))) if n >= min_markets else 0.0
        off = sum(1 for t in ts if is_off_hours(t.ts)) / n if is_off_hours else 0.0
        scores[acct] = AccountScore(acct, n, hits, hits / n, shrunk, weight, off)
    return scores


def to_weights(scores: dict[str, AccountScore], min_abs_weight: float = 0.1) -> dict[str, float]:
    return {a: s.weight for a, s in scores.items() if abs(s.weight) >= min_abs_weight}


async def collect_history(stream: AsyncIterator[Event]) -> tuple[list[PublicTrade], dict[str, Side]]:
    trades: list[PublicTrade] = []
    outcomes: dict[str, Side] = {}
    async for ev in stream:
        if isinstance(ev, PublicTrade):
            trades.append(ev)
        elif isinstance(ev, MarketResolved):
            outcomes[ev.market_id] = ev.outcome
    return trades, outcomes
