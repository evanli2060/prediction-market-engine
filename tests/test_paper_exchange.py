import asyncio

from pmbot.engine import PaperExchange
from pmbot.models import BookUpdate, Fill, MarketResolved, OrderClosed, OrderRequest, Side

from helpers import MON_10AM, book


def submit(ex, **kw):
    order = OrderRequest(
        **{
            "order_id": 1,
            "market_id": "M",
            "side": Side.YES,
            "limit_price": 0.52,
            "size": 150,
            "created_ts": MON_10AM,
            **kw,
        }
    )
    asyncio.run(ex.submit(order))


def fills_of(updates):
    return [u for u in updates if isinstance(u, Fill)]


def test_respects_latency():
    ex = PaperExchange(latency_s=0.8)
    submit(ex)
    assert ex.on_market_event(BookUpdate(MON_10AM + 0.5, book(0.49, 0.51, at=MON_10AM + 0.5))) == []
    out = ex.on_market_event(BookUpdate(MON_10AM + 1, book(0.49, 0.51, at=MON_10AM + 1)))
    assert fills_of(out)


def test_walks_levels_up_to_limit():
    ex = PaperExchange(latency_s=0)
    submit(ex, size=400)
    out = ex.on_market_event(BookUpdate(MON_10AM, book(0.49, 0.51)))
    assert [(f.price, f.size) for f in fills_of(out)] == [(0.51, 100), (0.52, 200)]  # 100 unfilled, IOC
    assert out[-1] == OrderClosed(MON_10AM, 1, "M", 300)  # closed after the fills, with filled size


def test_book_moved_through_limit_means_no_fill():
    ex = PaperExchange(latency_s=0.8)
    submit(ex)
    out = ex.on_market_event(BookUpdate(MON_10AM + 1, book(0.55, 0.57, at=MON_10AM + 1)))
    assert out == [OrderClosed(MON_10AM + 1, 1, "M", 0)]
    assert ex.unfilled == 1


def test_no_side_lifts_yes_bids():
    ex = PaperExchange(latency_s=0)
    submit(ex, side=Side.NO, limit_price=0.51, size=50)
    (fill,) = fills_of(ex.on_market_event(BookUpdate(MON_10AM, book(0.49, 0.51))))
    assert (fill.side, fill.price, fill.size) == (Side.NO, 0.51, 50)


def test_resolution_cancels_pending():
    ex = PaperExchange(latency_s=5)
    submit(ex)
    assert ex.on_market_event(MarketResolved(MON_10AM + 1, "M", Side.YES)) == [OrderClosed(MON_10AM + 1, 1, "M", 0.0)]
    assert ex.on_market_event(BookUpdate(MON_10AM + 10, book(0.49, 0.51, at=MON_10AM + 10))) == []
