// Would the live site (dtcc-strict-v1, site main 022c39b) have shown a headline in past 12-day windows?
//
// Usage:  node research/tools/site_window_replay.js /path/to/Ai-credit-stress-index- [/path/to/acsi-lab] [step]
// Needs:  the site checkout (api/cds.js) and the lab's data/raw/dtcc/*.zip.
//
// Replays the site's 12-calendar-day window starting on every `step`-th file day (default 5) through the site's own
// ledger and pricing code. A window can feed the headline only if the lifecycle check is complete AND CoreWeave has an
// accepted price in it (Oracle is priced most days). Missing or unpublished files, which also block the headline, are
// not modelled here: every file in the window is present.
const fs = require('fs');
const path = require('path');
const [SITE, LABARG, STEP = '5'] = process.argv.slice(2);
const LAB = LABARG || path.resolve(__dirname, '../..');
const cds = require(path.resolve(SITE, 'api/cds.js'))._test;
const RAW = path.resolve(LAB, 'data/raw/dtcc');
const days = fs.readdirSync(RAW).filter((f) => /^\d{4}-\d{2}-\d{2}\.zip$/.test(f)).map((f) => f.slice(0, 10)).sort();
const cache = new Map();
const load = (d) => {
  if (!cache.has(d)) cache.set(d, cds.slim(cds.parseCSV(cds.unzipFirst(fs.readFileSync(path.join(RAW, `${d}.zip`))).toString('utf8'))));
  return cache.get(d);
};
let windows = 0, complete = 0, crwv = 0, both = 0;
const issues = {};
for (let i = 0; i < days.length; i += Number(STEP)) {
  const end = new Date(Date.parse(`${days[i]}T00:00:00Z`) + 11 * 86400000).toISOString().slice(0, 10);
  if (end > days[days.length - 1]) break;
  const win = days.filter((d) => d >= days[i] && d <= end);
  windows += 1;
  const byDay = cds.ledger(win.map((d) => ({ date: d, slim: load(d) })));
  const ok = byDay.quality.status === 'complete';
  Object.keys(byDay.quality.issue_counts).forEach((k) => { issues[k] = (issues[k] || 0) + 1; });
  let prev = null, priced = false;
  for (const d of [...byDay.keys()].sort()) {
    const lv = cds.levelsForDay(byDay.get(d), d, prev, true);
    if (lv.CRWV) priced = true;
  }
  complete += ok; crwv += priced; both += ok && priced;
}
console.log(`# Site window replay\n\n${days.length} raw DTCC files, ${days[0]} to ${days[days.length - 1]}; site code at ${path.basename(path.resolve(SITE))}\n`);
console.log(`12-day windows (every ${STEP}th start day): ${windows}`);
console.log(`- lifecycle check complete: ${complete}`);
console.log(`- CoreWeave priced at least once: ${crwv}`);
console.log(`- both, so the headline could show: ${both}`);
console.log(`- windows with each lifecycle issue: ${JSON.stringify(issues)}`);
