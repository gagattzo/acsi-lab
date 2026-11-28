// Does the live site block the headline on a normal weekday, before that day's DTCC end-of-day file is published?
//
// Usage:  node research/tools/weekday_block_check.js /path/to/Ai-credit-stress-index- [/path/to/acsi-lab] [now] [unpublished-date]
//         defaults: now = 2026-11-25T18:00:00Z (a Wednesday afternoon), unpublished-date = 2026-11-25
// Needs:  the site checkout (api/cds.js) and the lab's data/raw/dtcc/*.zip.
//
// Runs the site's /api/cds handler with the clock fixed at `now`. DTCC requests are answered from the lab's cached
// files; the unpublished day's end-of-day file and every intraday slice answer HTTP 404, as DTCC does before
// publication. Prints the source_quality block that the headline gate reads.
const fs = require('fs');
const path = require('path');
const [SITE, LABARG, NOW = '2026-11-25T18:00:00Z', UNPUBLISHED = '2026-11-25'] = process.argv.slice(2);
const LAB = LABARG || path.resolve(__dirname, '../..');
const RAW = path.resolve(LAB, 'data/raw/dtcc');
const FIXED = Date.parse(NOW);
const RealDate = Date;
global.Date = class extends RealDate {
  constructor(...a) { if (a.length) super(...a); else super(FIXED); }
  static now() { return FIXED; }
};
global.Date.UTC = RealDate.UTC; global.Date.parse = RealDate.parse;
global.fetch = async (url) => {
  const m = /SEC_CUMULATIVE_CREDITS_(\d{4})_(\d{2})_(\d{2})\.zip$/.exec(url);
  const day = m && `${m[1]}-${m[2]}-${m[3]}`;
  const file = day && path.join(RAW, `${day}.zip`);
  if (file && day !== UNPUBLISHED && fs.existsSync(file)) {
    const b = fs.readFileSync(file);
    return { ok: true, status: 200, arrayBuffer: async () => b.buffer.slice(b.byteOffset, b.byteOffset + b.length) };
  }
  return { ok: false, status: 404, arrayBuffer: async () => new ArrayBuffer(0) };
};
const handler = require(path.resolve(SITE, 'api/cds.js'));
const res = { setHeader() {}, status() { return this; }, send(s) { this.body = JSON.parse(s); } };
handler({}, res).then(() => {
  const q = res.body.source_quality;
  console.log(`# Weekday block check\n\nnow ${NOW}; ${UNPUBLISHED} end-of-day file not yet published; site code at ${path.basename(path.resolve(SITE))}\n`);
  console.log(`source_quality.status: ${q.status}`);
  console.log(`calculation_eligible: ${q.calculation_eligible}`);
  console.log(`reasons: ${JSON.stringify(q.reasons)}`);
  console.log(`missing_dates: ${JSON.stringify(q.missing_dates)}`);
  console.log(`lifecycle issues: ${JSON.stringify(q.lifecycle.issue_counts)}`);
});
