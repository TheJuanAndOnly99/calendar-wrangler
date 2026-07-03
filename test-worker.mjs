// Ad-hoc test harness: runs the Worker's `normalize()` on the local .ics and
// verifies it produces the same clean output the Python CLI produces.
// Run with: node test-worker.mjs
import fs from "node:fs";
import path from "node:path";

const workerSource = fs.readFileSync(path.resolve("worker.js"), "utf-8");

// Extract the `normalize` function from worker.js without importing the
// default export (which requires the Workers runtime).
const startIdx = workerSource.indexOf("function normalize(");
if (startIdx < 0) throw new Error("normalize() not found in worker.js");
const normalizeSrc = workerSource.slice(startIdx);
const normalize = new Function(`${normalizeSrc}\nreturn normalize;`)();

const raw = fs.readFileSync("Calendar.ics", "utf-8");
const cleaned = normalize(raw, "20280101T000000Z");
fs.writeFileSync("Calendar-clean.worker.ics", cleaned);

const count = (haystack, re) => (haystack.match(re) || []).length;
const beforeVEvents = count(raw, /^BEGIN:VEVENT/gm);
const afterVEvents = count(cleaned, /^BEGIN:VEVENT/gm);
const beforeEmptyAtt = count(raw, /^ATTENDEE;VALUE=TEXT:\s*$/gm);
const afterEmptyAtt = count(cleaned, /^ATTENDEE;VALUE=TEXT:\s*$/gm);
const beforeTzid = count(raw, /^(DTSTAMP|CREATED|LAST-MODIFIED);TZID=/gm);
const afterTzid = count(cleaned, /^(DTSTAMP|CREATED|LAST-MODIFIED);TZID=/gm);
const beforeUntil = count(raw, /UNTIL=2[1-9]\d{6}T\d{6}Z?/g);
const afterUntil = count(cleaned, /UNTIL=2[1-9]\d{6}T\d{6}Z?/g);

const pad = (s, n) => (s + " ".repeat(n)).slice(0, n);
console.log(pad("VEVENT count:", 32), `before=${beforeVEvents}\tafter=${afterVEvents}`);
console.log(pad("Empty ATTENDEE lines:", 32), `before=${beforeEmptyAtt}\tafter=${afterEmptyAtt}`);
console.log(pad("TZID-on-timestamp lines:", 32), `before=${beforeTzid}\tafter=${afterTzid}`);
console.log(pad("Far-future UNTIL values:", 32), `before=${beforeUntil}\tafter=${afterUntil}`);

if (
  afterVEvents !== beforeVEvents ||
  afterEmptyAtt !== 0 ||
  afterTzid !== 0 ||
  afterUntil !== 0
) {
  console.error("Worker normalize() failed one or more expectations.");
  process.exit(1);
}
console.log("OK: worker.js normalize() matches expectations.");
