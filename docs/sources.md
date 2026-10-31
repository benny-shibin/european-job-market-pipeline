# Source evaluation and attribution

Reviewed 31 October 2026. Rules and APIs can change; check the official links before changing deployment or polling frequency.

| Source | Endpoint / scope | Limits and attribution |
| --- | --- | --- |
| Arbeitnow | `https://www.arbeitnow.com/api/job-board-api`, paginated source sample, default two pages | Preserve source and original URL. Default two-second inter-page delay. Describes source eligibility, not a complete European market census. |
| Jobicy | `https://jobicy.com/api/jobs?count=100&geo=europe`; cursor pagination through a rolling seven-day publication window | No API key. At most one new automated synchronization pass per hour; sequential cursor requests are permitted. Preserve Jobicy attribution and canonical Jobicy URL. Daily scheduling is comfortably within the limit. |

Jobicy's [official API and fair-use documentation](https://jobicy.com/jobs-rss-feed) permits research tools, internal dashboards, and displaying listings with attribution; it advises caching and retaining the source link. The [provider's reference repository](https://github.com/Jobicy/remote-jobs-api) describes permitted reuse and native ID deduplication. The website currently documents 100 per page; the repository mentions 200, so the adapter conservatively requests 100. Public responses may have publication delays. This pipeline uses no paid endpoint, token, or ATS-link purchase. Raw response envelopes are retained locally for replay; generated data is excluded from Git and images.

Jobicy geography is employment eligibility, not an office coordinate. `geo=europe` is an API filter, not an independent geocoding guarantee. Remote is true for this remote-only feed. Native integer IDs become strings without adding prefixes; the composite source/ID key prevents collisions. Title and company HTML entities are decoded; full original job descriptions and payloads remain in Bronze. Publication timestamps are parsed separately from ingestion timestamps.

The dashboard labels each listing with its source and retains the canonical URL, including CSV exports. Per-source history only starts once source-level snapshots exist. Earlier aggregate snapshots are preserved and never guessed into a source breakdown.

`duplicate_candidates` identifies exact normalized title/company/location matches across different sources. These are review candidates, not verified duplicate roles. Neither identities nor totals are collapsed. A source may expose multiple identical roles; descriptions, URLs and original records remain traceable.

Jobicy's rolling window means even a complete traversal cannot establish closure: a listing may simply age out. This project currently never assigns `confirmed_closed`. Any future explicit status-check integration must retain provider confirmation and distinguish `unknown` from `closed`.

Remotive was evaluated but not selected: its API-specific reuse rules and broader site terms require extra care around onward distribution. Jobicy provides clearer documented permission for this research/dashboard use.

## Requested LinkedIn and StepStone connectors

Official access reviewed again on 31 October 2026:

- [LinkedIn Job Posting API overview](https://learn.microsoft.com/en-us/linkedin/talent/job-postings/api/overview): approved partner access is required for these Talent Solutions integrations. Posting APIs manage employer jobs; they are not a documented free public search/download feed for arbitrary LinkedIn listings.
- [StepStone JobFeed XML guide](https://api.stepstone.com/knowledge-base/xml-guide-stepstone-de/): employer PUSH/PULL feeds submit job advertisements to StepStone. This is not a free job-search ingestion endpoint. No appropriate free public retrieval feed was identified in the reviewed official documentation.

Both appear under **Source connections & ingestion health** as **Access required**, and are excluded from live-source filters and ingestion totals. There is no fabricated connector, third-party scraper, or credential field claiming to unlock an unsupported search API. Enabling either provider requires documented permission and an actual job-retrieval feed/schema, followed by an adapter and contract tests. A generic posting API credential alone does not supply global job-search access.

To check current sources without fetching again:

```sh
python -m src.job_market.quality.source_health
```

The read-only report checks latest fetch completion, page/record counts, observation IDs, source identity, Silver/Gold content, and state/Silver fingerprints. It describes saved-data integrity, not a fresh network availability probe. A verified page-capped run remains a sampled traversal; it does not establish complete provider coverage. Raw record counts can exceed distinct IDs when a feed repeats a listing.
