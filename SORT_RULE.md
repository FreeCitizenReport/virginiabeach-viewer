# Commonwealth Citizens Legal News — SORT RULE

Canonical rule set, confirmed by Van 2026-04-18. Do NOT change without Van's explicit sign-off.

## The four rules

**Rule 1 — Mugshot is a FILTER, not a sort bucket.** Records without a mugshot are excluded
from the feed entirely. They do not rank last; they are not shown at all. A record joins
the feed only once it has a photo.

**Rule 2 — Custody status is irrelevant to ordering.** Released and in-custody records
intermix freely. (Users may filter by custody on the Browse page, but sort order itself
is custody-agnostic.)

**Rule 3 — Primary sort key: `retrievedAt` DESCENDING.** Most recently retrieved at top.
At sort time we compute:

    retrievedAt = min(firstSeenAt, bookDate + 24h)

The 24h clamp guards against new-source onboarding, which stamps `firstSeenAt = now`
on every ingested record regardless of how old the booking is. Without the clamp, a
5-day-old booking retrieved today for the first time would outrank a booking that
actually happened today. The clamp caps every record's retrievedAt at 24h after its
bookDate — the latest plausible moment a well-behaved scraper would have retrieved
it on the original booking day.

**Rule 4 — Tiebreakers** apply only when retrievedAt values are literally equal (rare —
these are second-precision timestamps): (a) bookDate DESC, (b) name A-Z alphabetically
for stability.

## Data-layer corollary

The clamp is a viewer-side guard. The source of truth is `firstSeenAt` in each jail's
`data.json`. Whenever a new jail source is onboarded, the scraper MUST backfill
`firstSeenAt` on pre-existing records to match `bookDate`, not the onboarding timestamp.
See the `peninsula-viewer` repo for the backfill pattern.

## Canonical comparator

```js
RECORDS = RECORDS.filter(function(r) { return r.mugshot; });
RECORDS.sort(function(a, b) {
  var fa = a.firstSeenAt ? Date.parse(a.firstSeenAt) : NaN;
  var fb = b.firstSeenAt ? Date.parse(b.firstSeenAt) : NaN;
  var ba = parseDate(a.bookDate), bb = parseDate(b.bookDate);
  var capA = isNaN(ba) ? Infinity : ba + 86400000;
  var capB = isNaN(bb) ? Infinity : bb + 86400000;
  var ra = isNaN(fa) ? (isNaN(ba) ? 0 : ba) : Math.min(fa, capA);
  var rb = isNaN(fb) ? (isNaN(bb) ? 0 : bb) : Math.min(fb, capB);
  if (ra !== rb) return rb - ra;
  var bd = (isNaN(bb) ? 0 : bb) - (isNaN(ba) ? 0 : ba);
  if (bd) return bd;
  var an = (a.name || "").toUpperCase(), bn = (b.name || "").toUpperCase();
  return an < bn ? -1 : an > bn ? 1 : 0;
});
```

## Canonical symptom of regressions

If FARMER, CLIFTON (Peninsula bookDate 04/13/2026) appears above PRESSLEY, ALEX
(Peninsula bookDate 04/18/2026) on 2026-04-18, the clamp or the upstream `firstSeenAt`
backfill is broken.

## History

- `1229ac1d` (2026-04-18): initial scaffold — simple bookDate DESC.
- `8345feae` (2026-04-18): introduced firstSeenAt as primary key — corrupted by Peninsula onboarding.
- `19e270b3` (2026-04-18): reverted to bookDate DESC. Wrong rule per Van — retrievedAt should be primary.
- (this commit) (2026-04-18): retrievedAt DESC with 24h clamp + mugshot filter + custody-agnostic. Confirmed.
