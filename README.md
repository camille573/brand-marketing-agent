# Stellar Mill ad research agent

A subscription-free research tool for Stellar Mill’s Tax Checkup campaign. Python 3.10+; no external packages required.

## What it does

- Retrieves the live offer and IRS and public competitor reference pages, recording source URLs, timestamps and failures.
- Produces three evidence-linked static-ad briefs and image-generation prompts when the offer can be verified. These are rule-based test hypotheses, not proven winners or automated visual critiques.
- Links directly to competitor image-ad searches in Meta’s free Ad Library.
- Imports actual competitor copy and notes, stores source links, and identifies question/list/offer framing and financial claims requiring verification.
- Analyzes Ads Manager CSVs, distinguishing link CTR from website leads and leaving missing conversion data unknown.
- Attempts Meta’s official Ad Library API and can retrieve the connected account’s historical ads and results when authorization permits it.

## Run

Use the existing isolated checkout; a second worktree is unnecessary.

```sh
cd /workspace/brand-marketing-agent
python3 -m unittest discover -s tests -v
python3 marketing_agent.py research
python3 marketing_agent.py serve
```

The local research dashboard listens on port 3000, loopback only. Run locally or through your environment’s supported authenticated access. Do not expose it publicly: it has no user login. `research/report.html` is also a standalone read-only report you can download and open in a browser. Import/refresh controls are enabled only when served by the application. No paid account, API key or subscription is required for public source retrieval, reference imports, CSV analysis, or brief generation.

A checked-in `research/latest.json` and `research/report.html` contain an actual source-retrieval run. Re-run research to refresh them. `.data/` contains ignored local reference imports and reports. Do not commit private account exports.

## Meta access

`META_ACCESS_TOKEN` is read from the secure environment binding, never hardcoded, printed, or written to reports. API version defaults to `v23.0` and is configurable with `META_API_VERSION`. Fetch your own data with:

```sh
python3 marketing_agent.py own-ads --account 506078191226626
```

Pagination is capped at 100 records and reports when capped; saved data never includes token-bearing pagination URLs. This code makes read-only Meta requests. It has no launch, budget, or spending endpoint.

The current connection rejected BOTH own-account research and competitor Ad Library queries with Meta error 200 (“Cannot call API … on behalf of user”). This is an authorization blocker; it does not establish that competitors have no ads, that access is permanently unavailable, or that the token value is malformed. Restore app/user/asset authorization through Meta’s supported flow, then retry. Ad Library access and country/category coverage have additional limits; a Marketing API token alone does not guarantee broad US commercial-ad research.

Competitor performance is unknown. Longevity, duplicated creative and spend are clues, not verified conversion rates. Native results can be analyzed only when available and must retain the appropriate date range, attribution and conversion definition.

## Current scope

This is the research component, with original creative briefs and prompts. It does not call a paid image API, scrape restricted Facebook pages, or claim to visually analyze images it has not seen. The assistant can use the briefs with its available image generator. Adding paid Arcads generation is optional and requires separate authorization and credits.

On inspection of this restored environment, the checkout contained only the original README; the earlier chat-built campaign application was absent. This rebuild is committed in Git to retain it. It does not claim to restore that application's launch controls or deployment. GitHub publication and a user-accessible hosted deployment are separate from local implementation.
