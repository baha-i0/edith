# EDITH — strategy

<!-- Owner: strategist. Created 2026-10-19. English. <=150 lines. Baha's decisions are marked (Baha, date); agents propose, never change them.
EDITH is a PERSONAL TOOL, not a store app: no downloads, no paywall, no marketing. Template sections are adapted:
"audience" = the one user, "competitors" = alternatives for the same money and time, "business model" = economics and success criteria. -->

## 1. One sentence
A Binance USDⓈ-M futures bot that Baha sets up once and never touches; it trades a back-tested 4h trend edge on 200-300 USDT, has zero running cost and switches itself off when the evidence turns against it.

## 2. Why does this tool exist?
- **The job:** "I want my money working for me without me making the decisions" (CLAUDE.md: "ben sadece gozlemciyim ve patronum"). Baha has no trading knowledge but a strong engineering mindset. He wants a system he can trust, not a hobby.
- **The original ask was impossible, and he was shown the numbers:** $20-50/day on 200-300 USDT. $20/day = $7,300/yr, which at ~19%/yr would need ~$37.8k of capital (CLAUDE.md). Never imply that EDITH closes this gap.
- **What he would do instead:** hold USDT or BTC, follow a Binance copy-trader, or pay for a bot SaaS (section 4). The first rule-set he brought (10x, -$40 stop / +$20 target; a "competitor bot" spec of RSI35+EMA200 on 15m) measured as a loser: -0.339R/trade, t=-7.39 (CLAUDE.md, README).
- **Why EDITH and nothing else:** every parameter carries its own measured reason, tested with pessimistic fills, real fees, funding and slippage, and a time split (config.example.yaml, README "Kanit"). Backtest/live parity is enforced by 254 tests. No LLM runs at runtime, so the running cost is $0 (CLAUDE.md). It has its own kill switch (health edge check, then shadow mode). None of the alternatives below give Baha all four.

## 3. User
- **Primary and only user:** Baha. Windows 11, PowerShell, Python 3.11. He watches through Telegram (@EDITHTraderBot) and the local panel on 127.0.0.1:8787, and reads everything in Turkish.
- **His behaviour risks are the real product risk.** He rejected the 2-week paper run as "mantikli gelmedi, hizlandiralim" (Baha, 2026-09-05). He ran a 12-month backtest, saw -4.7% and got confused (CLAUDE.md warning). Expect pressure to tweak the bot after a bad month.
- **Not for:** other users, selling signals, a SaaS or a public product. Those would need licences and a support load. Assumption: never wanted. If Baha ever raises it, treat it as a new decision.

## 4. Alternatives and gaps (same money, same "hands-off" job)
| Alternative | Cost model | What it does well | Where it fails for Baha | EDITH's difference |
|---|---|---|---|---|
| Do nothing: USDT in Binance Simple Earn | Free; variable APR, ~0.5-5% (third-party, undated: Datawallet, jeangalea.com) | Zero time, near-zero drawdown | Small yield, and still exchange custody risk | Higher expected return, bought with ~27% drawdowns |
| BTC/BNB buy-and-hold | Free | +72% / +77% over the README's 5-year window, zero effort | Alts fell 60-99% in the same window (README); no protection in bear markets | Stays positive in bear markets through shorts; its value is the bear market, not beating hold in a bull run (README) |
| Binance futures copy trading | Free to follow; lead trader takes a 10% profit share (The Block, 2023 launch) | One tap, no code | Leaderboard selection bias, no access to the trader's risk rules, the edge belongs to someone else (assumption) | Rules and evidence are inspectable; Baha owns them |
| Binance / Pionex grid bots | Free bots; Pionex futures fee 0.02-0.05% (Coin Bureau review, 2025-08) | Built in, no setup | Grid is a range strategy: it bleeds in trends, and leverage up to 50x is offered (third-party reviews) | Trend + breadth filter; leverage capped by stop distance and liquidation buffer (README) |
| 3Commas / Cryptohopper SaaS | Subscription; 3Commas Pro quoted at $37-75/mo in conflicting third-party sources, official page not checked | Polished UI, many exchanges | At $37/mo the fee is ~$444/yr, more than 2x Baha's whole $200 stake (estimate) | $0 running cost |
| Freqtrade (open source, Python) | Free | Mature backtester, Telegram, web UI, big community | Binance futures support is labelled "experimental"; steep learning curve; porting EDITH's measured edge = a rewrite | Already built, measured and audited twice |
Sources: WebSearch 2026-10-19 (toolradar, hackernoon, theblock.co, coinbureau.com, datawallet.com, Freqtrade docs via greptile/gainium). Prices change fast: re-check before using any number.
**The gap EDITH fills:** pessimistic, time-split evidence for small capital at $0/month. **Where every alternative wins:** time to set up. EDITH's real cost is Baha's hours, so guard those (section 6).

## 5. Operating stance and voice (how EDITH talks to Baha)
- **Claim:** "a measured small edge, not a money printer". README: about 10-15%/yr at 0.75% risk with drawdowns above 20%; at the current 1.5% risk, +29.1% CAGR backtest with a 26.7% max drawdown (CLAUDE.md, 2026-09-05).
- **Tone (canon from README/CLAUDE.md):** plain Turkish, numbers with confidence ranges, one daily report, no running P&L pings ("kotu kararlarin kaynagi"). "Bir sey yapma" during normal drawdowns.
- **Never say:** "gunde X$", "garanti", "belki olur" about the $20/day goal. Never quote the uncorrected t=4.39 without "real value ~1.5-2". Never quote 12-month numbers without the 60-month context.
- **Expectation facts every message must respect** (my run 2026-10-19 on the cached data to 2026-08, config.yaml at 1.5% risk, 356 trades; this re-run is mine, not a Baha-verified number):
  - 71 trades/yr, but **19 of 61 months had zero trades**; median 4/month. Silence is normal.
  - Longest losing streak: **17 trades**. Longest time below the equity peak: **314 days** (2024-09 to 2025-07).
  - Per trade: p10 -1.03R, median +0.38R, p90 +2.47R; mean +0.314R, sd 1.225R.

