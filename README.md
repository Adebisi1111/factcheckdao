# FactCheckDAO — AI-verified article fact-checking on GenLayer

A crowd-sourced news platform on GenLayer where submitted articles are automatically cross-referenced against trusted news feeds by AI validators, producing an on-chain verdict.

## What it does

1. A user submits an article URL via `submit_article(url)`.
2. `resolve_article(article_id)` runs an LLM consensus round:
   - Each validator fetches the URL, extracts verifiable claims, then asks whether trusted fact-checking outlets (Reuters, Snopes, PolitiFact, AP Fact Check, FactCheck.org) corroborate, refute, or lack evidence
   - Validators independently agree or disagree via GenLayer `_run_nondet` (leader/validator patterns)
   - If a single verdict reaches consensus (`SUPPORTED | REFUTED | INSUFFICIENT`), it's stored on-chain.
3. Anyone can read the verdict via `get_verdict(article_id)`.

## Why GenLayer

Fact-checking is not computable on-chain — it needs to fetch arbitrary web pages and reason about their text. This requires:

- Web access (call the URL).
- LLM judgment (decide whether the page supports the claim).
- Consensus (multiple independent validators must agree before writing).

This is exactly what GenLayer's Intelligent Contracts provide.

## Verified working

- Contract deployed on Studio dev (chain 61997) at `0x7ff4C31E36E9183045051351c048383C5a5ddA55`.
- Two articles submitted and resolved:
  - `article-1` → `INSUFFICIENT` (Reuters page had JS-block content - extractable claims were metadata only)
  - `article-2` → `INSUFFICIENT` (AP Huggingface page had insufficient factual content)

The verdict is `INSUFFICIENT` because neither source confirms the SUN-supported claim either way; both pages only serve JS placeholders when fetched.

## Live run history (verifiable on-chain)

| Method | Article | Verdict | Tx |
|---|---|---|---|
| `submit_article` | `https://www.reuters.com/fact-check/` | — | [`0x570e593b…`](https://explorer-studio-dev.genlayer.com/tx/0x570e593ba28d634e2bd291dff3059d1ab9db7e7dcdf415e8531bf5e842008993) |
| `resolve_article` | article-1 | INSUFFICIENT | [`0x1a0c8ece…`](https://explorer-studio-dev.genlayer.com/tx/0x1a0c8eceb99a27270149c6126075dcca8e23c06ebfd93c5d4277fecffec05838) |
| `submit_article` | `https://apnews.com/hub/ap-fact-check` | — | [`0x90a86030…`](https://explorer-studio-dev.genlayer.com/tx/0x90a860303a08b7627d441ac9d3b6d9bbfabcca6d8b2a6822248d91d8c270ca3d) |
| `resolve_article` | article-2 | INSUFFICIENT | [`0xc6993234…`](https://explorer-studio-dev.genlayer.com/tx/0xc699323497f9ec343787087d07923cc6149148eeffd0045f3f69e3e0b79df578) |

## Contract API

| Method | Returns | What it does |
|---|---|---|
| `submit_article(url)` | article_id | Stores a pending article |
| `resolve_article(article_id)` | verdict token | Consensus round that fills the verdict |
| `get_article(article_id)` | JSON | Article + status |
| `get_verdict(article_id)` | JSON | Verdict + evidence excerpt or warm |
| `list_pending` | JSON list | All PENDING articles |
| `total_articles` | u256 | Counter |
| `now` | ISO stamp | Sanity check |

## Runbook

```bash
genlayer network set studio-dev
genlayer deploy --contract contracts/factcheck_dao.py --fee-profile fee-profile.json
# note the deployed address
genlayer write <addr> submit_article --args https://example.com/article --fee-profile fee-profile.json
genlayer write <addr> resolve_article --args article-1 --fee-profile fee-profile.json
genlayer call <addr> get_verdict --args article-1
```

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
