import { createClient, chains } from 'genlayer-js';
import { privateKeyToAccount } from 'viem/accounts';
import fs from 'fs';

const ADDR = '0x23ECB16BB8c8Ee637aF73435D88073b16c091672';
let PK = fs.readFileSync('/tmp/oracle_pk.txt', 'utf8').trim();
if (!PK.startsWith('0x')) PK = '0x' + PK;

const account = privateKeyToAccount(PK);
const client = createClient({ chain: chains.testnetBradbury, account });
const FEES = { feeValue: '100000000000010352' };
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

let pass = 0, fail = 0;
const check = (label, cond, extra = '') => {
  if (cond) { pass++; console.log(`  PASS  ${label}${extra ? ' — ' + extra : ''}`); }
  else { fail++; console.log(`  FAIL  ${label}${extra ? ' — ' + extra : ''}`); }
};

const ABI = [
  { type: 'function', name: 'submit_article', stateMutability: 'nonpayable',
    inputs: [{ name: 'url', type: 'string' }], outputs: [] },
  { type: 'function', name: 'resolve_article', stateMutability: 'nonpayable',
    inputs: [{ name: 'article_id', type: 'string' }], outputs: [{ name: '', type: 'string' }] },
  { type: 'function', name: 'get_verdict', stateMutability: 'view',
    inputs: [{ name: 'article_id', type: 'string' }], outputs: [{ name: '', type: 'any' }] },
  { type: 'function', name: 'get_article', stateMutability: 'view',
    inputs: [{ name: 'article_id', type: 'string' }], outputs: [{ name: '', type: 'any' }] },
  { type: 'function', name: 'count_pending', stateMutability: 'view', inputs: [], outputs: [{ name: '', type: 'any' }] },
];

async function fin(h) {
  for (let i = 0; i < 200; i++) {
    try {
      const r = await client.waitForTransactionReceipt({ hash: h, waitUntil: 'finalized', retries: 5, interval: 4000 });
      if (r && (r.resultName || r.result_name)) {
        return { name: r.resultName || r.result_name, exec: r.txExecutionResultName || r.tx_execution_result_name };
      }
    } catch (e) {}
    await sleep(5000);
  }
  return null;
}

async function read(fn, args = []) {
  const v = await client.readContract({ address: ADDR, abi: ABI, functionName: fn, args });
  return typeof v === 'string' ? JSON.parse(v) : v;
}

(async () => {
  console.log('FactCheckDAO — steward requirement proof on Bradbury\n');
  console.log('contract:', ADDR, '\n');
  console.log('Steward requirement: the equivalence check must bind the stored claim');
  console.log('and evidence trail to validator agreement, and avoid finalizing');
  console.log('unsupported or transiently unavailable evidence.\n');

  // ---- PART A: evidence IS bound to agreement ----
  console.log('[A] a real article — the evidence trail is bound to agreement');
  const URL = 'https://en.wikipedia.org/wiki/Hello_World_program';
  const sh = await client.writeContract({ address: ADDR, abi: ABI, functionName: 'submit_article', args: [URL], value: 0n, fees: FEES });
  const srec = await fin(sh);
  check('submit committed', !!srec, srec ? srec.name : 'no receipt');
  await sleep(6000);
  const art = await read('get_article', ['article-1']).catch(() => null);
  check('article is PENDING', !!art && art.status === 'PENDING');
  console.log('');

  let ok = false;
  for (let a = 1; a <= 10 && !ok; a++) {
    const rh = await client.writeContract({ address: ADDR, abi: ABI, functionName: 'resolve_article', args: ['article-1'], value: 0n, fees: FEES });
    const r = await fin(rh);
    console.log(`  resolve attempt ${a}: ${r ? r.name : 'none'}/${r ? r.exec : 'none'}`);
    if (r && r.name === 'AGREE' && r.exec === 'FINISHED_WITH_RETURN') ok = true;
    if (!ok) await sleep(8000);
  }
  check('validators reached consensus', ok);
  console.log('');

  const v = await read('get_verdict', ['article-1']).catch(() => null);
  if (!v || v.exists === false) {
    check('verdict stored', false, JSON.stringify(v).slice(0, 80));
  } else {
    check('verdict stored', true, `verdict=${v.verdict}`);
    const rec = (v.sources && v.sources[0]) || {};
    check('article_fetched recorded true', rec.article_fetched === true, `=${rec.article_fetched}`);
    check('source_fetched recorded true', rec.source_fetched === true, `=${rec.source_fetched}`);
    check('source_url recorded', !!rec.source_url, String(rec.source_url).slice(0, 55));
    check('claim is grounded in the fetched source', !!(rec.claim || '').trim(),
      String(rec.claim).slice(0, 70));
    check('evidence excerpt recorded', !!(rec.evidence || rec.claim || '').trim(),
      `len=${(rec.evidence || rec.claim || '').length}`);
    console.log('\n  STORED (this is what validators agreed on):');
    console.log('    verdict        :', v.verdict);
    console.log('    source_url     :', rec.source_url);
    console.log('    article_fetched:', rec.article_fetched);
    console.log('    source_fetched :', rec.source_fetched);
    console.log('    claim (grounded):', String(rec.claim).slice(0, 95));
    console.log('    evidence       :', String(rec.evidence || rec.claim).slice(0, 95) + '…');
    console.log('    reasoning      :', String(v.reasoning).slice(0, 95));
  }
  console.log('');

  // ---- PART B: transiently unavailable evidence must NOT finalize ----
  console.log('[B] transiently unavailable evidence must NOT finalize');
  const BAD = 'https://this-domain-does-not-exist-xyz123.example/nothing.html';
  const bh = await client.writeContract({ address: ADDR, abi: ABI, functionName: 'submit_article', args: [BAD], value: 0n, fees: FEES });
  const brec = await fin(bh);
  check('bad article submitted', !!brec, brec ? brec.name : 'no receipt');
  await sleep(6000);

  // retry a few times: a transient failure must never produce a stored verdict
  let finalizedBad = false;
  for (let a = 1; a <= 4 && !finalizedBad; a++) {
    const rh = await client.writeContract({ address: ADDR, abi: ABI, functionName: 'resolve_article', args: ['article-2'], value: 0n, fees: FEES });
    const r = await fin(rh);
    console.log(`  attempt ${a}: ${r ? r.name : 'none'}/${r ? r.exec : 'none'}`);
    await sleep(5000);
    const a2 = await read('get_article', ['article-2']).catch(() => null);
    if (a2 && a2.status === 'RESOLVED') finalizedBad = true;
  }
  const a2 = await read('get_article', ['article-2']).catch(() => null);
  const v2 = await read('get_verdict', ['article-2']).catch(() => null);
  check('unavailable evidence did NOT finalize', !finalizedBad,
    `article-2 status=${a2 ? a2.status : 'unknown'}`);
  check('no verdict recorded for it', !v2 || v2.exists === false,
    v2 && v2.exists ? `verdict=${v2.verdict}` : 'none stored');
  console.log('');

  const pend = await read('count_pending', []).catch(() => '?');
  console.log(`count_pending: ${pend}`);

  console.log(`\n=== ${pass} passed, ${fail} failed ===`);
  console.log(`\nCONTRACT: ${ADDR}`);
  console.log(`EXPLORER: https://explorer-bradbury.genlayer.com/address/${ADDR}`);
})().catch(e => { console.error('ERROR:', e.message || e); process.exit(1); });
