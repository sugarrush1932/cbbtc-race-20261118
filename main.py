"""Washout-and-Reclaim: a selective cbBTC bot for bars-replay/v1.

Design notes, from three earlier races on this harness: the dev window
is only a qualification gate (a bot must finish it above zero after
fees), and the live window rewards a small number of decisive trades,
not activity. A full-size round trip costs roughly 26 bps in fee,
slippage and price impact, so every entry has to be chasing a move
several times that size, and the position is sized like it matters.

The bot speaks the agent-fun/bars-replay/v1 line protocol: a hello
message first, then one batch per minute bar, each answered with one
reply carrying its orders, then done. It keeps no state outside the
run, reads no clock and no files, and decides only from the bars and
the account each batch reports, so two runs on the same tape agree
byte for byte.

Entry shapes, both long-only, at most one position at a time:

- Washout reclaim. Price has fallen hard over the last 45 bars (or
  harder over 140) and the current bar turns up with a real body:
  the fall has stalled and the first buyers are back. This is the
  capitulation trough the earlier entries hunted, restated tighter.
- Quiet-base spark. The prior day-part sat in a range under 0.55%
  and the current bar jumps at least 0.5% to close near its high:
  a flat tape's one decisive directional move, taken early.

Risk stack on every trade: a hard stop under the entry, a rebound
target, a trailing stop once the trade has run, a time stop so no
position drifts, a long rest between trades, a cap of five round
trips, and a global halt that flattens everything and stops trading
if equity falls 3% from its peak.
"""

import json
import sys

FAST = 45            # short washout window, in bars
SLOW = 140           # long washout window, in bars
FAST_DROP = 0.012    # 45-bar fall that counts as a washout
SLOW_DROP = 0.022    # 140-bar fall that counts as a washout
BOUNCE = 0.003       # rise off the recent low that confirms a reclaim
BASE_BARS = 24       # quiet-base window for the spark
BASE_RANGE = 0.0055  # widest range that still counts as quiet
SPARK_JUMP = 0.006   # one-bar jump that counts as a spark
SIZE_FRAC = 0.82     # share of cash committed per entry
STOP_FRAC = 0.0055   # hard stop below the entry price
TARGET_FRAC = 0.0135  # rebound target above the entry price
TRAIL_ARM = 0.0075   # gain that arms the trailing stop
TRAIL_FRAC = 0.0038  # trailing distance below the peak close
TIME_STOP = 200      # bars after which a held trade is closed
REST_BARS = 150      # bars rested after any exit
ENTRY_GAP = 200      # minimum bars between two entries
MAX_TRADES = 5       # round trips allowed over the whole window
HALT_DD = 0.03       # equity fall from peak that halts trading
SLIP_BPS = 200       # max_slippage_bps sent on every order
MIN_NOTIONAL = 1000000  # 1 USDC in quote units


