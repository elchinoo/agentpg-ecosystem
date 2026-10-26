import * as http from 'node:http';
import * as crypto from 'node:crypto';
import { freePort } from './util';

export interface FakeLlm {
  url: string; // base URL including /v1
  port: number;
  calls: { path: string; body: any }[];
  stop(): Promise<void>;
}

/** Deterministic unit-ish vector for a text (no model involved). */
function vec(text: string, dim: number): number[] {
  const out: number[] = [];
  let seed = crypto.createHash('sha256').update(text).digest();
  while (out.length < dim) {
    for (const b of seed) if (out.length < dim) out.push((b - 127.5) / 127.5);
    seed = crypto.createHash('sha256').update(seed).digest();
  }
  const n = Math.sqrt(out.reduce((a, x) => a + x * x, 0)) || 1;
  return out.map((x) => x / n);
}

/**
 * A fake OpenAI-compatible server with canned answers, started by the harness on a free loopback port.
 * - /v1/embeddings: deterministic vectors of `dimensions` (or defaultDim) numbers.
 * - /v1/chat/completions: if the prompt carries numbered POLICY PASSAGES (TrustDesk), an "approve" verdict quoting the
 *   first sentence of passage 1 word for word; if it asks for a fraud triage JSON, a canned triage; else a plain reply.
 */
export async function startFakeLlm(opts: { defaultDim?: number } = {}): Promise<FakeLlm> {
  const port = await freePort();
  const calls: FakeLlm['calls'] = [];
  const srv = http.createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => {
      let body: any = {};
      try {
        body = JSON.parse(Buffer.concat(chunks).toString() || '{}');
      } catch {}
      const path = (req.url || '').split('?')[0];
      calls.push({ path, body });
      const send = (code: number, obj: unknown) => {
        res.writeHead(code, { 'content-type': 'application/json' });
        res.end(JSON.stringify(obj));
      };
      if (/\/models$/.test(path)) return send(200, { object: 'list', data: [{ id: 'fake-chat', object: 'model' }, { id: 'fake-embed', object: 'model' }] });
      if (/\/embeddings$/.test(path)) {
        const inputs: string[] = Array.isArray(body.input) ? body.input : [String(body.input ?? '')];
        const dim = Number(body.dimensions) || opts.defaultDim || 768;
        return send(200, {
          object: 'list',
          model: body.model || 'fake-embed',
          data: inputs.map((t, index) => ({ object: 'embedding', index, embedding: vec(t, dim) })),
          usage: { prompt_tokens: 1, total_tokens: 1 },
        });
      }
      if (/\/chat\/completions$/.test(path)) {
        const messages: { role: string; content: string }[] = body.messages || [];
        const prompt = messages.map((m) => (typeof m.content === 'string' ? m.content : JSON.stringify(m.content))).join('\n');
        let content = 'This is a canned answer from the e2e fake model.';
        const passage = prompt.match(/<passage>\n([\s\S]*?)\n<\/passage>/);
        if (passage) {
          const text = passage[1].replace(/\s+/g, ' ').trim();
          const quote = (text.match(/^[^.!?]{20,160}[.!?]/)?.[0] ?? text.slice(0, 100)).replace(/["\\]/g, '');
          content = JSON.stringify({
            recommendation: 'approve',
            compliance: 'compliant',
            summary: 'The request fits the quoted policy. Approval is advised.',
            reasons: [{ text: 'The request complies with the cited policy passage.', passages: [1] }],
            citations: [{ passage: 1, quote }],
            missing_information: [],
          });
        } else if (/triage|fraud case|risk case/i.test(prompt)) {
          content = JSON.stringify({ verdict: 'review', confidence: 0.6, summary: 'Canned triage from the e2e fake model.', reasons: ['canned'] });
        }
        return send(200, {
          id: 'chatcmpl-fake',
          object: 'chat.completion',
          created: Math.floor(Date.now() / 1000),
          model: body.model || 'fake-chat',
          choices: [{ index: 0, message: { role: 'assistant', content }, finish_reason: 'stop' }],
          usage: { prompt_tokens: 10, completion_tokens: 10, total_tokens: 20 },
        });
      }
      send(404, { error: { message: 'not found: ' + path } });
    });
  });
  await new Promise<void>((r) => srv.listen(port, '127.0.0.1', r));
  return {
    url: `http://127.0.0.1:${port}/v1`,
    port,
    calls,
    stop: () => new Promise<void>((r) => srv.close(() => r())),
  };
}
