// Summarise reports/a11y.jsonl (written by the `snap` fixture) as one line per distinct finding.
import { readFileSync, existsSync } from 'node:fs';
const file = new URL('../reports/a11y.jsonl', import.meta.url);
if (!existsSync(file)) { console.log('no reports/a11y.jsonl: run the suite first'); process.exit(0); }
const rows = readFileSync(file, 'utf8').trim().split('\n').filter(Boolean).map((l) => JSON.parse(l));
const by = new Map();
for (const r of rows) {
  const k = `${r.app}\t${r.id}\t${r.impact}`;
  const e = by.get(k) ?? { ...r, screens: new Set(), themes: new Set() };
  e.screens.add(r.screen); e.themes.add(`${r.theme}/${r.viewport}`);
  by.set(k, e);
}
const order = { critical: 0, serious: 1, moderate: 2, minor: 3 };
for (const e of [...by.values()].sort((a, b) => (order[a.impact] ?? 9) - (order[b.impact] ?? 9) || a.app.localeCompare(b.app))) {
  console.log(`${e.impact.padEnd(9)} ${e.app.padEnd(10)} ${e.id}  screens: ${[...e.screens].join(', ')}  [${[...e.themes].join(' ')}]  e.g. ${e.targets[0]}`);
}
console.log(`\n${by.size} distinct findings, ${rows.length} rows`);