class Bot:
    def __init__(self, hello):
        sym = hello["symbols"][0]
        self.symbol = sym["symbol"]
        self.price_scale = sym["price_scale"]
        self.qty_scale = sym["qty_scale"]
        self.quote_scale = hello["capital"]["quote_scale"]
        self.closes = []
        self.opens = []
        self.counter = 0
        self.was_holding = False
        self.entry_px = None
        self.peak_px = None
        self.held_bars = 0
        self.last_entry_bar = -10 ** 9
        self.rest_until = -1
        self.trades_done = 0
        self.peak_equity = None
        self.halted = False

    def _order_id(self):
        self.counter += 1
        return "wr-%d" % self.counter

    def _ret(self, n):
        if len(self.closes) <= n:
            return None
        then = self.closes[-1 - n]
        if then <= 0:
            return None
        return self.closes[-1] / then - 1.0

    def _entry_price(self, qty, cost_units):
        # Average price paid, in price-scale units, from the position's
        # recorded cost: cost is quote units for qty base units.
        return (cost_units * 10 ** (self.qty_scale + self.price_scale)
                // (qty * 10 ** self.quote_scale))

    def _washout(self, close):
        fast = self._ret(FAST)
        slow = self._ret(SLOW)
        deep = (fast is not None and fast <= -FAST_DROP) \
            or (slow is not None and slow <= -SLOW_DROP)
        if not deep:
            return False
        # A single up bar inside a slide is a pause, not a trough: the
        # close must already stand a real bounce above the lowest
        # close of the last dozen bars before the reclaim is believed.
        recent_low = min(self.closes[-12:])
        return recent_low > 0 and close >= recent_low * (1.0 + BOUNCE)

    def _spark(self, high, low, close):
        if len(self.closes) <= BASE_BARS:
            return False
        prior = self.closes[-1 - BASE_BARS:-1]
        top = max(prior)
        bottom = min(prior)
        if bottom <= 0 or (top - bottom) / bottom > BASE_RANGE:
            return False
        prev = self.closes[-2]
        if prev <= 0 or close / prev - 1.0 < SPARK_JUMP:
            return False
        if high <= low:
            return False
        # A true breakout closes through the base's top, not just
        # near its own bar high.
        return close > top and close >= low + 0.85 * (high - low)

    def _enter(self, cash):
        notional = cash * int(SIZE_FRAC * 10000) // 10000
        if notional < MIN_NOTIONAL:
            return []
        self.last_entry_bar = len(self.closes)
        return [{"side": "buy", "client_id": self._order_id(),
                 "symbol": self.symbol,
                 "notional_quote_units": str(notional),
                 "max_slippage_bps": SLIP_BPS}]

    def _leave(self, qty):
        return [{"side": "sell", "client_id": self._order_id(),
                 "symbol": self.symbol, "qty": str(qty),
                 "max_slippage_bps": SLIP_BPS}]

    def on_batch(self, batch):
        account = batch["account"]
        cash = int(account["cash_quote_units"])
        equity = int(account["equity_quote_units"])
        held = None
        for position in account["positions"]:
            if position["symbol"] == self.symbol and int(position["qty"]) > 0:
                held = position
        bar = None
        for candidate in batch["bars"]:
            if candidate["symbol"] == self.symbol:
                bar = candidate
        if bar is None:
            return []

        close = int(bar["c"])
        high = int(bar["h"])
        low = int(bar["l"])
        self.closes.append(close)
        self.opens.append(int(bar["o"]))
        now = len(self.closes)

        if self.peak_equity is None or equity > self.peak_equity:
            self.peak_equity = equity

        holding = held is not None
        if holding and not self.was_holding:
            self.entry_px = self._entry_price(
                int(held["qty"]), int(held["cost_quote_units"]))
            self.peak_px = close
            self.held_bars = 0
        if not holding and self.was_holding:
            self.trades_done += 1
            self.rest_until = now + REST_BARS
            self.entry_px = None
            self.peak_px = None
        self.was_holding = holding

        if self.halted:
            if holding:
                return self._leave(int(held["qty"]))
            return []

        if self.peak_equity and equity < self.peak_equity * (1.0 - HALT_DD):
            self.halted = True
            if holding:
                return self._leave(int(held["qty"]))
            return []

        if holding:
            qty = int(held["qty"])
            self.held_bars += 1
            if close > self.peak_px:
                self.peak_px = close
            entry = self.entry_px
            if entry:
                if close <= entry * (1.0 - STOP_FRAC):
                    return self._leave(qty)
                if close >= entry * (1.0 + TARGET_FRAC):
                    return self._leave(qty)
                if self.peak_px >= entry * (1.0 + TRAIL_ARM) \
                        and close <= self.peak_px * (1.0 - TRAIL_FRAC):
                    return self._leave(qty)
            if self.held_bars >= TIME_STOP:
                return self._leave(qty)
            return []

        if self.trades_done >= MAX_TRADES:
            return []
        if now < self.rest_until or now - self.last_entry_bar < ENTRY_GAP:
            return []
        if len(self.closes) < 3:
            return []
        prev_close = self.closes[-2]
        turned_up = close > prev_close and close > int(bar["o"])
        if turned_up and self._washout(close):
            return self._enter(cash)
        if self._spark(high, low, close):
            return self._enter(cash)
        return []


def main():
    bot = None
    stream = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        message = json.loads(line)
        kind = message.get("type")
        if kind == "hello":
            bot = Bot(message)
        elif kind == "batch":
            orders = bot.on_batch(message)
            reply = {"type": "reply", "seq": message["seq"],
                     "orders": orders}
            stream.write(json.dumps(reply, separators=(",", ":")) + "\n")
            stream.flush()
        elif kind == "done":
            return


if __name__ == "__main__":
    main()
