# Video script: from zero to a running ecosystem with ecoctl

Working title: "Five templates: AI-enabled apps on EDB Postgres on AgentPG". Audience: people who need something similar to TrustDesk, PagerLens, CityPulse, PayBroker or ShopFront, and EDB colleagues. The five apps are Apache 2.0 templates with sign-in, a model gateway, secrets, service-to-service calls, webhooks and a deploy process; the video is about the apps, how they integrate, and the EDB features they use. ecoctl is an internal tool and gets about 20 s; the Ansible recipe gets its own scene. Narration is written for listening and spoken by Kokoro (local, US accent, shared speed default 1.08). Three voices, alternating male and female, never the same voice twice in a row: am_liam (opener), af_heart, am_eric; each scene names its voice in the `## <scene> <voice>` header of [video/narration.txt](video/narration.txt). Reference for every claim: [GUIDE.md](GUIDE.md) and [09-goals-and-acceptance.md](09-goals-and-acceptance.md).

Review cut (2026-10-29, apps-first re-cut): about 7:20. No picture stays identical for more than about 8 s (freezedetect, -50 dB and -60 dB); long pictures flip with a faint amber tint.

## Scene table

Times are cumulative. CALLOUTS says how each picture is labelled on screen; NARRATION mirrors narration.txt (`python3 docs/video/build.py --sync-script`).

