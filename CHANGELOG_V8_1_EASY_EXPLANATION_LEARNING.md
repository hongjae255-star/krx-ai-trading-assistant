# v8.1 Easy Explanation + Close Learning

## Telegram / website wording
- Recommendation messages now lead with `왜 뽑혔나` and translate strong model features into plain Korean.
- Replaced or supplemented jargon such as ATR, MFE/MAE, Rank-IC, RS proxy, Trend Template, Risk Regime with plain-language labels.
- Strategy cards show a short `왜 후보인가?` explanation before detailed metrics.
- Learning/history screen shows how many morning candidates were evaluated and what the model changed after the close.

## Close learning changes
- Weight learning now prefers the full frozen morning shadow candidate pool instead of only the final recommended picks.
- Uses actual same-day outcomes of those candidates, producing substantially more feedback samples per session.
- Uses cross-sectional reward/feature association so features that differentiated winners from losers get more weight.
- Keeps bounded daily weight changes and baseline decay for stability.
- Adds same-trading-day idempotency so retries/manual reruns do not learn the same close twice.
- Keeps a backward-compatible final-pick fallback for old databases.
- Saves plain-Korean daily learning lessons to state for Telegram/dashboard display.

## Important
This remains an adaptive statistical scoring system, not a guarantee of next-day returns. The predictive ensemble still uses walk-forward validation and only activates after its sample/date gates are met.
