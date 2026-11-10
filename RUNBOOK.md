# Contest-day runbook (60 minutes)

Rule of thumb: **get a valid entry in first, improve second.** A submission whose demo table fails does not count, and you have 3 submissions in the entry window.

## Before you click "Sign in to Join"
- [ ] Project is in a permanent folder (not this session's temporary folder).
- [ ] This chat is open and ready; paste the API docs / starter code here as soon as you see them.

## 0–10 min: read the API, ship a minimal entry
Paste into the chat: the starter code, the state/observation format, the action format, and any rules text
(blinds, antes, time limit, file/submission format). Claude then:
1. Writes `to_state()` in `contest_glue.py` (then `python3 build_submission.py`) using only the six required fields
   (hole, board, pot, to_call, stack, big_blind) plus `my_bet` if given.
2. Fills in `from_action()` with the right raise convention (`RAISE_MODE`: `to`, `by`, or `add`).
3. You submit it. **Goal: a counted entry by minute 10.**

Answer these from the docs (they decide the next steps):
- Does the bot see other players' actions (history)? → picks the settings file (see table below).
- Does it see other players' stacks and bets? Position / who acts after you?
- Is one bot object kept alive between hands? Is there an end-of-hand callback?
- Blind schedule, antes, starting stack, hands per level.
- Raise amount: total ("to"), increment ("by"), or chips to add?
- Single file? Allowed imports? Entry-point name?

## 10–30 min: full adapter + right settings
1. Extend `to_state()` with every extra field available (stacks, bets, folded, all-in, history, position).
2. Rebuild with the settings that match the information available:

| The API provides | Build command |
|---|---|
| Full action history + stacks | `python3 build_submission.py` |
| Stacks/bets but **no history** | `PARAMS=params/nohistory.json python3 build_submission.py` |
| Little beyond own cards/stack/pot | `PARAMS=params/minimal.json python3 build_submission.py` |

   The glue (`to_state` / `from_action`) lives in `contest_glue.py`, which every rebuild includes.
3. Set the real structure for testing, e.g.
   `export BLINDS='[[10,20],[20,40],...]' ANTE_FRAC=0 HANDS_PER_LEVEL=25 START_STACK=2000`

## 30–50 min: test
- Their demo / sample game, if provided, with the new file: **0 errors, every action legal.**
- `python3 leaderboard.py --pool ai --n 1000` and `--pool contest` under the real structure.
- If the real blind structure differs a lot from 10/20…200/400 every 25 hands, a short
  `python3 tune.py --resume --pools ai,contest,strong --iters 15 --n 300` (~5 min).
- Check `_bot.errors` stays 0 in any local run (hidden errors = silent folds).

## 50–60 min: submit
- Submit the tested file. Keep one submission in reserve.
- Write down: settings file used, raise mode, anything unusual in the API.

## Between bracket rounds (1 resubmission each)
- Bring back: your placings and anything visible about opponents' play.
- Re-weight `POOLS` in `bots.py` toward what you saw, re-tune briefly, change little.
