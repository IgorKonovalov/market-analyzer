# Strategy authoring best practices

The longer-form correctness checklist. The SKILL.md highlights the three non-negotiables (lookahead, determinism, type hints); this file covers the failure modes in more depth.

## Lookahead bias — how it sneaks in

The strategy at bar `i` may only read data from indices `0..=i`. A backtest with lookahead is worse than no backtest — it produces confident-looking results that are statistically meaningless. Look for these patterns:

### 1. Pandas `.shift(-1)` (negative shift)

```python
df["next_close"] = df["close"].shift(-1)   # this is reading the FUTURE
```

`.shift(-1)` shifts the column backward — meaning at row `i`, you now see what *was* at row `i+1`. If you ever use `.shift(-N)` for any N, you have lookahead. The legitimate shift direction is `.shift(+1)` (last bar's close becomes available *next* bar).

### 2. Centered rolling windows

```python
df["rolling_max"] = df["high"].rolling(window=20, center=True).max()
```

A centered window at row `i` reads rows `i-10..i+10`. Lookahead. Always use trailing windows (the default for `.rolling()`, but check the indicator implementation — some are centered).

### 3. Reading the close of bar `i` to decide *at* bar `i`

Subtle: the bar's close is only known at the *end* of the bar. If the strategy is meant to act on close, the action happens at the *start of bar i+1*, not at bar `i`. The convention in this codebase:

> Strategy emits the signal at bar index `i`; the backtester executes the signal at bar `i+1`'s open.

So reading `bars[i].close` to decide a signal at bar `i` is fine, because the signal is interpreted as "at the end of bar i, fire — execute at bar i+1". As long as you don't read past `i`, you're safe.

What's **not** safe: reading `bars[i].close` and applying the trade *at* `bars[i].close` price — that's executing at the same instant you observed it, which assumes infinitely fast execution. The new contract pushes execution policy into the engine specifically to prevent strategies from getting this wrong.

### 4. Computing an indicator on the full series before the loop

This one is usually safe but worth verifying:

```python
closes = [b.close for b in bars]
rsi = calc_rsi(closes, period)        # whole series indicator
for i in range(len(bars)):
    if rsi[i] < threshold:            # using rsi[i] at bar i is fine IFF rsi[i] only depends on closes[0..i]
        ...
```

Trailing indicators (RSI, EMA, MACD, Bollinger, Donchian, supertrend) compute `output[i]` from `input[0..i]` only — safe. Centered or filtered indicators (some Kalman, some HP filters) compute `output[i]` from `input[i-k..i+k]` — unsafe in a backtest.

When in doubt, **read the indicator implementation in `indicators_calc.py`**. If it doesn't use future indices, you're safe.

## Determinism — the full list

Backtests must be byte-identical on re-run. Sources of non-determinism:

1. **`random.random()` / `np.random.rand()`** without explicit seeding from `Params`.
2. **`time.time()` / `datetime.now()` / `os.urandom`**.
3. **`dict.popitem()` / `set` iteration order** — Python 3.7+ `dict` preserves insertion order; `set` does not.
4. **`hash()` of strings** — Python 3 randomizes hash seeds across runs by default. Don't hash strings as part of strategy logic.
5. **Floating-point reduction order across threads** — strategies are single-threaded by design; never spawn threads from within `generate_signals`.
6. **File / network / env reads** — none allowed in strategy code.
7. **Mutable module-level state** — none allowed.

If your strategy *genuinely* needs randomness (e.g. a randomized indicator), expose `seed: int = Field(default=42)` in `Params` and use `random.Random(params.seed)` (not the module-level `random`).

## Parameter design

### Don't over-parameterize

A strategy with 12 params is a strategy that will be overfit on whatever data the user backtests it against. 2-4 params is the sweet spot. If you find yourself wanting a fifth, ask: "could this be a constant, or derived from another param?"

### Range constraints are correctness, not just UX

```python
period: int = Field(default=14, ge=2, le=200)
```

The `ge=2, le=200` isn't just for the UI form. It prevents the backtester from running with `period=0` (division by zero in the indicator) or `period=10_000_000` (allocates a huge array, times out). Always set bounds.

### Use `model_config = {"frozen": True}`

This makes `Params` immutable post-construction. Mutating params mid-backtest is a class of bug we don't want — freezing makes it a `TypeError` instead of a silent correctness failure.

### Field descriptions become UI tooltips

```python
period: int = Field(default=14, ge=2, le=200, description="RSI lookback in bars")
```

The `description` shows up in the UI's parameter form (`ui-builder` reads `Params.model_json_schema()`). Write descriptions for every field — they're free documentation.

### Inter-field constraints with `model_validator`

When two fields have a relationship (e.g. `fast_period < slow_period`), enforce it:

```python
from pydantic import model_validator

class Params(BaseModel):
    fast_period: int = Field(default=12, ge=2, le=100)
    slow_period: int = Field(default=26, ge=2, le=200)

    @model_validator(mode="after")
    def fast_must_be_less_than_slow(self) -> "Params":
        if self.fast_period >= self.slow_period:
            raise ValueError("fast_period must be < slow_period")
        return self
```

This validates at `Params` construction time, before `generate_signals` is even called.

## Common indicator gotchas

These come up in real strategy code; worth knowing:

- **`None` values in indicator output.** Trailing indicators return `None` for the first `period - 1` (or so) bars because the window hasn't filled. Always check `if rsi[i] is None: continue` (or equivalent) before using.
- **Lists vs arrays.** The in-house indicators return lists of floats with `None` for warmup. If you need NaN instead, convert with `[float("nan") if x is None else x for x in series]` — but be aware NaN comparisons are always `False`, which can silently break conditions.
- **EMA vs SMA convergence.** EMA never fully "warms up" — it gradually weights toward more recent data. The first few EMA values are heavily biased by the seed (often the first SMA value). For strategies that need a stable EMA, skip the first ~5×period bars.
- **MACD signal line lag.** The MACD signal line is an EMA of the MACD line, so a "macd-cross" signal at bar `i` reflects activity from several bars back. This is by design — don't try to "fix" it by reducing the signal period below 9 without thinking through what the change means.

## When to ship vs. when to flag

You produce code. You don't sign off on whether the strategy *makes sense* as a trading idea — that's the user's call. But you should flag obvious red flags:

- **The strategy is symmetric in suspicious ways** (e.g. enter when X > T, exit when X < T, where T is the *same threshold*). This usually means whipsaw in flat markets. Note it to the user but ship.
- **The strategy reads close to make decisions that get executed at close** — see the lookahead section. Refer to the engine's execution policy.
- **A `Params` field has no defensible default** (e.g. `magic_number: float = 1.234`). Either name it something meaningful, document why that value, or ask the user.

The skill's job is to produce correct, contract-conformant code. Whether the strategy *works* in the market is something the backtester reveals.
