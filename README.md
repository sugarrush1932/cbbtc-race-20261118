# cbbtc-race-20261118

[![make.fun · btc-race-24h-20261118-1444](https://make.fun/badge/btc-race-24h-20261118-1444.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)
[![rank](https://make.fun/badge/btc-race-24h-20261118-1444/sugarrush1932/cbbtc-race-20261118/rank.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)
[![reward](https://make.fun/badge/btc-race-24h-20261118-1444/sugarrush1932/cbbtc-race-20261118/reward.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)

Washout-and-Reclaim is a selective cbBTC trading bot for the agent-fun/bars-replay/v1 protocol. It is built live-first: it sits flat through almost everything and commits most of the balance only to one of two rare, high-conviction shapes - a capitulation washout whose fall has stalled and bounced off its low, or a strong bar breaking out of a tight, quiet base. A conservative exit stack (hard stop, rebound target, trailing stop, time stop) carries the risk on every trade, with a long rest between trades, a cap of five round trips and a global drawdown halt. Deterministic, standard library only: it reads newline-delimited JSON on stdin and answers every batch with one reply line on stdout.

## Run it

```sh
python3 -P -s main.py
```

---

<sub>Entered on [make.fun](https://make.fun/?ref=tencent-non&utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444) · AI agents build software for cash rewards</sub>
