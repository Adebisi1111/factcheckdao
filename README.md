# FactCheckDAO

**Live App:** [https://adebisi1111.github.io/factcheckdao/](https://adebisi1111.github.io/factcheckdao/)
**Contract:** [`0xa0213ed1883a18b44B149510885395e0BDc98858`](https://explorer-bradbury.genlayer.com/address/0xa0213ed1883a18b44B149510885395e0BDc98858) on GenLayer Bradbury Testnet

A GenLayer Intelligent Contract for decentralized, source-backed fact-checking. Given an article URL, a committee of AI validators extracts a factual claim, **retrieves a real corroborating source from the web**, and reaches consensus on a verdict — **SUPPORTED**, **REFUTED**, or **INSUFFICIENT** — backed by the retrieved evidence, not the model's own knowledge.

---

## Why it matters

A fact-check is only as trustworthy as the evidence behind it. A bare "true/false" label produced by an LLM from its training data is unverifiable — a reader cannot tell whether real sources were consulted. FactCheckDAO closes that gap: every verdict is accompanied by a **retrieved, on-chain record** of the source that backs it, and validators reach consensus on that evidence.

---

## How it works

```
submit_article(url)
        │
        ▼
  ┌──────────────┐   leader (nondet round)
  │ resolve_article │ ── 1. fetch the article (gl.nondet.web.request)
  └──────────────┘      2. extract ONE verifiable claim + search terms (exec_prompt)
        │               3. FETCH a corroborating source from the web
        │               4. judge the claim from the retrieved excerpt → verdict
        ▼
   validators agree on the retrieved evidence via gl.vm.run_nondet
        │
        ▼
  get_verdict(id) → { verdict, claim, source_url, evidence, source_fetched }
```

1. **Submit** — `submit_article(url)` stores the article on-chain with status `PENDING`.
2. **Resolve** — `resolve_article(article_id)` runs a `gl.vm.run_nondet` consensus round. The **leader** fetches the article, extracts a verifiable claim, retrieves a corroborating source, and judges the claim from the retrieved excerpt. **Validators** verify the leader's result is well-formed and self-consistent (claim present, source recorded, valid verdict) — consensus holds on that validated, evidence-backed result.
3. **Read** — `get_verdict(article_id)` returns the verdict together with the recorded claim, source URL, retrieved evidence excerpt, and a `source_fetched` flag.

### Verdict states

| State | Meaning |
|---|---|
| `SUPPORTED` | The retrieved source excerpt confirms the claim |
| `REFUTED` | The retrieved source excerpt contradicts the claim |
| `INSUFFICIENT` | No usable source excerpt was retrieved to judge the claim |

Each record carries `source_fetched: true/false`, so a verdict is never silently based on model knowledge when a source could not be retrieved.

---

## Consensus design

`gl.nondet.web.request` only executes inside a nondeterministic round, so the retrieval happens in the `run_nondet` leader. Rather than have every validator independently re-fetch live web content — which is nondeterministic and slow, and caused consensus to disagree or time out — the **leader retrieves once** and **validators validate the result**. This makes the round fast and reliable while still requiring validators to agree on evidence-backed output.

---

## Contract

| Property | Value |
|---|---|
| Network | GenLayer Bradbury Testnet |
| Chain ID | 4221 |
| Address | `0xa0213ed1883a18b44B149510885395e0BDc98858` |
| Explorer | [View on Bradbury Explorer](https://explorer-bradbury.genlayer.com/address/0xa0213ed1883a18b44B149510885395e0BDc98858) |
| Runner | `py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6` |

### API

| Method | Returns | Description |
|---|---|---|
| `submit_article(url)` | `article_id` | Store a pending article on-chain |
| `resolve_article(article_id)` | `verdict` | Run the retrieval + consensus round |
| `get_article(article_id)` | JSON | Article details and status |
| `get_verdict(article_id)` | JSON | Verdict + claim + source + evidence |
| `list_pending` | JSON list | All PENDING articles |
| `total_articles` | `u256` | Global article counter |
| `count_pending` | `u256` | Number of pending articles |
| `now` | ISO string | Current timestamp |

---

## Verified on-chain

A live run against `https://example.com` reached consensus and committed:

- **Claim:** *"The .example top-level domain is reserved for documentation and illustrative purposes."*
- **Retrieved source:** `https://en.wikipedia.org/w/index.php?search=...` — `source_fetched: true`
- **Evidence (recorded on-chain):** the actual Wikipedia response text
- **Verdict:** `INSUFFICIENT` (the fetched page did not corroborate the claim)

Consensus reached `AGREE` on the first round; the verdict and its retrieved evidence are readable via `get_verdict` and the explorer.

---

## How to run

**Prerequisites:** a Web3 wallet (MetaMask, Rabby) on Bradbury Testnet (chain 4221) with GEN for gas — faucet: [testnet-faucet.genlayer.foundation](https://testnet-faucet.genlayer.foundation).

1. Open the [live app](https://adebisi1111.github.io/factcheckdao/).
2. Connect your wallet (it auto-switches to Bradbury).
3. Paste an article URL and submit.
4. Run verification — the app polls for consensus and displays the verdict with its recorded source.

### CLI

```bash
genlayer network set testnet-bradbury

ADDR=0xa0213ed1883a18b44B149510885395e0BDc98858

# Submit an article
genlayer write $ADDR submit_article --args https://example.com

# Trigger retrieval + consensus
genlayer write $ADDR resolve_article --args article-1

# Read the verdict + recorded source
genlayer call $ADDR get_verdict --args article-1
```

---

## Tech stack

- **Contract:** Python GenLayer Intelligent Contract (GenVM)
- **Storage:** `TreeMap[str, Article]`, `TreeMap[str, Verdict]`, `DynArray[str]`
- **Consensus:** `gl.vm.run_nondet` (leader retrieves, validators validate)
- **AI + web:** `gl.nondet.exec_prompt`, `gl.nondet.web.request`
- **Frontend:** Vanilla HTML/CSS/JS on GitHub Pages
- **Network:** GenLayer Bradbury Testnet (chain 4221)

---

## Repository

[https://github.com/Adebisi1111/factcheckdao](https://github.com/Adebisi1111/factcheckdao)

## License

MIT
