# Design notes

How the production system evolved, at the level of approach. Each stage is why some part of this codebase exists.

## 1. A single inefficiency

Short-duration crypto markets reprice off the underlying, but early on the books took a few seconds to catch up. A simple bot that priced the contract from the live underlying and bought whatever the book hadn't repriced yet was profitable.

→ `signals/lag_momentum.py`, `pricing.py`

## 2. The edge started decaying

More automated participants arrived and the window shrank. Returns didn't fall evenly: some hours held up, others turned negative, and late-window price action outside core hours became noticeably more adversarial. Breaking performance down by hour and by minute-in-window made this visible; trading less in the bad buckets recovered most of the loss.

→ `engine/regime.py`, the by-hour and by-minute tables in `analysis/report.py`, `entry_delay_s` / `entry_cutoff_s` in risk

## 3. More signals, combined carefully

- Correlated markets that resolve at the same moment carry information about each other.
- Scheduled macro announcements move the underlying faster than any automated signal can, so an operator override with a short TTL was cheaper than trying to model them.
- Once there were several signals, they disagreed. The aggregator normalizes by the weight of *enabled* signals (so one signal alone can't look strong), shrinks conflicting opinions, and requires the result to clear the spread.

→ `signals/cross_market.py`, `signals/manual.py`, `engine/aggregator.py`

## 4. The public tape

Some accounts were consistently right, often in specific hours. Some were consistently on the other side of those accounts. Scoring accounts on resolved history, once per market and with shrinkage toward 50%, turned both into signal: positive weights to follow, negative weights to fade.

→ `analysis/account_scoring.py`, `signals/order_flow.py`

## 5. Other people adapt too

Every visible pattern eventually got traded against: windows that worked attracted large counter-positioning, and followed accounts became targets themselves. The system needed to notice when a signal stopped working without waiting for a large drawdown, and that is the decay monitor's job. When enough of the signals were being competed away at once, the right move was to stop and exit.

→ `engine/monitor.py`

## Engineering constraints

The system ran on consumer hardware behind a VPN, so it couldn't win on raw speed. Instead:

- **Latency budget**: decisions that take too long are dropped instead of sent late.
- **Stale-data refusal**: no trading off a stale book or a stale underlying feed.
- **O(1) state updates**: running sums and deques, no history re-scans on the hot path.
- **Paper fills model latency explicitly**, so a strategy that only works with zero latency is caught in simulation.
- **Record/replay**: the same engine runs on captured sessions, so changes are tested on real conditions before going live.

## What reviewing the template turned up

These came from testing this repository against its own simulator, not from the production system.

- **Judge on markouts, not short-run P&L.** Across 10 seeds, two-day returns landed on both sides of zero whether or not the engine traded off-hours. Markouts 5 s after each fill were positive in core hours and negative off-hours in every seed. The decay monitor now judges signals on markouts, and with it the ungated engine switches the lag signal off for off-hours on its own.
- **Short-window volatility leaks edge.** A 5-minute realized-variance estimate let a single jump inflate volatility for minutes, making the fair price wrong exactly when it mattered. Swapping in the simulator's true volatility recovered most of the edge, which pointed at the estimator. A 30-minute bipower estimate, which mostly ignores isolated jumps, replaced it.
- **Count orders in flight.** With a slow venue (tested at 15 s latency) the engine could send a NO order while its YES order on the same market was still unfilled, then hold both sides. Risk now treats in-flight orders as exposure, checks limits at the worst price an order can fill at, and every order must end with an `OrderClosed`.
- **Stay out of the tails.** Without a price band, a handful of 5¢ contracts that paid $1 decided whole runs. Contracts outside 10–90¢ are now skipped: there, a small volatility error is a large probability error.

## What this template simplifies

- Paper fills don't consume displayed size.
- The simulator's dynamics are invented, not calibrated.
- No partial-fill order management, cancel/replace, or exits; positions are held to resolution, so markout edge is exposed to the binary outcome.
- Fees default to zero; set `ExecutionConfig.fee_rate` to the venue's current schedule.
