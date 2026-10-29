# Video script: from zero to a running ecosystem with ecoctl

Working title: "Five templates: AI-enabled apps on EDB Postgres on AgentPG". Audience: people who need something similar to TrustDesk, PagerLens, CityPulse, PayBroker or ShopFront, and EDB colleagues. The five apps are not only demos of AgentPG: they are Apache 2.0 templates with sign-in, a model gateway, secrets, service-to-service calls, webhooks and a deploy process, ready to adapt for a real use case. The video is about the apps, how they integrate, the EDB features they use (one Postgres instead of a search stack, GIS server, message broker and external monitoring), how fast a whole fleet deploys, and the AIDB roadmap. ecoctl is an internal tool and gets about 20 s; the Ansible recipes get about 30 s. Narration is written for listening and spoken by Kokoro (local, US accent, speed 1.0 for this video, set in `docs/video/build.py`). Three voices, alternating male and female, never the same voice twice in a row: am_liam (opener), af_heart, am_eric; each scene names its voice in the `## <scene> <voice>` header of [video/narration.txt](video/narration.txt). Letter acronyms are respelled in `docs/video/pronounce.json` only (spaced, no hyphens; API is not respelled). Reference for every claim: [GUIDE.md](GUIDE.md) and [09-goals-and-acceptance.md](09-goals-and-acceptance.md).

Review cut (2026-10-29, take 6): about 7:30 at speed 1.0. Every slide and still is shown 1:1 and held perfectly still between keyframes (no pan, no zoom, no tint); long sentences are broken up by revealing or highlighting an element, switching to a crop, or changing the caption. Encoded with x264 crf 18, no B-frames, no mbtree or adaptive quantisation, and an I-frame exactly where the picture changes.

## Scene table

Times are cumulative. CALLOUTS says how each picture is labelled on screen; NARRATION mirrors narration.txt (`python3 docs/video/build.py --sync-script`).

