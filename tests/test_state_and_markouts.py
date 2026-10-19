import asyncio
import random

from pmbot.bot import TradingBot
from pmbot.config import BotConfig
from pmbot.engine import PaperExchange
from pmbot.models import BookUpdate, Fill, MarketOpened, OrderClosed, Side
from pmbot.state import UnderlyingState

from helpers import MON_10AM, book, market


def feed(u: UnderlyingState, jump_at: int | None = None) -> None:
    rng = random.Random(4)
    px = 100.0
    for i in range(1800):
        px *= 1 + rng.gauss(0, 1e-4)
        if i == jump_at:
            px *= 1.01  # one 100-sigma move
        u.on_tick(MON_10AM + i, px)


def test_bipower_ignores_a_single_jump_that_inflates_rv():
    clean_rv, jump_rv = UnderlyingState("BTC", 1800, estimator="rv"), UnderlyingState("BTC", 1800, estimator="rv")
    clean_bp, jump_bp = UnderlyingState("BTC", 1800), UnderlyingState("BTC", 1800)
    for u in (clean_rv, clean_bp):
        feed(u)
    for u in (jump_rv, jump_bp):
        feed(u, jump_at=900)
    assert abs(clean_bp.vol_per_s() / 1e-4 - 1) < 0.1
    assert jump_rv.vol_per_s() > 2 * clean_rv.vol_per_s()  # one jump more than doubles RV
    assert abs(jump_bp.vol_per_s() / clean_bp.vol_per_s() - 1) < 0.1


class Idle:
    async def events(self):
        return
        yield


def test_markouts_measure_the_book_after_each_fill():
    bot = TradingBot(BotConfig(), Idle(), PaperExchange())

    async def go():
        await bot.on_event(MarketOpened(MON_10AM - 300, market()))
        await bot.on_event(BookUpdate(MON_10AM, book(0.49, 0.51)))
        await bot.on_event(Fill(MON_10AM, 1, "M", Side.YES, 0.51, 10))
        await bot.on_event(Fill(MON_10AM, 2, "M", Side.NO, 0.51, 10))  # NO bought at 1 - 0.49
        await bot.on_event(OrderClosed(MON_10AM, 1, "M", 10))
        await bot.on_event(BookUpdate(MON_10AM + 4, book(0.53, 0.55, at=MON_10AM + 4)))
        await bot.on_event(BookUpdate(MON_10AM + 5, book(0.55, 0.57, at=MON_10AM + 5)))  # mid 0.56
        await bot.on_event(BookUpdate(MON_10AM + 31, book(0.59, 0.61, at=MON_10AM + 31)))  # mid 0.60

    asyncio.run(go())
    yes, no = bot.portfolio.trades
    assert yes.markouts == {5: 0.05, 30: 0.09}  # book moved toward YES
    assert no.markouts == {5: -0.07, 30: -0.11}
