# Video script: from zero to a running ecosystem with ecoctl

Working title: "One file, eight apps: deploying the AgentPG ecosystem with ecoctl". Target length 7:30 (acceptable range 6:00 to 8:00). Audience: customers
evaluating Postgres + pgvector + MCP + AI on AgentPG, and EDB colleagues who will run the fleet themselves. Narration is written for listening (short sentences, one idea each) and spoken by neural voices (Kokoro, local, US accent, speed 0.95; voice plan: scenes 1-2 am_michael, 3-4 af_heart, 5-8 am_eric, 9-11 af_bella, 12 am_michael; set in the `## <scene> <voice>` headers of narration.txt):
about 1,130 words, 7:48 for the review cut with pauses; each scene's word count is given so a re-cut can be timed. The text lives in [video/narration.txt](video/narration.txt); this column mirrors it (`python3 docs/video/build.py --sync-script`). Reference for every claim: [GUIDE.md](GUIDE.md). The terminal output quoted here was captured from real runs on 2026-10-27 (see "Captured output").

## Prerequisites (for the person recording)

- A fresh clone of this repository in a clean directory with **no** `.ecosystem/` (so `plan` shows a first deployment), `uv venv && uv pip install -e ".[dev]"` done, terminal font 18 pt or larger, 1920x1080, light or dark theme but the same in every scene.
- For the live segment (scenes 7 to 9) only: `agentpg login` done on a **dedicated demo account**, images public (GUIDE section 5.2), and a separate manifest copy with `fleet: demo` so names never show your working fleet.
- A browser profile signed in to nothing else, with notifications off. The identity admin key, database keys, `lg_` keys and merchant keys must never be on screen: do not run `ecoctl env ... --reveal`, `ecoctl db`, or `cat .ecosystem/state.json` while recording.
- A model path for the demo fleet: the gateway's built-in `echo` provider (canned answers, fine for showing wiring) or a real provider you have checked first. On 2026-10-25 the real provider answered HTTP 402 (see doc 01); check `ecoctl llm test trustdesk` before recording the TrustDesk moment.

## Recording plan: what is live, what is mocked

A live `apply --execute` takes many minutes (each sandbox waits up to 7 minutes for a URL), creates real databases, prints new URLs every time, and is not repeatable. So the video is built from two kinds of footage.

| Kind | Scenes | How | Why it is repeatable |
|---|---|---|---|
| **Offline, re-recordable at will** | 2-5, 12 (terminal parts) | `validate`, `plan --only`, `apply --only` (dry run) and its `--execute` answered `n`, run against the manifest and an empty state directory; `recreate paybroker` answered `n` needs a state that lists deployed apps, so it runs against a fixture state with made-up sandbox ids (no secrets). `docs/video/record.sh` does exactly this and records it (below). `env` is not used in any scene. These commands read only the manifest and the state file; `plan` and the dry run make no call to AgentPG or any service (checked in `cli.py` and `plan.py`). Secrets print as `***` | Same output every take, no account, no cost; rehearsed 2026-10-27 |
| **Pre-recorded live segment** | 7-9 | One real run on the demo account, screen-recorded once and edited: `apply --execute --yes --only trustdesk` (identity, gateway, TrustDesk), cut the waits to a timer overlay ("about 6 minutes, sped up"), then `ecoctl status`. A second take of `ecoctl recreate paybroker` showing the cascade list | The run exists as footage; viewers see real output, and the edit says what was sped up |
| **Seeded app footage** | 12 (interaction tour) | Capture each app in a browser **after** seeding: TrustDesk `AUTO_SEED=true` (set in the manifest), PayBroker Settings, Demo traffic, Send 120; a Simulator city and company created and started; PagerLens Lab incident; ShopFront order paid with test card `4242 4242 4242 4242`. Record the clicks, not the waiting | Recorded once from a fleet that is already warm; the narration describes what the screen shows |

### Recorder for the offline segments

`docs/video/record.sh [--mp4] [--fast]` (code in `docs/video/record.py`) copies `ecosystem.yaml` into a temporary directory, links the repo's `ecoctl` there as
`.venv/bin/ecoctl`, runs the commands of scenes 2, 4, 5 and 12 for real with that directory as the working directory (so the empty temporary `.ecosystem/` is
used and the real fleet's state file is never opened), answers the two prompts with `n`, and writes to `docs/video/out/` (git-ignored): `offline.cast` (an
asciinema v2 recording; `asciinema play` or `agg` can replay or render it, neither is installed here), `transcripts/*.txt` (the exact output of each command,
what the section below quotes) and, with `--mp4`, `offline.mp4`, a rendered terminal video made with Pillow and ffmpeg (no network, no browser). The mp4 is a
fallback or reference; for the final video, record the screen live with the commands typed, and use the transcripts to check it. Nothing in it calls AgentPG.
It was run on 2026-10-27 as a rehearsal (about 31 s of footage, 11 s to render); those outputs were deleted afterwards.