| # | Time | ON-SCREEN ACTION | CALLOUTS | NARRATION (mirror of narration.txt) |
|---|---|---|---|---|
| 1 | 0:00 to 0:34 | Problem slide (sign-in, gateway, secrets, a database each: weeks of plumbing), the five apps (not only demos: templates, Apache License 2.0 for the five), what each template has, then the labelled map with the fourteen-minute fleet. | Slides | "You need an app with AI in it, on Postgres. With sign-in, access to a model, secrets, and a deploy. That is weeks of plumbing before the first useful screen. Here are five apps that already have all of it. They are not only demos of AgentPG. They are templates: take one, adapt it, and run it for a real use case. Full sign-in, a model gateway, secrets, service-to-service integration, and a full deploy process are already in place. The five apps are Apache 2.0. And the whole fleet, eight sandboxes with their databases, deploys from one manifest in about fourteen minutes." (102 words) |
| 2 | 0:34 to 1:19 | The ecosystem map in stages: PLATFORM (EDB Postgres on AgentPG), REFERENCE SCAFFOLDING (identity OIDC, llm-gateway, kit vault; swappable), SAMPLE APPS; then the calls: simulator to CityPulse to PagerLens, ShopFront to PayBroker and the webhook back, apps to the gateway, identity for everyone. | Category pills, legend; tag SLIDE | "Here is the map, with three labels. The platform is EDB Postgres on AgentPG: a database and a sandbox for every app, with the EDB extensions. Reference scaffolding is identity, the LLM gateway, and the kit vault. All swappable: any OIDC provider that sends the roles the apps expect, any OpenAI-compatible gateway, your own vault through the kit. The sample apps sit on top. The simulator sends readings to CityPulse. CityPulse reports trouble to PagerLens. ShopFront sends payments to PayBroker, and a signed webhook comes back. Every app asks the gateway for its model. And everything signs in through identity, with short-lived service tokens." (104 words) |
| 3 | 1:19 to 1:55 | TrustDesk: frame from the app video; CAPTURED stills of REQ-000016 (rejection, verified quotes, decision); frame from the app video (typo-tolerant search); CAPTURED masked views; diagram. Before/after: one Postgres instead of a separate search stack. | Tags: LOCAL / CAPTURED / SLIDE | "TrustDesk is a compliance workspace. An approver has a stack of employee requests, and a long policy handbook. A model checks each request against the policy. The answer lists numbered reasons, and each one quotes the policy. The app checks every quote against the document, word for word. A person still decides. Instead of a separate search stack, search is pgvector, full-text, and pg_trgm in one Postgres, so typos still find the right policy. Personal data sits behind masked views, not behind code in every service. It signs in through identity, and asks the gateway for its model." (98 words) |
| 4 | 1:55 to 2:36 | PagerLens: frames from the app video (incident page, report with manual steps, Ask PagerLens with the labelled stand-in model); tcn instead of a message broker; diagram. | Tags: LOCAL / SLIDE | "PagerLens is an incident investigator. It is three in the morning, and an alert fires. It gathers the evidence, ranks likely causes, and shows the data that supports or refutes each one. It can read, but it cannot restart or roll back anything. Ask PagerLens answers in plain English, and each citation is marked verified against the database. Here, a labelled stand-in model answers. Live incident pages come from Postgres itself, through tcn notifications, so there is no message broker. Tuning is propose and approve: nothing is applied by default. It receives alerts from CityPulse, and signs in through identity." (100 words) |
| 5 | 2:36 to 3:27 | CityPulse and the Simulator: frames from the app video (overview, burst); diagram CityPulse to PagerLens; CAPTURED Brussels map (real commune outlines from OpenStreetMap, simulated sensors inside them) and Explain cards (five data models, one SQL statement; PostGIS distances in metres: 101, 176, 249 m), 29 Oct 2026; PostGIS instead of a GIS server. | Tags: LOCAL / CAPTURED (29 Oct 2026) / SLIDE | "CityPulse watches a smart city. Thousands of simulated sensors report through a real collection path, and a status engine turns the readings into a city verdict. The simulator feeds it, with a short-lived token from identity. Press burst, and the city degrades. CityPulse reports its own health to PagerLens, and PagerLens opens an incident. The city is real: Brussels, with real commune outlines from OpenStreetMap, and simulated sensors placed inside them. One SQL statement answers a district question across relational, time series, JSON, geographic, and vector data. PostGIS answers distance questions in metres: the nearest healthy sensors are 101, 176, and 249 metres away. It replaces a separate GIS server. Readings are partitions, and sensor metadata is JSONB." (118 words) |
| 6 | 3:27 to 4:06 | PayBroker: diagram; frames from the app video (hash-chained ledger, tamper named); CAPTURED Database page with the masking statements; row-level security and masked views in the database, not in every service; diagram of the webhook. | Tags: LOCAL / CAPTURED / SLIDE | "PayBroker is a payment broker. One API in front of several simulated processors, with smart routing and a three-tier fraud engine. It takes test cards only. Every decision goes into a hash-chained audit ledger, inside Postgres. Change one old row, and verify names the first broken entry. Row-level security keeps each merchant to its own rows, and masked views hide what the read-only user should not see. Both live in the database, not in every service. Payments are idempotent: a retry never charges twice. ShopFront calls it, and PayBroker sends signed webhooks back." (93 words) |
| 7 | 4:06 to 4:38 | ShopFront: frames from the app video (did you mean, hybrid search, checkout token, payment trail); live order updates through tcn instead of a broker; diagram. | Tags: LOCAL / SLIDE | "ShopFront is a small web shop. It shows an ordinary app, on a database that works. Search forgives typos: kettel finds kettle. A vague query like morning caffeine matches by words and by meaning, in one SQL statement, with no separate vector store. At checkout, the card goes from the browser straight to PayBroker, so the shop never sees the number. A signed webhook comes back, and the order page updates live through tcn, with no message broker. It is the easiest one to copy: a catalog, a cart, orders, and payments through another service." (95 words) |
| 8 | 4:38 to 5:10 | Text slide: sign-in (OIDC, roles), one gateway door, service tokens with scopes, signed webhooks, the kit vault, the shared kit extras (Insights tab, health, masking); one line highlighted per sentence. | Tag: SLIDE | "How do they integrate? Five things, all swappable. Sign-in is OIDC, with roles per app. Model access is one gateway door, with a key and limits for each app. Service calls use short-lived tokens that carry scopes, so CityPulse may call PagerLens, and nothing else. Webhooks are signed, between ShopFront and PayBroker. Secrets live in the kit vault, never in code or in files. And every app gets the same extras from the shared kit: an Insights tab, health checks, and masking." (82 words) |
| 9 | 5:10 to 5:53 | CAPTURED Database pages (edb_stat_monitor, edb_pg_tuner, query_advisor, masked views, PayBroker monitor); slide 'One Postgres instead of five tools' (search stack, GIS server, message broker, external monitoring, masking in every service); slide on the two MCP routes (docs/08). | Tags: CAPTURED / SLIDE | "Now the platform. Every app has a Database page, fed by edb_stat_monitor for top queries, edb_pg_tuner for settings, and query_advisor for index suggestions. No external monitoring or tuning tool. These are suggestions only. A person decides what to change. Masking is Postgres done properly. Base tables are revoked, masked views are the way in, and the kit keeps it in force. The read-only user an AI connects with cannot read what is masked. So it is one Postgres, instead of a search engine, a vector database, a map server, and a message queue. Two MCP routes exist: each app's own MCP server, and AgentPG's database MCP." (106 words) |
| 10 | 5:53 to 6:28 | Roadmap slides: EDB AIDB pipelines and in-database agents as what we are integrating next (docs/03, docs/06). Nothing is shown running. | Tag: ROADMAP | "Next on the roadmap: EDB AIDB. We are integrating it into the apps. The plan is pipelines that chunk and embed documents inside the database, and keep them in step when the source table changes. So the apps stop computing embeddings in application code. TrustDesk policies, PagerLens runbooks and incident memory, and CityPulse questions in plain language are the first candidates. And agents that run inside Postgres, with tools that act as a database role. The grant, not a prompt, sets what an AI may touch." (86 words) |
| 11 | 6:28 to 7:02 | Run them yourself: README quick start and Dockerfile; terminal with the real offline output of `.venv/bin/ecoctl validate` and captions (internal tool, built to show how to wrap the agentpg client); slides: the Ansible recipes (tested, whole fleet from empty in about fourteen minutes; a sample to reuse: same manifest, ansible-core only, site, verify, destroy). | Tags: SLIDE; terminal = real offline output | "To run them: each repository has a README quick start and a Dockerfile. ecoctl is an internal tool. One manifest, one command, and the whole fleet is deployed. It was built to show how you can wrap your own solution around the agentpg client. There are also Ansible recipes that deploy the whole fleet on AgentPG. They are tested, and they work: all eight apps, deployed from empty, in about fourteen minutes. They are a sample to reuse, to fit your company's own deployment. The same manifest as ecoctl, ansible-core only, and three playbooks: site, verify, and destroy." (98 words) |
| 12 | 7:02 to 7:30 | Recap diagram (five apps are templates, take one, swap identity/gateway/vault, adapt, run) and the end card. | Slides | "So: five apps, and they are not only demos. They are templates. Take one. Swap identity, the gateway, or the vault for yours. Adapt it, and run it for a real use case. Sign-in, the gateway, secrets, service-to-service calls, and the deploy are already in place, on EDB Postgres on AgentPG. One manifest, one command, a whole fleet. Apache 2.0 for the five apps. Thanks for watching." (67 words) |