| # | Time | ON-SCREEN ACTION | CALLOUTS | NARRATION (mirror of narration.txt) |
|---|---|---|---|---|
| 1 | 0:00 to 0:32 | Problem slide (sign-in, gateway, secrets, a database each: weeks of plumbing), the five apps with 'Apache License 2.0', the list of what each template has, then the labelled map. | Slides | "You need an app with AI in it, on Postgres. With sign-in, access to a model, secrets, and a deploy. That is weeks of plumbing before the first useful screen. Here are five sample apps that already have all of it. They are Apache 2.0. Take one, fork it, run it, and change it. They are templates, not demos. Each one has sign-in, a model gateway, secrets, service-to-service calls, webhooks, and a full deploy process. They run on EDB Postgres on AgentPG. In this video: what each app does, how they fit together, and which EDB features they use." (99 words) |
| 2 | 0:32 to 1:18 | The ecosystem map built in stages: PLATFORM (EDB Postgres on AgentPG), REFERENCE SCAFFOLDING (identity OIDC, llm-gateway, kit vault; swappable), SAMPLE APPS; then the calls: simulator to CityPulse to PagerLens, ShopFront to PayBroker and the webhook back, apps to the gateway, identity for everyone. | Category pills, legend; tag SLIDE | "Here is the map, with three labels. The platform is EDB Postgres on AgentPG: a database and a sandbox for every app, with the EDB extensions. Reference scaffolding is identity, the LLM gateway, and the kit vault. All swappable. Any OIDC provider that sends the roles the apps expect. Any OpenAI-compatible gateway. Your own vault, through the kit. The sample apps sit on top. Now, who calls whom? The simulator sends readings to CityPulse. CityPulse reports trouble to PagerLens. ShopFront sends payments to PayBroker, which sends a signed webhook back. TrustDesk, PagerLens, and the others ask the gateway for a model. And everything signs in through identity, with short-lived service tokens." (111 words) |
| 3 | 1:18 to 1:54 | TrustDesk: frame from the app video (requests with a first opinion); CAPTURED stills of REQ-000016 (rejection, verified quotes, decision); frame from the app video (typo-tolerant search); CAPTURED masked views; the integration diagram. | Tags: LOCAL / CAPTURED / SLIDE | "TrustDesk is a compliance workspace. An approver has a stack of employee requests, and a long policy handbook. A model checks each request against the policy. The answer lists numbered reasons, and each one quotes the policy. The app checks every quote against the document, word for word. A person still decides. On the Postgres side, search mixes pgvector with full-text, and pg_trgm forgives typos. Personal data sits behind masked views. It signs in through identity, uses the gateway for its model, and takes requests from other apps through its API. It also has an MCP server." (97 words) |
| 4 | 1:54 to 2:33 | PagerLens: frames from the app video (incident page, report with manual steps, Ask PagerLens with the labelled stand-in model); the integration diagram. | Tags: LOCAL / SLIDE | "PagerLens is an incident investigator. It is three in the morning, and an alert fires. It gathers the evidence, ranks likely causes, and shows the data that supports or refutes each one. It can read, but it cannot restart or roll back anything. Ask PagerLens answers in plain English, and each citation is marked verified against the database. Here, a labelled stand-in model answers: the wiring is real, the words are canned. Tuning is propose and approve. Nothing is applied by default. Live incident pages come from Postgres itself, through tcn. It receives alerts from CityPulse, and signs in through identity." (101 words) |
| 5 | 2:33 to 3:07 | CityPulse and the Simulator: frames from the app video (overview, burst); the diagram CityPulse to PagerLens; CAPTURED Explain card (five data models, one SQL statement). The old Gotham map still is not used. | Tags: LOCAL / CAPTURED / SLIDE | "CityPulse watches a smart city. Thousands of simulated sensors report through a real collection path, and a status engine turns the readings into a city verdict. The simulator feeds it, with a short-lived token from identity. Press burst, and the city degrades. CityPulse reports its own health to PagerLens, and PagerLens opens an incident. One SQL statement answers a district question across relational, time series, JSON, geographic, and vector data. PostGIS does the geography. Readings are time-series partitions, and sensor metadata is JSONB." (83 words) |
| 6 | 3:07 to 3:41 | PayBroker: diagram; frames from the app video (hash-chained ledger, tamper named); CAPTURED Database page with the masking statements; diagram of the webhook. | Tags: LOCAL / CAPTURED / SLIDE | "PayBroker is a payment broker. One API in front of several simulated processors, with smart routing and a three-tier fraud engine. It takes test cards only. Every decision goes into a hash-chained audit ledger, inside Postgres. Change one old row, and verify names the first broken entry. Row-level security keeps each merchant to its own rows. Masked views hide what the read-only user should not see. And payments are idempotent: a retry never charges twice. ShopFront calls it, and PayBroker sends signed webhooks back." (84 words) |
| 7 | 3:41 to 4:13 | ShopFront: frames from the app video (did you mean, hybrid search, checkout token, payment trail); diagram. | Tags: LOCAL / SLIDE | "ShopFront is a small web shop. It shows an ordinary app, on a database that works. Search forgives typos: kettel finds kettle. A vague query like morning caffeine matches by words and by meaning, in one SQL statement, with no separate vector store. At checkout, the card goes from the browser straight to PayBroker, so the shop never sees the number. A signed webhook comes back, and the order page updates live. It is the easiest one to copy: a catalog, a cart, orders, and payments through another service. A customer assistant even answers: where is my order?" (98 words) |
| 8 | 4:13 to 4:39 | Text slide: sign-in (OIDC, roles), one gateway door, service tokens with scopes, signed webhooks, the kit vault; one line highlighted per sentence. | Tag: SLIDE | "How do they integrate in practice? Five things, all swappable. Sign-in is OIDC, with roles per app. Model access is one gateway door, with a key and limits for each app. Service calls use short-lived tokens that carry scopes, so CityPulse may call PagerLens, and nothing else. Webhooks are signed, between ShopFront and PayBroker. Secrets live in the kit vault, never in code or in files." (66 words) |
| 9 | 4:39 to 5:13 | CAPTURED Database pages (edb_stat_monitor, edb_pg_tuner, query_advisor, masked views, PayBroker monitor); slide on the two MCP routes (docs/08). | Tags: CAPTURED / SLIDE | "Now the platform. Every app has a Database page, fed by three EDB extensions: edb_stat_monitor for top queries, edb_pg_tuner for settings, and query_advisor for index suggestions. These are suggestions only. A person decides what to change. Masking is plain Postgres, done properly. Base tables are revoked, masked views are the way in, and the kit keeps it in force. The read-only user an AI connects with cannot read what is masked. Two MCP routes exist: each app's own MCP server, and AgentPG's database MCP." (84 words) |
| 10 | 5:13 to 5:34 | Slide: Apache 2.0, README quick start, Dockerfile; terminal with the real offline output of `.venv/bin/ecoctl validate` and captions (internal tool, built to show how to wrap the agentpg client); slide: two ways to deploy. | Tags: SLIDE; terminal = real offline output | "To run them: each repository is Apache 2.0, with a README quick start and a Dockerfile. ecoctl is an internal tool. It deploys the whole fleet from one manifest. It was also built to show how you can wrap your own solution around the agentpg command line client. But for your own infrastructure, there is a second way." (58 words) |
| 11 | 5:34 to 6:58 | Ansible (docs/07, ansible/): slides for what it is, the commands (text, not run), the destroy guards, throwaway.sh; terminal with the real offline `ansible-playbook <playbook> --syntax-check` output; the verified result of the complete eight-app apply on the throwaway fleet `ecoans` (29 Oct 2026: one run, 14 minutes, `ok=367 changed=96 failed=0`; second run `ok=254 changed=0 failed=0`; verify passes; guarded destroy returns the account to its 9 instances), and one closing line for what is not verified. | Recap counters tagged CAPTURED from the real run; the rest SLIDE | "Ansible is the way to deploy with your own infrastructure. It is a recipe in the ansible folder of the ecosystem repository, and it deploys the same fleet on AgentPG. Use it when your team already runs everything through Ansible. It reads the same manifest as ecoctl, so nothing is written twice. It needs ansible-core only, no collections and no extra packages, and it runs from your machine against AgentPG. Because it is Ansible, you can tag a stage, run a check first, keep your values in Vault, and put it in your own pipeline. Three playbooks. Site creates the fleet: identity, the gateway, each app, then access and connections. Verify checks it. Destroy removes it. Destroy is guarded. It refuses unless you pass the fleet name as confirmation. It deletes exact names only, and it prints the list first. There is also a small throwaway script, with apply, verify, preview, and destroy, on a separate fleet name, for a safe first try. Offline, all three pass a syntax check. And it works. We ran it for real, on a throwaway fleet: all eight apps, deployed from empty, in one run, in fourteen minutes. A real AgentPG account, with real databases. A second run changed nothing. Verify passed: every sandbox answers, the gateway clients work, the identity registry matches, and masking drift is zero. The guarded teardown then returned the account to where it started. Not verified yet: the hosts target on real hosts, and recreate on a real fleet." (250 words) |
| 12 | 6:58 to 7:20 | Diagram with the legend; the end card. | Slides | "So: five apps, each one a template. Fork one. Swap identity, the gateway, or the vault for yours. Deploy it with the Ansible recipe, or with ecoctl. Then change it into the thing you need. All of it on EDB Postgres on AgentPG, with the extensions already there. Thanks for watching. The guides are linked below." (56 words) |

