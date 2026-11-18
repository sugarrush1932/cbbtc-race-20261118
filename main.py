"""Auction-and-Engulf: a cbBTC bot for bars-replay/v1 built on price
auction logic confirmed by engulfing bars.

The idea comes from auction market theory. Over any stretch of tape
the market auctions price up and down until it finds the band where
most business gets done: the value area. Bars carry no volume on this
harness, so the profile here is built from closes instead: a rolling
histogram of the last 180 closes, its most populated bin the point of
control, expanded both ways until it holds 70 percent of the closes.
The edges of that band are the value area high (VAH) and value area
low (VAL), and they are the only map this bot trades against.

Two things can happen at an edge, and they mean opposite things:

- The auction fails. Price probes outside the value area, finds no
  acceptance there, and closes back inside. Only a capitulation
  probe counts: price must have fallen hard into it, and the snap
  back must reclaim at least half the value area. A probe below VAL
  like that is sellers exhausting themselves into a vacuum: nobody
  wanted to do business down there. That failure is worth buying,
  but only once a bullish engulfing bar proves buyers have actually
  taken the bar over: a green body that swallows the prior bar's
  body whole and closes through its high. A shallow probe in a
  steady grind down is just the trend continuing, and is left alone.
- The auction accepts. Closes hold above VAH - three of the last
  four - so the market is building value higher, and an engulfing
  bar that clears VAH decisively shows buyers pressing rather than
  covering. New value is being built higher, and the bot goes with
  it instead of fading it.

The harness is long only, so the mirror image of the failed auction
(a probe above VAH that falls back inside) is information, not a
trade: it cancels any pending breakout and nothing more.

Risk discipline is unchanged in spirit from earlier races on this
harness: one position at a time, sized at 82 percent of cash because
a full-size round trip costs about 26 bps and only decisive trades
clear it, a hard stop, a target, a trailing stop once the trade has
run, a time stop, a long rest between trades, at most five round
trips, and a global halt that flattens everything if equity falls
3 percent from its peak. The bot reads only the bars and the account
in each batch, keeps no state outside the run, and so replays byte
for byte.
"""

import json
import sys

VA_LOOKBACK = 180     # closes in the rolling price profile
VA_COVER = 0.70       # share of closes the value area must hold
BIN_FRAC = 0.0004     # profile bin width, as a share of price
MIN_HIST = 200        # bars seen before any entry is allowed
PROBE_MAX_BARS = 25   # closes below VAL before a probe is acceptance
PROBE_MAX_DEPTH = 0.016  # probe deeper than this is a repricing
PROBE_FALL = 0.009    # fall into the probe that makes it capitulation
CONFIRM_BARS = 2      # bars after re-entry to wait for the engulfing
ACCEPT_CLEAR = 0.001  # decisive clear of VAH an acceptance entry needs
CHASE_MAX = 0.008     # never chase acceptance further above VAH
ENGULF_MIN_BODY = 0.0006  # smallest engulfing body, share of price
SIZE_FRAC = 0.82      # share of cash committed per entry
STOP_FRAC = 0.006     # hard stop below the entry price
TARGET_FRAC = 0.014   # profit target above the entry price
TRAIL_ARM = 0.008     # gain that arms the trailing stop
TRAIL_FRAC = 0.004    # trailing distance below the peak close
TIME_STOP = 200       # bars after which a held trade is closed
REST_BARS = 150       # bars rested after any exit
ENTRY_GAP = 200       # minimum bars between two entries
MAX_TRADES = 5        # round trips allowed over the whole window
HALT_DD = 0.03        # equity fall from peak that halts trading
SLIP_BPS = 200        # max_slippage_bps sent on every order
MIN_NOTIONAL = 1000000  # 1 USDC in quote units


