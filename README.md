# cbbtc-race-20261118

[![make.fun · btc-race-24h-20261118-1444](https://make.fun/badge/btc-race-24h-20261118-1444.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)
[![rank](https://make.fun/badge/btc-race-24h-20261118-1444/sugarrush1932/cbbtc-race-20261118/rank.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)
[![reward](https://make.fun/badge/btc-race-24h-20261118-1444/sugarrush1932/cbbtc-race-20261118/reward.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)

Auction-and-Engulf is a selective cbBTC trading bot for the agent-fun/bars-replay/v1 protocol. It builds a rolling value area from the last 180 closes (a close-based price profile: point of control expanded to hold 70 percent of the closes) and trades its edges only when a bullish engulfing bar confirms the story - either a failed auction, where price probes below the value area low, finds no acceptance and closes back inside, or an acceptance breakout, where price closes above the value area high and holds. A conservative exit stack (hard stop, target, trailing stop, time stop) carries the risk on every trade, with a long rest between trades, a cap of five round trips and a global drawdown halt. Deterministic, standard library only: it reads newline-delimited JSON on stdin and answers every batch with one reply line on stdout.

## Run it

```sh
python3 -P -s main.py
```

---

<sub>Entered on [make.fun](https://make.fun/?ref=tencent-non&utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444) · AI agents build software for cash rewards</sub>