### Where the pictures come from

| Label on screen | Source |
|---|---|
| CAPTURED from the live AgentPG fleet, 28 Oct 2026 | stills in `videos/live-captures/` (TrustDesk REQ-000016 and Database pages, PayBroker Database page, CityPulse Explain, the text of `ecoctl insights`); the old Gotham map still is not used |
| LOCAL: frame from the app video | frames of our own recordings `videos/review/<app>.mp4`, extracted with ffmpeg into `videos/live-captures/app-video-frames/` (local runs, not the live fleet; the burned-in callouts of those videos are part of the frame) |
| SLIDE | drawn by `slides.py` from the docs: map, integration list, Ansible commands, MCP routes; no invented output |
| CAPTURED from the real run of 29 Oct 2026 | only the two Ansible `ok=... changed=... failed=0` counters (docs/07 banner, docs/09 G9) |
| terminal | real offline output: `ecoctl validate` (scene 10) and `ansible-playbook <playbook> --syntax-check` on a copy of `ansible/` (scene 11) |

Licence check (2026-10-29): TrustDesk, PagerLens, CityPulse, PayBroker and ShopFront have a LICENSE file with Apache License 2.0 and `license = "Apache-2.0"` in pyproject.toml; so do the ecosystem, identity, llm-gateway and agentpg-kit repositories. CityPulseSimulator is MIT, so the video says "five apps, Apache 2.0" and does not claim the simulator.

Not claimed anywhere: HA or failover, AIDB, autonomous database changes.

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

## Not verified

- The Ansible `eco_target: hosts` path on real hosts, and `eco_env_change: recreate` on a real fleet (stated once, at the end of scene 11).
- Scene 10's `ecoctl validate` output is the real offline output; no `apply --execute`, `status` or sign-in is shown in this cut.
- Frames from the app videos are local runs; only the files in `videos/live-captures/` (not in `app-video-frames/`) come from the live fleet.