Fallbacks and mocks:

- If the account cannot host eight sandboxes, the live segment uses only `--only trustdesk` (3 instances, 3 sandboxes), which the narration already says.
- If you want an `ecoctl status` frame without a live fleet, **do not fake output**. Record it from the live run; or show the status of a fleet where everything is paused and say so. A made-up status table would misrepresent the tool.
- Seeded data: the apps' demo content is synthetic by design; the narration says so once. Add a one-line on-screen banner "Synthetic data, test cards only".
- Auto-pause is off in the manifest. If you turn it on to save credit, resume everything (`agentpg sandbox resume <id>`) a few minutes before recording and expect locked vaults until a sign-in.
- Re-record offline scenes after any manifest change; the app count and the order in scene 3 come from `ecoctl validate` and must match the video.
- Edit out or blur URLs if you do not want viewers to try them; they contain sandbox ids and change on every recreate.

## Scene table

Times are cumulative targets. ON-SCREEN ACTION is what the viewer sees; NARRATION is spoken; CALLOUTS are on-screen text or overlays added in the edit.

| # | Time | ON-SCREEN ACTION | NARRATION (about 140 wpm) | CALLOUTS |
|---|---|---|---|---|
| 1 | 0:00 to 0:35 | Title card, then the architecture diagram from GUIDE section 3 drawn in stages: identity in the middle, the gateway, then the six apps. Nothing is typed yet. | "This is the AgentPG ecosystem: eight small apps, each with its own Postgres database, built to show what you can do with Postgres, pgvector, MCP and AI. There is a compliance workspace, an incident investigator, a payment broker, a web shop, a smart city and its simulator, one sign-in service and one LLM gateway. Putting that on a platform by hand is a long checklist. One command line tool, ecoctl, does it from a single file. In the next seven minutes: deploy it, then watch the apps talk to each other." (91 words) | "Welcome to the first video in this series, about the AgentPG ecosystem. It is a set of small sample apps for EDB AIDB and AgentPG. Each one runs in its own sandbox, with its own Postgres database. One tool, ecoctl, deploys the whole fleet from a single file. TrustDesk checks requests against company policy. PagerLens investigates incidents. CityPulse watches a smart city. A simulator feeds it. PayBroker routes payments. ShopFront is a web shop that pays through it. Two shared services sit underneath. Identity handles sign-in, and issues short-lived service tokens. The LLM gateway is the one door to models, with its own key and limits for each app. Now the connections. The simulator sends readings to CityPulse, and CityPulse reports trouble to PagerLens. ShopFront sends payments to PayBroker, which sends a signed webhook back. TrustDesk asks the gateway to check requests against policy. That makes eight sandboxes. In this video we plan a deployment, run it, and watch the apps talk. The data is synthetic, and the cards are test cards." (172 words) |
| 2 | 0:35 to 1:05 | Terminal in the repo. `.venv/bin/ecoctl validate`. Output, one line: `ok: fleet 'eco', identity + 8 apps (llmgw, pagerlens, citypulse, paybroker, trustdesk, cpsim, pagerlens-agent, shopfront)`. | "You need three things: the agentpg command line tool, logged in; uv with Python 3.11; and container images that AgentPG can pull. Our CI builds the images and we make them public once. Install is two commands, and validate checks the manifest. Notice what it prints: the order the apps will be created in, worked out for you. That order is not written down anywhere." (65 words) | "First, what you need. The AgentPG command line tool, logged in. Then uv, with Python 3.11. And public container images. Installing ecoctl is two commands. Then run validate. It lists eight apps after identity, with the gateway first, because the others depend on it. The PagerLens agent is only registered. It runs outside AgentPG. That order is worked out for you. It is not written down anywhere." (67 words) |
| 3 | 1:05 to 2:00 | Open `ecosystem.yaml` in an editor, scrolling slowly. Pause on `llm: true` on trustdesk; on `calls:` of citypulse; on `uses:` of cpsim; on `connect:` of shopfront; on `exposes:`. | "Here is the whole fleet in one file. Each app has a name, an image, its database extensions and its roles. Three small keys describe how apps relate. Exposes lists the scopes an app offers. Calls lists the scopes it may request from another app; CityPulse may send data to PagerLens, and nothing else. A typo here fails validation, not production. Uses gives an app another app's address without a scope; the simulator uses PayBroker with a merchant key. And llm true wires an app to the gateway. Secrets never appear. A name that looks like a secret is refused, and listed under generate, so ecoctl creates the value. The roles a person can hold in each app are defined here too, and the groups at the bottom map people to roles across the whole fleet, so access is a line in this file, not a round of clicking." (149 words) | "Now the manifest: the whole fleet, in one file. Each app has a name, its database extensions, and its roles. Exposes lists the scopes an app offers. Calls lists the scopes it may ask of other apps. CityPulse may call PagerLens, and nothing else. A typo here fails validation, not production. Uses gives an app another app's address, with no scope. The simulator uses PayBroker, with a merchant key. And llm true wires an app to the gateway. Secrets never appear here. A name that looks like a secret is refused. List it under generate, and ecoctl creates the value. Groups at the bottom map people to roles across the fleet. So access is one line, not a round of clicking." (121 words) |
| 4 | 2:00 to 2:50 | `.venv/bin/ecoctl plan --only trustdesk`. Output: 11 numbered steps (full text in "Captured output" below): identity, gateway, TrustDesk, then the two access groups. Step 7 is the gateway client; step 8 shows `extensions create vector` and `pg_trgm` and `sandbox create ... --env '...=***'`. The plan is 32 lines and its sandbox lines are long: use a terminal at least 160 columns wide or let it wrap, and scroll. | "Before anything runs, ask for a plan. Asking for TrustDesk alone pulls in what it needs: the identity provider and the gateway. The plan shows every real command and every identity call, with secrets masked. It creates a database and a sandbox for each app, registers it for single sign-on with its roles, creates its gateway client, and then points its sign-in at its real address. Plan changes nothing and calls nothing, so you can run it as often as you like." (83 words) | "Before anything runs, ask for a plan. Asking for TrustDesk alone pulls in what it needs: the identity provider, and the gateway. The plan lists every real command and every identity call. Secrets are masked. Each app is registered for sign-on, gets its own gateway key, then a database and a sandbox. Last, its sign-in points at its real address, and two access groups are set. Plan changes nothing, and calls nothing. Run it as often as you like." (79 words) |
| 5 | 2:50 to 3:20 | `.venv/bin/ecoctl apply --only trustdesk` (no `--execute`). Same plan, ending with "Dry run: nothing was changed. Add --execute to create these on AgentPG and register them with the identity provider." Then `.venv/bin/ecoctl apply --only trustdesk --execute`: the plan again, then the credits sentence and `Continue? [y/N]`; answer `n`: it prints `Cancelled.` and exits 1. (Plain `apply` prints the whole fleet's plan, about 8 sandboxes' worth; use `--only` on screen.) | "Apply without execute is the same dry run. Add execute and it shows the plan, then asks before it spends anything on your account. It is safe to run again: it finds what exists by name, and never rotates a secret unless you ask. If a step fails, progress is saved, and you simply run the same command. Every created thing is named after the fleet prefix, so you can run several fleets in one account, and a deleted sandbox is simply noticed and created again." (87 words) | "Now, applying. Without execute, apply is the same dry run, and it ends by saying nothing was changed. Add execute, and it shows the plan, then asks before it spends anything on your account. We answer no, and nothing is created. Apply is safe to run again. It finds what exists by name, and never rotates a secret unless you ask. If a step fails, progress is saved. Run the same command again. Everything is named after the fleet prefix, so one account can hold several fleets. A deleted sandbox is simply noticed, and created again." (96 words) |
| 6 | 3:20 to 3:40 | Cut to the pre-recorded live segment title: "Recorded once on a demo account". | "Creating real databases takes minutes, so for this part you are watching a recording of one real run, sped up." (20 words) | "Creating real databases takes minutes. So for the next part, we follow one real run on a demo account, sped up." (21 words) |
| 7 | 3:40 to 4:30 | Live footage: `apply --execute --yes --only trustdesk`. Step lines appear: `[1/..] Identity provider: database and sandbox ... https://... (created)`, readiness and issuer, TrustDesk registration, `LLM gateway client and key`, TrustDesk sandbox. Finish with the printed list of URLs (blurred) and the reminder to add yourself to a group. | "The identity provider comes first, because its address is the issuer every app trusts. Then the gateway, because TrustDesk uses a model through it. For TrustDesk, ecoctl registers the app, creates its own gateway key with a rate limit and a monthly token budget, and creates the sandbox with that key in its environment. No one pastes a secret. The secrets live in one private file next to the manifest, and that file is the only copy, so you back it up." (82 words) | "We run apply with execute, for TrustDesk only. Identity comes first, because its address is the issuer every app trusts. Then the gateway, because TrustDesk uses a model through it. For TrustDesk, ecoctl registers the app and creates its own gateway key, with a rate limit and a monthly token budget. Then it creates the sandbox, with that key in its environment. No one pastes a secret. On a real run, you see one line per step, each ending with what happened. At the end come the addresses, and a reminder to join a group. Secrets live in one private file next to the manifest. It is the only copy, so back it up." (114 words) |
| 8 | 4:30 to 5:10 | Live footage: `.venv/bin/ecoctl status`. Table of apps with health 200 and ready 200; the LLM gateway section with a client row and `chat ... ok`; identity registry rows `ok`. | "Status is the check you will use most. Every app answers health and readiness. Each app's gateway client exists, its limits match the manifest, and one tiny chat call, made with that app's own key, goes through. And the identity registry matches what is deployed. Exit code zero means green, so it also works in a script. If a model limit was changed by hand in the gateway, status warns about drift, and one sync command either shows the difference or brings the gateway back in line with the manifest." (90 words) | "Status is the check you will use most. First, every app answers health and readiness. The real output is a table, with one row per app. Second, the gateway. Each client exists, its limits match the manifest, and a tiny chat call with its own key goes through. Third, the identity registry matches what is deployed. Exit code zero means green, so it works in a script. If a limit was changed by hand, status warns about drift. One sync command shows it, or restores the manifest." (87 words) |
| 9 | 5:10 to 5:50 | Live footage: first sign-in. Browser: identity admin, create a user, add to `ecosystem-admins`; launcher `/apps` with tiles; click TrustDesk, land signed in. | "The one manual step is you. Sign in to the identity console, create yourself, and join the ecosystem admins group, which ecoctl created with the admin role in every app. Open the launcher: one tile per app, one sign-in. Roles are per app, so a person in the risk team sees PayBroker as a risk manager and PagerLens read-only. Google or GitHub login is optional, and switched on in the same console. Nothing in the apps changes." (77 words) | "Now the apps themselves, starting with the first sign-in. This step is you. In the identity console, create yourself as a user. Then add yourself to the group ecosystem admins. ecoctl created that group, with the admin role in every app. Open the launcher. It has one tile per app, and one sign-in. Roles are per app. Someone in the risk team is a risk manager in PayBroker, and read-only in PagerLens. Google or GitHub login is optional." (78 words) |
| 10 | 5:50 to 6:30 | Interaction tour, seeded footage, quick cuts with the diagram highlighting the path: Simulator city starts; CityPulse heatmap fills; BURST pressed; PagerLens opens an incident and investigates with cited evidence. | "Now watch the apps work together. The simulator starts a city and sends readings to CityPulse, using a short-lived token from the identity provider, not a copied secret. Press burst: CityPulse degrades and reports to PagerLens, which opens an incident, gathers evidence and ranks the likely causes, each with the data that supports or refutes it. PagerLens asks the model through the gateway. The simulator also runs payment and policy-request workloads, so the same fleet shows the fraud engine and TrustDesk under load. Every call between apps is limited to the scope the manifest granted, nothing more." (97 words) | "Now watch the apps work together. The simulator starts a city, and sends readings to CityPulse with a short-lived identity token. Not a copied secret. Press burst, and CityPulse degrades and reports to PagerLens. PagerLens opens an incident, gathers evidence, and ranks likely causes. Each one has the data that supports or refutes it. To reason, it asks a model through the gateway. The simulator also sends payments and policy requests. So the fraud engine, and TrustDesk, run under load. Every call is limited to the scope the manifest granted." (90 words) |
| 11 | 6:30 to 7:05 | Seeded footage: TrustDesk request appears with AI advice and a quoted policy passage; the approver approves. Then ShopFront checkout with a test card, order paid, and PayBroker's Payments list showing the decision and the fraud score. | "A request arrives in TrustDesk and is checked against company policy, with quoted passages the app verified word for word; a person still decides. In ShopFront a customer pays with a test card. The card is tokenized in the browser, PayBroker scores the payment and signs a webhook back to the shop. Ecoctl wired that pair by itself: merchant, allowed origin, keys, webhook. Run connect any time, and it repairs it. The model behind all of this is one alias in the gateway. Repoint it once, and every app follows, with no change in any of them." (97 words) | "A request arrives in TrustDesk. A model checks it against policy, and the app verifies every quote word for word. A person still decides. Now ShopFront. A customer pays with a test card. It is tokenized in the browser, so it never touches the shop's server. PayBroker scores the payment, and signs a webhook back to the shop. ecoctl wired that pair itself: the merchant, the allowed origin, the keys, and the webhook. Run connect any time to repair it. The model behind all of this is one alias in the gateway. Repoint it once, and every app follows." (99 words) |
| 12 | 7:05 to 7:30 | Terminal: `.venv/bin/ecoctl recreate paybroker` output listing the cascade, cancelled at the prompt (`n`: `Cancelled.`, exit 1; see "Captured output" for the exact lines, and the note on the fixture state); then the diagram one last time; end card with the repository and GUIDE.md link. | "When you ship a new image, recreate the app. A sandbox's address contains its id, so apps that depend on it are recreated too, and the connection is repaired. Everything here is in one manifest, one state file and one guide. Add an app by adding a few lines. Thanks for watching. Rotating every secret is one flag, followed by a recreate, and the state file is your backup target. The link to the full guide is below." (78 words) | "To wrap up: when you ship a new image, you recreate the app. A sandbox's address contains its id. So apps that depend on it are recreated too, and the connection is repaired. ecoctl lists that cascade, and asks first. We answer no, and nothing is deleted. That is the whole loop. One manifest, one state file, one guide. Add an app with a few lines. Rotating every secret is one flag, then a recreate. The state file is your backup target. Thanks for watching. The full guide is linked below, and the other videos in this series go deeper into each part." (103 words) |

