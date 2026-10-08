# FactCheckDAO

**Live App:** [https://adebisi1111.github.io/factcheckdao/](https://adebisi1111.github.io/factcheckdao/)

A GenLayer-native decentralized fact-checking utility that fetches article text via `gl.nondet.web.request` and uses AI consensus (`gl.nondet.exec_prompt`) to verify claims against encyclopedic sources (Wikipedia, NASA, Britannica) into three states: **SUPPORTED**, **REFUTED**, or **INSUFFICIENT**.

---

## Core Overview

FactCheckDAO is an Intelligent Contract on the GenLayer protocol that performs automated fact-checking using a committee of AI validators. The workflow:

1. **Submit** — A user submits an article URL via `submit_article(url)`. The article is stored on-chain with status `PENDING`.
2. **Resolve** — Anyone can trigger `resolve_article(article_id)`. This initiates a `gl.vm.run_nondet` consensus round where multiple validators independently:
   - Fetch the article via `gl.nondet.web.request`
   - Strip HTML to extract clean paragraph text
   - Extract 1–5 verifiable factual claims using `gl.nondet.exec_prompt`
   - Verify claims against authoritative encyclopedic sources (Wikipedia, NASA, Britannica, Nature, Scientific American)
   - Return a structured JSON verdict: `{"verdict": "SUPPORTED|REFUTED|INSUFFICIENT", "reasoning": "..."}`
3. **Consensus** — If a majority of validators agree on the same verdict, it is stored on-chain with full reasoning and extracted claims.
4. **Read** — Anyone can query `get_verdict(article_id)` to retrieve the verdict, reasoning, and claims.

### Verdict States

| State | Meaning |
|---|---|
| `SUPPORTED` | Claims are confirmed by authoritative sources or established knowledge |
| `REFUTED` | Claims are contradicted by authoritative sources or established knowledge |
| `INSUFFICIENT` | Claims rely on real-time news or information past the LLM's knowledge cutoff |

---

## Smart Contract

| Property | Value |
|---|---|
| **Network** | GenLayer Bradbury Testnet |
| **Chain ID** | 4221 |
| **Contract Address** | `0xb005F5176b1D668d6AcF9d664D57C2476B2f1418` |
| **Explorer** | [View on Bradbury Explorer](https://explorer-bradbury.genlayer.com/address/0xb005F5176b1D668d6AcF9d664D57C2476B2f1418) |
| **GenVM Runner** | `py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6` |

---

## Key Engineering Accomplishment

### The LEADER_TIMEOUT Problem

On Bradbury, the `resolve_article` transaction involves a multi-step nondeterministic execution: web scraping → HTML stripping → claims extraction → encyclopedic verification. This pushes close to the block execution window, causing occasional `LEADER_TIMEOUT` errors where one validator fails to complete in time.

**The consensus still succeeds** — 4 out of 5 validators agree, and the verdict is written to chain. The timeout is purely a network/timing latency layer, not a governance failure.

### The Frontend Solution

We mitigated this by decoupling the UI from transaction confirmation:

1. **Optimistic UI State** — When a user clicks "Run AI Verification", the frontend immediately shows a progress bar with "Verifying Article (This may take up to 45 seconds)..." rather than blocking on the RPC response.

2. **Polling Loop** — After transaction submission, the frontend polls `get_verdict(article_id)` every 5 seconds for up to 60 seconds. This decouples the UX from the consensus finalization time.

3. **Graceful Timeout Handling** — If the RPC connection throws a `LEADER_TIMEOUT` or network error, the frontend does not show a failure screen. Instead, it waits 10 seconds and cross-checks the contract state directly — because consensus likely succeeded despite the frontend timeout.

4. **Fallback Retry** — Only if the verdict is still not found after 60 seconds of polling does the frontend show a "Network busy. Retry Verification?" button with a one-click retry mechanism.

This approach ensures users never see a raw timeout error and always get their verdict as long as consensus was reached on-chain.

---

## Contract API

| Method | Returns | What it does |
|---|---|---|
| `submit_article(url)` | `article_id` | Stores a pending article on-chain |
| `resolve_article(article_id)` | `verdict` token | Triggers AI consensus round |
| `get_article(article_id)` | JSON | Article details + status |
| `get_verdict(article_id)` | JSON | Verdict + reasoning + extracted claims |
| `list_pending` | JSON list | All PENDING articles |
| `total_articles` | `u256` | Global article counter |
| `count_pending` | `u256` | Number of pending articles |
| `now` | ISO string | Current timestamp |

---

## Verified On-Chain Results

| Article | URL | Verdict | Reasoning |
|---|---|---|---|
| article-1 | `https://en.wikipedia.org/wiki/Mars` | **SUPPORTED** | "All claims are consistent with authoritative sources (NASA, Britannica, Wikipedia). Mars is the 4th planet, mean diameter ~6779 km, has moons Phobos and Deimos, year ~687 Earth days, and Mariner 4 performed the first successful flyby in 1965." |

**Extracted Claims:**
- Mars is the fourth planet from the Sun.
- The mean diameter of Mars is 6,779 km (4,212 mi).
- Mars has two natural satellites: Phobos and Deimos.
- A Martian solar year is equal to 687 Earth days.
- The first successful flyby exploration of Mars was conducted in 1965 with Mariner 4.

**Consensus:** 4/5 validators agreed (1 timeout) — verdict finalized on-chain.

---

## How to Run

### Prerequisites

- A Web3 wallet (MetaMask, Rabby, or similar)
- Bradbury Testnet (chain ID 4221) configured in your wallet
- Some GEN tokens for gas (faucet: [https://testnet-faucet.genlayer.foundation](https://testnet-faucet.genlayer.foundation))

### Steps

1. **Open the app:** [https://adebisi1111.github.io/factcheckdao/](https://adebisi1111.github.io/factcheckdao/)
2. **Connect wallet** — Click "Connect Wallet". The app will auto-switch your wallet to Bradbury Testnet (chain 4221).
3. **Submit an article** — Paste any article URL and click "Submit for Fact-Check".
4. **Run verification** — Click "Run AI Verification" on any pending article. The app will poll for consensus and display the verdict with reasoning.
5. **View results** — Verdicts, reasoning, and extracted claims are displayed on-chain and verifiable via the [Bradbury Explorer](https://explorer-bradbury.genlayer.com/address/0xb005F5176b1D668d6AcF9d664D57C2476B2f1418).

### Manual CLI Interaction

```bash
# Set network
genlayer network set testnet-bradbury

# Submit an article
genlayer write 0xb005F5176b1D668d6AcF9d664D57C2476B2f1418 submit_article \
  --args https://en.wikipedia.org/wiki/Mars \
  --fees '{"gasLimit":"0x186a0","gasPrice":"0x0bebc200"}'

# Resolve (trigger AI consensus)
genlayer write 0xb005F5176b1D668d6AcF9d664D57C2476B2f1418 resolve_article \
  --args article-1 \
  --fees '{"gasLimit":"0x186a0","gasPrice":"0x0bebc200"}'

# Read verdict
genlayer call 0xb005F5176b1D668d6AcF9d664D57C2476B2f1418 get_verdict --args article-1
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Frontend (GitHub Pages)               │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │ Submit Form │  │ Article List │  │ Verdict Display │  │
│  └──────┬──────┘  └──────┬───────┘  └───────┬────────┘  │
│         │                │                   │           │
│         └────────────────┼───────────────────┘           │
│                          │                               │
│              ┌───────────▼──────────┐                    │
│              │  Polling Loop (5s)   │                    │
│              │  Progress Bar + Retry│                    │
│              └───────────┬──────────┘                    │
└──────────────────────────┼──────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│              Bradbury Testnet (Chain 4221)                │
│  ┌────────────────────────────────────────────────────┐ │
│  │         FactCheckDAO Intelligent Contract           │ │
│  │                                                    │ │
│  │  submit_article(url)  →  Article(PENDING)          │ │
│  │  resolve_article(id)  →  gl.vm.run_nondet          │ │
│  │    ├─ leader_fn: web.request → strip HTML →       │ │
│  │    │  exec_prompt(claims) → exec_prompt(verify)   │ │
│  │    └─ validator_fn: independent verification       │ │
│  │  get_verdict(id)      →  {verdict, reasoning, ...} │ │
│  └────────────────────────────────────────────────────┘ │
│                          │                               │
│              ┌───────────▼──────────┐                    │
│              │  5 AI Validators     │                    │
│              │  (4/5 majority wins) │                    │
│              └──────────────────────┘                    │
└─────────────────────────────────────────────────────────┘
```

---

## Tech Stack

- **Contract:** Python GenLayer Intelligent Contract (GenVM)
- **Storage:** `TreeMap[str, Article]`, `TreeMap[str, Verdict]`, `DynArray[str]`
- **Consensus:** `gl.vm.run_nondet` with leader/validator pattern
- **AI Calls:** `gl.nondet.web.request` + `gl.nondet.exec_prompt`
- **Frontend:** Vanilla HTML/CSS/JS (GitHub Pages)
- **Network:** GenLayer Bradbury Testnet (chain 4221)

---

## Repository

[https://github.com/Adebisi1111/factcheckdao](https://github.com/Adebisi1111/factcheckdao)

---

## License

MIT