### Where the pictures come from

| Label on screen | Source |
|---|---|
| CAPTURED from the live AgentPG fleet, 28 Oct 2026 / 29 Oct 2026 | stills in `videos/live-captures/` (28 Oct: TrustDesk REQ-000016 and Database pages, PayBroker Database page; 29 Oct: CityPulse Brussels map and Explain cards); no Gotham or Metropolis still is used |
| LOCAL: frame from the app video | frames of our own recordings `videos/review/<app>.mp4`, extracted with ffmpeg into `videos/live-captures/app-video-frames/` (local runs, not the live fleet; the burned-in callouts of those videos are part of the frame) |
| SLIDE / ROADMAP | drawn by `slides.py` from the docs: map, integration list, before/after list, Ansible recipes, MCP routes, AIDB roadmap; no invented output |
| terminal | real offline output of `ecoctl validate` (scene 11) |

Licence check (2026-10-29): TrustDesk, PagerLens, CityPulse, PayBroker and ShopFront have a LICENSE file with Apache License 2.0 and `license = "Apache-2.0"` in pyproject.toml; so do the ecosystem, identity, llm-gateway and agentpg-kit repositories. CityPulseSimulator is MIT, so the video says "five apps, Apache 2.0" and does not claim the simulator.

Not claimed anywhere: HA or failover, AIDB working today, autonomous database changes.

## Captured output (rehearsal, 2026-10-27)

Real output of the commands above, from the recorder (manifest of that day; re-capture after any manifest change). Commands run in an empty temporary directory.

`.venv/bin/ecoctl validate` (exit 0):

```
ok: fleet 'eco', identity + 8 apps (llmgw, pagerlens, citypulse, paybroker, trustdesk, cpsim, pagerlens-agent, shopfront)
```