Review cut (2026-10-27, series opener): 7:48, within the 6:00 to 8:00 range. Each topic has one voice, never the same voice in two consecutive topics, male and female alternating; every topic's first sentence orients the listener, so nothing depends on the previous speaker. No static picture lasts more than about 10 s.

### Review cut: real versus placeholder scenes

| Scenes | In the review cut | Needs a real capture later |
|---|---|---|
| 1, 3, 12 (diagram, end card) | rendered slides; scene 3 shows real lines of `ecosystem.yaml` | no |
| 2, 4, 5, 12 (terminal part) | replay of the real captured output of `validate`, `plan --only`, dry-run `apply`, cancelled prompts and `recreate` (fixture state), typed and scrolled in step with the voice | no (re-run `docs/video/build.py` after a manifest change) |
| 6 | slide: the live segment title | yes, with the timer overlay |
| 7 | step diagram of `apply --execute --yes --only trustdesk`; no terminal output is shown | yes: the live run |
| 8 | three-check diagram of `ecoctl status`; no table is shown | yes: `ecoctl status` on a live fleet |
| 9 | step diagram plus the manifest's groups | yes: browser capture of the first sign-in and launcher |
| 10 | interaction diagram with the scopes | yes: seeded Simulator, CityPulse and PagerLens footage |
| 11 | interaction diagram | yes: TrustDesk request, ShopFront checkout, PayBroker payment |

