import { createClient, chains } from 'genlayer-js';
import { privateKeyToAccount } from 'viem/accounts';
import fs from 'fs';

const ADDR = '0x71C384848EAdb767CC7f59917D26F161b98c29Ce';
const RPC = 'https://rpc-bradbury.genlayer.com';
let PK = fs.readFileSync('/tmp/oracle_pk.txt', 'utf8').trim();
if (!PK.startsWith('0x')) PK = '0x' + PK;

const account = privateKeyToAccount(PK);
const client = createClient({ chain: chains.testnetBradbury, account });
const ME = account.address;
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
];

async function resyncNonce() {
  try {
    const j = await fetch(RPC, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', method: 'eth_getTransactionCount', params: [ME, 'pending'], id: 1 }),
    }).then(r => r.json());
    const n = parseInt(j.result, 16);
    if (Number.isFinite(n)) { client.account.nonce = n; return n; }
  } catch (e) {}
  return null;
}

async function fin(h) {
  for (let i = 0; i < 200; i++) {
    try {
      const r = await client.waitForTransactionReceipt({ hash: h, waitUntil: 'finalized', retries: 5, interval: 4000 });
      if (r && (r.resultName || r.result_name)) {
        const lr = r.lastRound || r.last_round;
        return {
          name: r.resultName || r.result_name,
          exec: r.txExecutionResultName || r.tx_execution_result_name,
          votes: lr ? (lr.validatorVotesName || lr.validator_votes_names) : null,
        };
      }
    } catch (e) {}
    await sleep(5000);
  }
  return null;
}

async function write(fn, args, a = 1) {
  try {
    const h = await client.writeContract({ address: ADDR, abi: ABI, functionName: fn, args, value: 0n, fees: FEES });
    return await fin(h);
  } catch (e) {
    const msg = (e.shortMessage || e.message || '');
    if ((msg.includes('to consensus contract') || msg.includes('nonce')) && a < 6) {
      await resyncNonce(); await sleep(4000);
      return write(fn, args, a + 1);
    }
    console.log(`    ${fn} failed: ${msg.slice(0, 90)}`);
    return null;
  }
}

async function read(fn, args = []) {
  const v = await client.readContract({ address: ADDR, abi: ABI, functionName: fn, args });
  return typeof v === 'string' ? JSON.parse(v) : v;
}

(async () => {
  console.log('FactCheckDAO — ADVERSARIAL proof on Bradbury\n');
  console.log('contract (rigged leader):', ADDR, '\n');
  console.log('The leader fabricates its evidence trail: a made-up source URL, a');
  console.log('fabricated excerpt, and a hash of that fabrication. Every validator');
  console.log('independently retrieves the REAL page. If the equivalence check binds');
  console.log('the evidence, the fabricated hash cannot match and NOTHING commits.\n');

  const URL = 'https://en.wikipedia.org/wiki/Hello_World_program';
  const sh = await write('submit_article', [URL]);
  check('submit committed', !!sh, sh ? sh.name : 'no receipt');
  await sleep(6000);

  let committed = false;
  const results = [];
  for (let a = 1; a <= 5 && !committed; a++) {
    const r = await write('resolve_article', ['article-1']);
    const line = `${r ? r.name : 'none'}/${r ? r.exec : 'none'} votes=${r && r.votes ? JSON.stringify(r.votes) : 'n/a'}`;
    console.log(`  round ${a}: ${line}`);
    results.push(r);
    // A rigged round that still commits would be AGREE + FINISHED_WITH_RETURN
    if (r && r.name === 'AGREE' && r.exec === 'FINISHED_WITH_RETURN') committed = true;
    await sleep(6000);
  }

  await sleep(8000);
  const art = await read('get_article', ['article-1']).catch(() => null);
  const v = await read('get_verdict', ['article-1']).catch(() => null);

  console.log('');
  check('rigged leader did NOT commit', !committed,
    committed ? 'a fabricated round committed — guard FAILED' : 'no round committed');
  check('no verdict stored', !v || v.exists === false,
    v && v.exists ? `verdict=${v.verdict} STORED` : 'none stored');
  check('article left PENDING', !!art && art.status === 'PENDING',
    `status=${art ? art.status : 'unknown'}`);

  const disagreeRounds = results.filter(r => r && (r.name === 'DISAGREE' || (r.votes && JSON.stringify(r.votes).includes('DISAGREE')))).length;
  check('at least one round showed validator DISAGREE', disagreeRounds >= 1,
    `${disagreeRounds} round(s) with DISAGREE votes`);

  console.log(`\n=== ${pass} passed, ${fail} failed ===`);
  console.log(`\nRIGGED CONTRACT: ${ADDR}`);
  console.log(`EXPLORER: https://explorer-bradbury.genlayer.com/address/${ADDR}`);
})().catch(e => { console.error('ERROR:', e.message || e); process.exit(1); });
