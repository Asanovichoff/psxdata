---
title: Screen PSX Stocks with Python
description: Filter and screen all PSX-listed stocks by sector, index, and trading metrics using psxdata.
---

# Screen PSX Stocks with Python

psxdata gives you two screening tools: `tickers()` to list symbols by index, and `quote()` to get current price and sector data for any symbol.

## List all KSE-100 tickers

```python
import psxdata

kse100 = psxdata.tickers(index="KSE100")
print(f"KSE-100 has {len(kse100)} stocks")
print(kse100[:10])
```

## List all PSX symbols

```python
all_tickers = psxdata.tickers()   # ~1,000 symbols
print(f"PSX lists {len(all_tickers)} symbols")
```

## Get a live quote

```python
import psxdata

q = psxdata.quote("ENGRO")
print(q.T)   # transpose for easier reading
```

## Screen all KSE-100 stocks for a metric

```python
import psxdata
import pandas as pd

# Fetch quotes for all KSE-100 stocks
tickers = psxdata.tickers(index="KSE100")

rows = []
for t in tickers:
    q = psxdata.quote(t)
    if not q.empty:
        rows.append(q.iloc[0])

screen = pd.DataFrame(rows).reset_index(drop=True)
print(screen.columns.tolist())
print(screen.head())
```

!!! note
    Screener data refreshes every 15 minutes. psxdata caches the full screener
    and serves all subsequent `quote()` calls from that cache — so screening
    all 100 stocks costs only one network request.

## See also

- [`tickers()`](../api/client.md) — parameter reference
- [`quote()`](../api/client.md) — parameter reference
- [Historical Data guide](historical-data.md) — download prices for screened stocks