## 6. Economics and what success looks like
- **Money at stake:** 200-300 USDT (CLAUDE.md). The backtest CAGR of +29.1% on $200 is ~$58/yr *if* the edge holds (estimate). The last 12 months were negative: -4.7%/yr in Baha's run; mean -0.051R over 92 trades in mine. The dollar upside is small. What Baha is really buying is (a) an option to scale capital later and (b) a working, audited autonomous system.
- **Hidden cost: Baha's time.** Every hour on EDITH is an hour not spent on his store apps, which have a legal income route (GVK 20/B). Assumption to confirm with `legal-finance`: trading gains are NOT covered by the 20/B exemption, which is for app-development income.
- **Time to evidence (the core strategic fact):** bootstrapping my R distribution (seed 20261019, 20k resamples). If the backtest edge is real, cumulative R is still <= 0 after 10 trades 20% of the time, after 20 trades 13%, after 40 trades 5%, after 72 trades 1%.
  - Proving the edge is *positive* (t=2, trades independent) needs ~61 trades. Trades are correlated (real t ~1.5-2 vs 4.39), so it needs ~290-520 trades, **about 4-7 years** (estimate).
  - Conclusion: live results can flag "broken" in ~7 months; they cannot prove "works" for years. Judge EDITH on plumbing and parity, not on profit.
- **Success ladder (proposal, needs Baha's approval):**
  1. *Plumbing (testnet, hours):* forced restarts, partial fills and reconcile leave zero orphan positions.
  2. *Parity (testnet/paper, weeks):* live slippage and post_only fill rate are close to the backtest assumptions (2 bps slippage). The fixed-% exit idea dies above 10 bps (CLAUDE.md).
  3. *Edge watch (live, small, months):* review at 40 live trades (see section 8, Q2).
- **Gap in the kill switch:** health halts only at 50+ trades with z=2.33 (config.yaml `health`). With sd 1.225R, that fires only if mean R <= ~-0.40R (my calculation). Slow erosion to ~0R, which is what the last 12 months look like, never triggers it. The bot would keep paying fees on a dead edge.

## 7. Bets and experiments
| Date | Hypothesis | What we did | Result (metric) | Keep / drop |
|---|---|---|---|---|
| 2026-09-04 | Breadth filter (>=4 same-direction signals) removes noise | Portfolio backtest, 3 OOS checks | Win rate 45.4% -> 54.5%, max drawdown 18.6% -> 14.1%, expectancy 3x | Keep |
| 2026-09-04 | post_only + market fallback cuts fees | Backtest, both time halves | Fees -28% | Keep |
| 2026-09-05 | Risk 0.75% -> 1.5% (floor off) | 60 months, two halves | CAGR +13.4% -> +29.1%, max drawdown 14.1% -> 26.7% | Keep (Baha, 2026-09-05) |
| 2026-09-05 | Fixed exit +1.6% / -1.2% beats ATR exits | 60 months, robustness grid, slippage sweep | +48.5% CAGR, but +24.3% at 10 bps; last 12 months -6.5% | Undecided: test on testnet (Baha, 2026-09-05) |
| 2026-09-05..07 | Paper mode exercises the live loop | `bot paper`, ~8.5 h of loop time in total (511 equity rows, bot.db) | 0 trades (expected: median 4/month) | Inconclusive: too short |
| next | Testnet "torture session" finds plumbing bugs faster than weeks of paper | Testnet keys in .env (empty today) | Target: 0 orphan positions after kill/restart and partial fills | P0 |
Rejected and never to be re-pitched: short timeframes, same-direction caps, smaller targets, faster learning, post_only "skip if unfilled", the competitor RSI spec, the Windows service (Baha), the 2-week paper run (Baha). See the CLAUDE.md tables.

## 8. Open questions for Baha (each with a recommendation)
1. **What is EDITH for?** A) A learning and engineering project with small capital and capped time. B) An income source. Recommend A: at $200 the backtest upside is ~$58/yr (estimate) and profit cannot be proven for years.
2. **Human review for slow edge erosion?** A) Pre-commit now: at 40 live trades, if cumulative R <= 0, pause and decide (5% false-alarm rate if the edge is real). B) Rely on auto-shadow only. Recommend A: shadow cannot see erosion to ~0R.
3. **Drawdown alerts are stale for 1.5% risk.** health.py:288-298 still says the worst backtest drawdown was 21%, and `drawdown_critical_pct: 22` would send "MUDAHALE GEREKIYOR" inside the normal 26.7% range. A) Re-derive the thresholds from the 1.5% backtest. B) Leave them. Recommend A: false alarms push Baha to panic-tweak.
4. **Legal/tax gate before live money?** Turkey now requires SPK licensing for crypto providers. Binance stopped direct marketing in Turkey (ekonomim.com). Futures access for TR-resident accounts and the tax on gains are unverified. A) `legal-finance` check before any live key. B) Go live, check later. Recommend A.
5. **Time budget?** A) Maintenance-only: one testnet session, then observe; no new features without a 60-month time-split measurement. B) Keep building. Recommend A.
