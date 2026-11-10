# Contest bot spec (for field bots)

Contest: Deep-ML "No Limit: Pokerbot Tournament". No-limit hold'em, 8-player sit-and-go tables,
2,000 starting chips, blinds rise every 25 hands (assume 10/20, 15/30, 25/50, 50/100, 75/150, 100/200,
150/300, 200/400; no antes), 200-hand cap. Only the top three score: 10/5/2 points; at the cap,
remaining players get ICM equity. 1 ms of think time per decision: a late answer or an exception
becomes check (or fold when facing a bet). Python with numpy and PyTorch (CPU) allowed; no network.
Ranking: round reached in an elimination bracket, then average points per table.

## Interface (the local engine in ../engine.py)

Write a class with:
    get_action(self, state) -> ('fold',) | ('check',) | ('call',) | ('raise', total_bet_to)
    on_hand_end(self, info)            # optional, untimed; info = {'hand_number','history','shown','board','stacks'}

One bot instance is created per table and lives for the whole tournament.

`state` keys:
  seat           int, your seat (0..7)
  hole           (c1, c2) cards as ints 0..51: rank = c >> 2 (0='2' .. 12='A'), suit = c & 3
  board          list of 0/3/4/5 card ints
  street         0 preflop, 1 flop, 2 turn, 3 river
  pot            total chips in the pot, including all bets this street
  to_call        chips you must add to call
  stack          your chips behind (not counting your bet this street)
  bet            your bet so far this street
  current_bet    highest bet this street
  min_raise_to   smallest legal 'raise' total; max_raise_to = all-in total
  can_raise      bool
  small_blind, big_blind, button
  stacks         {seat: chips behind + this street's bet} for every seat dealt in
  bets           {seat: bet this street}
  folded, allin  lists of seats
  players_left   number of players still in the tournament (int)
  active_in_hand number of players not folded in this hand (int)
  players_behind number of players who act after you this street and can still act (int)
  hand_number    0-based
  history        list of (street, seat, kind, bet_to_after_action, is_allin);
                 kind in 'sb','bb','fold','check','call','raise'

A 'raise' amount is the TOTAL you want your bet this street to be (raise TO, not BY); it is clamped
to [min_raise_to, max_raise_to]. A raise you can't make becomes a call.

You may import ../cards.py (evaluate(cards) -> int, higher is better; straight_high(mask)) as your
hand evaluator. Do NOT read or import mybot*.py, preflop.py or bots.py's strategy code: build your
own strategy independently, like a real competitor.

## Testing

From the pokerbot folder:
    python3 field/check_bot.py field/<yourfile>.py
plays 40 tables against simple opponents and reports avg points, errors and think time.
