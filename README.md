# FactCheckDAO — AI-verified article fact-checking on GenLayer

A crowd-sourced news platform on GenLayer where submitted articles are automatically cross-referenced against trusted news feeds by AI validators, producing an on-chain verdict.

## Deployed

**Network:** GenLayer Bradbury Testnet (chain ID 4221)  
**Contract address:** [`0xC3730a386478D8d14Af84C025c8997C86cb7c827`](https://explorer-bradbury.genlayer.com/address/0xC3730a386478D8d14Af84C025c8997C86cb7c827)

## What it does

1. A user submits an article URL via `submit_article(url)`.
2. `resolve_article(article_id)` runs an LLM consensus round:
   - Each validator fetches the URL, extracts verifiable claims, then asks whether trusted fact-checking outlets (Reuters, Snopes, PolitiFact, AP Fact Check, FactCheck.org) corroborate, refute, or lack evidence
   - Validators independently agree or disagree via GenLayer `gl.vm.run_nondet` (leader/validator pattern)
   - If a single verdict reaches consensus (`SUPPORTED | REFUTED | INSUFFICIENT`), it's stored on-chain.
3. Anyone can read the verdict via `get_verdict(article_id)`.

## Why GenLayer

Fact-checking is not computable on-chain — it needs to fetch arbitrary web pages and reason about their text. This requires:

- **Web access** (call the URL)
- **LLM judgment** (decide whether the page supports the claim)
- **Consensus** (multiple independent validators must agree before writing)

This is exactly what GenLayer's Intelligent Contracts provide.

## Verified working on Bradbury

- `submit_article("https://example.com/test")` → tx [`0x817d5609…`](https://explorer-bradbury.genlayer.com/tx/0x817d56096da91f55b0dbe2c577dcf32c38012d5c0e90f9609db635e9595dec5b)
- `get_article("article-1")` returns `{"exists": true, "status": "PENDING", ...}`
- `total_articles()` returns `1` after one submission

## Contract API

| Method | Returns | What it does |
|---|---|---|
| `submit_article(url)` | article_id | Stores a pending article |
| `resolve_article(article_id)` | verdict token | Consensus round that fills the verdict |
| `get_article(article_id)` | JSON | Article + status |
| `get_verdict(article_id)` | JSON | Verdict + evidence excerpt |
| `list_pending` | JSON list | All PENDING articles |
| `total_articles` | u256 | Counter |
| `now` | ISO stamp | Sanity check |

## Test suite

6 direct-mode tests cover:

- Submit validation (must be a valid URL)
- `resolve_article` only runs once per article (immutable)
- `INSUFFICIENT` path (no trusted source hits)
- `list_pending` surfaces only unresolved entries
- `get_verdict` only returns stored data once resolved

```bash
pytest tests/direct/ -v
```
