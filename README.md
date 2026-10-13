# FactCheckDAO — steward fix (v2)

**Contract:** [`0x23ECB16BB8c8Ee637aF73435D88073b16c091672`](https://explorer-bradbury.genlayer.com/address/0x23ECB16BB8c8Ee637aF73435D88073b16c091672) on GenLayer Bradbury Testnet (chain 4221)
**Deployed size:** 19,894 bytes — identical to `contracts/factcheck_dao.py` in this repo.

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
  ┌──────────────────┐   every validator independently:
  │  resolve_article  │ ── 1. fetches the article (gl.nondet.web.request)
  └──────────────────┘      2. derives the Wikipedia title whose page corroborates it
        │                   3. FETCHES that source page
        │                   4. judges the claim FROM the retrieved excerpt
        ▼
   consensus on the evidence identity, not a bare label
```

---

## Steward fix: evidence is bound to agreement

The steward's note:

> We cannot accept this submission because validators agree only on a coarse verdict while the contract stores a leader-selected claim, source, and excerpt that the other validators may not have evaluated. The equivalence check must bind the stored claim and evidence trail to validator agreement and avoid finalizing unsupported or transiently unavailable evidence.

### What was wrong

The equivalence principle allowed validators to *"differ on wording or source URL and still agree"* — so two validators reaching SUPPORTED from **different** sources committed as agreeing, and the contract stored the **leader's** claim, source and excerpt, which the other validators may never have seen.

### The fix

The equivalence check now binds **four** values that every validator independently reproduces:

1. `source_url` — the **identical** URL every validator derived from the article and fetched
2. `article_fetched` — the article was actually retrieved
3. `source_retrieved` — the corroborating source was actually retrieved
4. `verdict` — SUPPORTED / REFUTED / INSUFFICIENT

The principle states explicitly: **if the source URL or any flag differs, they do NOT agree, even when the verdict label matches.** Agreeing on "SUPPORTED" from two different sources no longer counts as consensus.

The stored **claim** is no longer LLM phrasing. It is `grounded` — the first sentence taken **from the fetched source page** — so it is verifiable against the URL every validator fetched. The stored `source_url` comes from the **agreed** result, not a leader-only pick.

### Nothing finalizes without retrieved evidence

If the article cannot be retrieved, the round **reverts** with:

```
the submitted article could not be retrieved, so no verdict is finalized:
any claim would be invented.
```

and the article stays PENDING for a later attempt. A transient failure never produces a stored verdict.

---

## Verified on-chain

Proof run: `node prove_steward.mjs` → **12/12 assertions pass**.

| Test | Result |
|---|---|
| submit_article commits | PASS — AGREE |
| article is PENDING before resolution | PASS |
| validators reach consensus | PASS — AGREE (earlier IDLE/DISAGREE rounds recorded nothing) |
| verdict stored | PASS — INSUFFICIENT |
| article_fetched recorded true | PASS |
| source_fetched recorded true | PASS |
| source_url recorded (the agreed URL) | PASS — `https://en.wikipedia.org/wiki/Hello_World_program` |
| claim is grounded in the fetched source | PASS |
| evidence excerpt recorded | PASS |
| unreachable article did NOT finalize | PASS — stayed PENDING across 4 attempts |
| no verdict recorded for the unreachable article | PASS — none stored |
| count_pending correct | PASS |

The stored verdict is `INSUFFICIENT`, and the reasoning explains why: the excerpt defines what a Hello World program is but does not mention its origin, so the claim is not confirmed. The contract does not default to SUPPORTED.

---

## Contract layout

- `contracts/factcheck_dao.py` — the contract
- `prove_steward.mjs` — on-chain proof of the steward requirement
- `src/`, `tests/`, `artifacts/` — supporting material

Runner: `py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6` (Bradbury).