Slides for 6 to 11 carry an amber "REVIEW CUT" tag; the narration is written to hold when the real footage replaces them (it says what the real output looks like instead of reading it).
between 7:15 and 7:45. If the first cut runs over 8:00, drop the last sentence of scenes 3, 5, 10 and 11 (each is an added sentence, marked by sitting last in its cell) before speeding up delivery. If it runs under 6:30, the live
footage in scenes 7 and 8 can be left at its natural pace.

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

## Production checklist

1. Record scenes 1-6 and 12 offline from a clean clone; keep a printed copy of the section above (or run `docs/video/record.sh` and read `docs/video/out/transcripts/`) to check the narration against.
2. Record the live segment once on the demo account (scenes 7-9) and the recreate take for scene 12 only if you are willing to delete the sandbox; otherwise record scene 12's list at the **prompt** and answer `n` (the command asks before deleting, and cancelling exits with code 1).
3. Seed and record the tour (scenes 10-11) from a warm fleet. Re-check that no key, `lg_` value, merchant key or admin key is visible in any frame, including browser address bars and autofill.
4. Time the read-through; adjust with the extra sentences. Keep every on-screen claim equal to something in GUIDE.md.
5. After recording, delete the demo fleet (GUIDE section 13) and the demo `.ecosystem` directory, and rotate anything that appeared on screen by mistake.

## Not verified

- Timings of the live segment are estimates from the code's wait limits (7 minutes per sandbox), not from a measured run. Cut to the real timings after recording.
- The word counts are hand counts; read the script aloud with a timer once.
- Not run on purpose: `apply --execute` past the prompt, `recreate` past the prompt, `status`, `connect`, `llm ...`, `env`; the live-segment output in scenes 7 to 9 is still the script's description, not captured output.
- Appearance of the apps' UIs is taken from their READMEs and the e2e screenshots, not from a fresh look.