class Bot:
    def __init__(self, hello):
        sym = hello["symbols"][0]
        self.symbol = sym["symbol"]
        self.price_scale = sym["price_scale"]
        self.qty_scale = sym["qty_scale"]
        self.quote_scale = hello["capital"]["quote_scale"]
        self.opens = []
        self.highs = []
        self.lows = []
        self.closes = []
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
        # Failed-auction probe state (downside probes only: the
        # harness is long only, so an upside probe is not traded).
        self.probe_low = None
        self.probe_ref = None
        self.probe_out = 0
        self.probe_back_age = None
        # Acceptance state: True while closes are holding above
        # VAH (value being built higher), False otherwise.
        self.accepted = False

    def _order_id(self):
        self.counter += 1
        return "ae-%d" % self.counter

    def _entry_price(self, qty, cost_units):
        # Average price paid, in price-scale units, from the
        # position's recorded cost in quote units.
        return (cost_units * 10 ** (self.qty_scale + self.price_scale)
                // (qty * 10 ** self.quote_scale))

    def _value_area(self):
        # The value area of the closes before the current bar: the
        # contiguous run of price bins around the most populated bin
        # that together hold VA_COVER of the lookback's closes.
        if len(self.closes) < VA_LOOKBACK + 1:
            return None
        window = self.closes[-1 - VA_LOOKBACK:-1]
        ref = window[-1]
        if ref <= 0:
            return None
        width = max(1, int(ref * BIN_FRAC))
        counts = {}
        for close in window:
            cell = close // width
            counts[cell] = counts.get(cell, 0) + 1
        poc = None
        for cell in counts:
            if poc is None or counts[cell] > counts[poc]:
                poc = cell
        lo = poc
        hi = poc
        covered = counts[poc]
        goal = VA_COVER * len(window)
        while covered < goal:
            up = counts.get(hi + 1, 0)
            down = counts.get(lo - 1, 0)
            if up == 0 and down == 0:
                break
            if up >= down:
                hi += 1
                covered += up
            else:
                lo -= 1
                covered += down
        return lo * width, (hi + 1) * width

    def _bull_engulf(self, bar_idx):
        # True when the bar at bar_idx is a bullish engulfing bar
        # against its predecessor: a green body at least as wide as
        # the prior bar's body on both sides, a body with real size,
        # and a close through the prior bar's high.
        if bar_idx < 1:
            return False
        o = self.opens[bar_idx]
        c = self.closes[bar_idx]
        if c <= o:
            return False
        if c - o < c * ENGULF_MIN_BODY:
            return False
        po = self.opens[bar_idx - 1]
        pc = self.closes[bar_idx - 1]
        if o > min(po, pc):
            return False
        return c > self.highs[bar_idx - 1]

    def _enter(self, cash):
        notional = cash * int(SIZE_FRAC * 10000) // 10000
        if notional < MIN_NOTIONAL:
            return []
        self.last_entry_bar = len(self.closes)
        self.probe_low = None
        self.probe_ref = None
        self.probe_out = 0
        self.probe_back_age = None
        self.accepted = False
        return [{"side": "buy", "client_id": self._order_id(),
                 "symbol": self.symbol,
                 "notional_quote_units": str(notional),
                 "max_slippage_bps": SLIP_BPS}]

    def _leave(self, qty):
        return [{"side": "sell", "client_id": self._order_id(),
                 "symbol": self.symbol, "qty": str(qty),
                 "max_slippage_bps": SLIP_BPS}]

    def _flat_decision(self, cash, now):
        # Entry logic, run only when no position is held. Updates the
        # probe and breakout state from the bar just appended and
        # returns an entry order list or nothing.
        if self.trades_done >= MAX_TRADES:
            return []
        if now < self.rest_until or now - self.last_entry_bar < ENTRY_GAP:
            return []
        if len(self.closes) < MIN_HIST:
            return []
        area = self._value_area()
        if area is None:
            return []
        val, vah = area
        idx = len(self.closes) - 1
        close = self.closes[idx]
        low = self.lows[idx]

        # --- failed auction: a downside probe that came back ------
        # Only capitulation probes count: price must have fallen at
        # least PROBE_FALL into the probe from the closes before it,
        # and the confirming engulfing bar must close at least
        # halfway back through the value area. A shallow probe in a
        # grind down is the trend continuing, not an auction failing.
        if self.probe_low is None:
            if low < val:
                self.probe_low = low
                self.probe_ref = max(self.closes[-61:-1])
                self.probe_out = 1 if close < val else 0
                self.probe_back_age = None
        elif self.probe_back_age is None:
            if low < self.probe_low:
                self.probe_low = low
            if val - self.probe_low > val * PROBE_MAX_DEPTH:
                self.probe_low = None
                self.probe_ref = None
                self.probe_out = 0
            elif close < val:
                self.probe_out += 1
                if self.probe_out > PROBE_MAX_BARS:
                    self.probe_low = None
                    self.probe_ref = None
                    self.probe_out = 0
            elif close >= val:
                self.probe_back_age = 0
        else:
            self.probe_back_age += 1
            if self.probe_back_age > CONFIRM_BARS:
                self.probe_low = None
                self.probe_ref = None
                self.probe_out = 0
                self.probe_back_age = None
        if self.probe_low is not None and self.probe_back_age is not None:
            fell = self.probe_ref is not None \
                and self.probe_low <= self.probe_ref * (1.0 - PROBE_FALL)
            reclaimed = close >= (val + vah) // 2
            if fell and reclaimed and self._bull_engulf(idx):
                return self._enter(cash)

        # --- acceptance: closes holding above VAH -----------------
        # Acceptance is a state, not a single bar: once at least
        # three of the last four closes sit above VAH the market is
        # building value higher, and it stays accepted until a close
        # falls back inside. An engulfing bar that clears VAH
        # decisively (but is not yet a chase) is the entry.
        recent = self.closes[-4:]
        above = 0
        for past in recent:
            if past > vah:
                above += 1
        if close < vah:
            self.accepted = False
        elif above >= 3:
            self.accepted = True
        if self.accepted \
                and vah * (1.0 + ACCEPT_CLEAR) <= close \
                <= vah * (1.0 + CHASE_MAX) \
                and self._bull_engulf(idx):
            return self._enter(cash)
        return []

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

        self.opens.append(int(bar["o"]))
        self.highs.append(int(bar["h"]))
        self.lows.append(int(bar["l"]))
        self.closes.append(int(bar["c"]))
        now = len(self.closes)
        close = self.closes[-1]

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

        return self._flat_decision(cash, now)


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