`.venv/bin/ecoctl plan --only trustdesk` (exit 0; `apply --only trustdesk` prints the same lines and ends with the dry-run sentence instead of the last line):

```
 1. Identity provider: database and sandbox
      agentpg create --name eco-identity-db --size small --json
      agentpg sandbox create eco-identity --image ghcr.io/elchinoo/identity:latest --instance '<instance-id>' --port 8000 --auto-pause=false --env 'IDENTITY_API_KEY=***'
 2. Identity provider: wait until it is ready and pin its issuer URL
      GET <identity>/readyz
      GET <identity>/.well-known/openid-configuration  (the first request fixes the issuer)
 3. LLM gateway: register with the identity provider
      identity: register web client 'llmgw' with roles admin, operator, viewer
 4. LLM gateway: database and sandbox
      agentpg create --name eco-llmgw-db --size small --json
      agentpg sandbox create eco-llmgw --image ghcr.io/elchinoo/llm-gateway:latest --instance '<instance-id>' --port 8000 --auto-pause=false --env 'LLMGW_ENCRYPTION_KEY=***' --env 'LLMGW_API_KEY=***' --env LLMGW_DEFAULT_CHAT_ALIAS=chat-default --env LLMGW_DEFAULT_EMBED_ALIAS=embed-default --env LLMGW_DEFAULT_EMBED_DIM=1536 --env 'LLMGW_OIDC_ISSUER=<identity url>' --env LLMGW_OIDC_CLIENT_ID=llmgw --env 'LLMGW_OIDC_CLIENT_SECRET=***'
 5. LLM gateway: point its sign-in at the real URL
      identity: set redirect URI to <llmgw url>/auth/callback and its launcher URL
 6. TrustDesk: register with the identity provider
      identity: register web client 'trustdesk' with roles admin, approver, viewer
 7. TrustDesk: LLM gateway client and key
      gateway llmgw: ensure client 'trustdesk' (aliases chat-default, embed-default; 60 requests/min; 5,000,000 tokens/month, hard)
      an existing client keeps its limits (`ecoctl llm sync` changes them) but gets any missing default alias
      keep its key in the state file (shown once by the gateway; rotated if lost)
 8. TrustDesk: database and sandbox
      agentpg create --name eco-trustdesk-db --size small --json
      agentpg extensions create vector '<instance-id>'
      agentpg extensions create pg_trgm '<instance-id>'
      agentpg sandbox create eco-trustdesk --image ghcr.io/elchinoo/trustdesk:latest --instance '<instance-id>' --port 8000 --auto-pause=false --env AUTO_SEED=true --env 'TRUSTDESK_ENCRYPTION_KEY=***' --env LLM_CHAT_MODEL=chat-default --env EMBEDDING_MODEL=embed-default --env EMBEDDING_DIM=1536 --env 'TRUSTDESK_OIDC_ISSUER=<identity url>' --env TRUSTDESK_OIDC_CLIENT_ID=trustdesk --env 'TRUSTDESK_OIDC_CLIENT_SECRET=***' --env 'LLM_BASE_URL=<llmgw url>/v1' --env 'LLM_API_KEY=***'
 9. TrustDesk: point its sign-in at the real URL
      identity: set redirect URI to <trustdesk url>/auth/callback and its launcher URL
10. Access: group 'ecosystem-admins'
      *: admin
11. Access: group 'support'
      trustdesk: approver

Nothing was changed. `ecoctl apply --execute` runs this plan; it is safe to run again.
```

`.venv/bin/ecoctl apply --only trustdesk --execute`, after the same plan, answered `n` (exit 1):

```
This creates databases and sandboxes on your AgentPG account (they may use credits) and writes secrets to .ecosystem/state.json (git-ignored).
Continue? [y/N] Cancelled.
```

`.venv/bin/ecoctl recreate paybroker`, answered `n` (exit 1), against the fixture state (apps identity, llmgw, paybroker, cpsim and shopfront marked deployed with fake ids). With an empty state it only prints `Nothing deployed to recreate.`, so the cascade list needs a state that lists the apps; on a live fleet the ids are real:

```
Also recreated because they call or use paybroker: cpsim, shopfront
Will delete the sandboxes (their databases are kept): cpsim (sbx-fixture-04), paybroker (sbx-fixture-03), shopfront (sbx-fixture-05)
Continue? [y/N] Cancelled.
```
