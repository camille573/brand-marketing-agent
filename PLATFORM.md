# Multi-brand marketing platform

This adds the campaign-execution foundation to the research tool. The default workspaces are Stellar Mill, In & Out, Pap Ten, and a placeholder for the future dropshipping brand. Names can be edited; the business details of the other brands are deliberately unset. Additional businesses can be added in the control room.

## Start the control room

```sh
cd /workspace/brand-marketing-agent
python3 -m unittest discover -s tests -v
python3 brand_server.py --monitor
```

Node is needed only for optional JavaScript syntax validation (`node --check public/control.js`). The application itself requires only Python 3.10+.

The control room binds to loopback on port 3001. It is a local application, not a public hosted service. It rejects other Host headers and cross-origin writes and uses a server-generated CSRF token. There is no multi-user authentication. Do not expose it publicly or remove its host checks; deploy behind authenticated HTTPS with a proper session/login design first. Start the research dashboard separately with `python3 marketing_agent.py serve` on port 3000 if its reference-import tools are needed. `/research` in the control room is the read-only Stellar Mill report.

## What is implemented

- Isolated brand settings, creative libraries, campaign plans, remote IDs, performance snapshots, and event history, stored in SQLite.
- Brand-specific secret variable names; token values remain in the secure runtime environment. Never paste a value into a settings field that asks for a variable **name**.
- Creative image upload and explicit approval/revocation. PNG/JPEG headers are checked locally; Meta must still validate the full image and ad.
- Local campaign planning with frozen brand, account, Page, offer, creative, audience, daily budget, and total-cap snapshots.
- Read-only account/Page/permission preflight before writes.
- Meta Traffic campaign creation, image upload, ad-creative creation, and ad creation. All campaign/ad-set/ad objects start paused.
- Explicit launch with an exact phrase containing brand, daily budget, and total campaign cap. The parent campaign is activated last.
- Launch checks for revoked/changed creative, modified image bytes, and remote budget/cap/country changes.
- Pause, manual performance refresh, and a history of execution failures. Returned remote IDs are saved after each successful step.
- Constrained per-brand commands: `check connection`, `show campaigns`, `refresh results`, `pause all campaigns`.
- Optional monitor: polls active or uncertain campaigns once per minute, records results/errors, and requests pause when reported lifetime spend reaches the saved total cap. It never creates or activates a campaign, increases budgets, or changes targeting on its own.

## Brand setup

Stellar Mill’s account and Page IDs come from previous successful read-only verification, not a claim of current authorization. Its offer is the free 60-second Tax Checkup, with contact details requested before results. Budget and target countries are unset. Other brands start with blank accounts, offers, budgets, and service areas. Each must be configured independently. Sharing a Meta credential between brands is possible only by deliberately choosing the same secure variable name; Page/account access still depends on Meta authorization.

This first version supports USD, country-wide audiences aged 18+, and website traffic optimized for link clicks. It does not optimize for completed checkups. The total campaign spend cap is required and is sent to Meta; daily budgets follow Meta’s averaging rules. The monitor is additional observation, not a precise real-time spend meter or a substitute for Meta’s own cap enforcement. Reporting may lag. Review applicable special-ad-category requirements before creating a campaign; this initial publisher sends an empty special-ad-category list and must not be used for campaigns requiring a regulated category.

## Live workflow

1. Select a brand and set its offer, HTTPS destination, ad account, Page, secure credential variable name, target countries, daily budget, and total campaign cap.
2. Run `check connection`. Fix Meta authorization/billing/asset assignments if it fails; don’t create duplicate secrets merely because a request is denied.
3. Upload finished images and primary text/headlines, then approve the exact creative. Earlier concepts rejected in chat have not been silently approved or loaded into this campaign library.
4. Build a local plan, review its settings, and create it paused in Meta.
5. Inspect it in Ads Manager and enter the displayed spending phrase to launch.
6. Refresh results or pause via the selected brand’s command box. With `--monitor`, observation continues only while this server is running.

## Failure recovery

Creation is one-shot per local plan. An incomplete attempt is blocked from automatic retries because a timed-out Meta call could have succeeded. Inspect the returned IDs and Ads Manager before deciding how to repair it. Do not blindly create a replacement plan after a timeout. A failed activation is marked `DELIVERY_UNCERTAIN`, not falsely marked paused. Use Pause or reconcile actual status in Meta. Plans are intentionally immutable snapshots; later brand edits do not change existing campaign budgets or destination URLs. New review/plans are required for revised creative.

The SQLite database, uploaded assets, and private reports live in ignored `.data/`. Back them up together for a real deployment; a GitHub checkout includes code, not private runtime data. Only one server/monitor process should manage a given database: operation locks are process-local. No distributed worker leasing, scheduled service supervision, credential rotation, or deployment backup system is included yet.

## Remaining work for a complete autonomous agent

This is the shared execution foundation and a limited command runner, not an open-ended conversational AI or unattended profit optimizer. There is no in-app model integration for strategic reasoning or image generation. Brand-specific approved policies, lead/purchase conversion tracking, evaluation of lead quality, a hosted authenticated UI, reliable worker supervision, backups, and refreshed Meta authorization are still needed before using it as a continuously running service. Arcads or another paid creative service is optional, not a dependency of this core.

All writes/launches were tested against a fake Meta transport. A live read-only check currently returns Meta error 200 (`Cannot call API ... on behalf of user`). No live Meta writes, ad acceptance, or delivery were verified. No campaign has been launched by this implementation.
