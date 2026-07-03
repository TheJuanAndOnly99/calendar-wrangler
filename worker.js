/**
 * Cloudflare Worker: LFX calendar normalizer + live proxy.
 *
 * On every request, this fetches the upstream LFX webcal feed, rewrites the
 * non-conformant patterns Google Calendar chokes on, and returns a valid
 * text/calendar response.
 *
 * Secrets / env vars (configure via `wrangler secret put ...` or the CF dashboard):
 *   LFX_ICS_URL   Full URL of the upstream feed.
 *                 e.g. https://webcal.prod.itx.linuxfoundation.org/lfx/<sub-id>_sub
 *
 * Optional environment variables (plain vars, not secrets):
 *   UNTIL_CAP     Yyyymmdd'T'hhmmss'Z' upper bound for RRULE UNTIL.
 *                 Default: 20280101T000000Z.
 *   ACCESS_TOKEN  If set, requests must include ?token=<value> to succeed.
 *                 Optional obscurity for the public Worker URL.
 */

const DEFAULT_UNTIL_CAP = "20280101T000000Z";

export default {
  async fetch(request, env) {
    if (env.ACCESS_TOKEN) {
      const url = new URL(request.url);
      if (url.searchParams.get("token") !== env.ACCESS_TOKEN) {
        return new Response("Forbidden", { status: 403 });
      }
    }

    if (!env.LFX_ICS_URL) {
      return new Response(
        "LFX_ICS_URL is not configured. Set it with `wrangler secret put LFX_ICS_URL`.",
        { status: 500 },
      );
    }

    let upstream;
    try {
      upstream = await fetch(env.LFX_ICS_URL, {
        headers: {
          "User-Agent": "lfx-ics-normalizer/1.0 (Cloudflare Worker)",
          Accept: "text/calendar, */*;q=0.5",
        },
        cf: { cacheTtl: 900, cacheEverything: true },
      });
    } catch (err) {
      return new Response(`Upstream fetch failed: ${err}`, { status: 502 });
    }

    if (!upstream.ok) {
      return new Response(
        `Upstream returned ${upstream.status} ${upstream.statusText}`,
        { status: 502 },
      );
    }

    const raw = await upstream.text();
    const untilCap = env.UNTIL_CAP || DEFAULT_UNTIL_CAP;
    const cleaned = normalize(raw, untilCap);

    return new Response(cleaned, {
      status: 200,
      headers: {
        "Content-Type": "text/calendar; charset=utf-8",
        "Cache-Control": "public, max-age=900",
        "Content-Disposition": 'inline; filename="Calendar.ics"',
        "X-Normalizer": "lfx-ics-normalizer/1.0",
      },
    });
  },
};

function normalize(text, untilCap) {
  const beginIdx = text.indexOf("BEGIN:VCALENDAR");
  if (beginIdx > 0) text = text.slice(beginIdx);

  text = text.replace(/\r?\n[ \t]/g, "");

  const capYmd = untilCap.slice(0, 8);
  const emptyAttendee = /^ATTENDEE;VALUE=TEXT:\s*$/;
  const timestampTzid = /^(DTSTAMP|CREATED|LAST-MODIFIED);TZID=[^:]+:/;
  const untilRe = /UNTIL=(\d{8}T\d{6}Z?)/g;

  const kept = [];
  for (let line of text.split("\n")) {
    line = line.replace(/\r$/, "");
    if (emptyAttendee.test(line)) continue;
    line = line.replace(timestampTzid, "$1:");
    if (line.startsWith("RRULE:") || line.startsWith("EXRULE:")) {
      line = line.replace(untilRe, (_, v) => {
        if (!v.endsWith("Z")) v += "Z";
        return v.slice(0, 8) > capYmd ? `UNTIL=${untilCap}` : `UNTIL=${v}`;
      });
    }
    kept.push(line);
  }

  const encoder = new TextEncoder();
  const folded = [];
  for (let l of kept) {
    while (encoder.encode(l).length > 75) {
      let cut = 75;
      while (cut > 0 && encoder.encode(l.slice(0, cut)).length > 75) cut--;
      if (cut <= 0) break;
      folded.push(l.slice(0, cut));
      l = " " + l.slice(cut);
    }
    folded.push(l);
  }
  return folded.join("\r\n") + "\r\n";
}
