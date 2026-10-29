# Video script: from zero to a running ecosystem with ecoctl

Working title: "One file, eight apps: deploying the AgentPG ecosystem". Audience: customers evaluating Postgres + pgvector + MCP + AI on AgentPG, and EDB colleagues who will run the fleet themselves. Narration is written for listening (short sentences, one idea each) and spoken by Kokoro (local, US accent, shared speed default 1.08 from `videos/tts/narrate.py`). Three voices only, alternating male and female, never the same voice twice in a row: am_liam (opener), af_heart, am_eric; each scene names its voice in the `## <scene> <voice>` header of [video/narration.txt](video/narration.txt). The text lives there; the NARRATION column below mirrors it (`python3 docs/video/build.py --sync-script`). Reference for every claim: [GUIDE.md](GUIDE.md) and [09-goals-and-acceptance.md](09-goals-and-acceptance.md). The terminal output quoted below was captured from real runs on 2026-10-27.

Review cut (2026-10-29 redo): about 6:48. Opens with the problem, labels PLATFORM / REFERENCE SCAFFOLDING / SAMPLE APPS in the first seconds and on the diagram, adds the Ansible scene (6), and replaces the live slides with captured stills where they exist (9 to 11).

## Prerequisites (for the person recording)

- A fresh clone of this repository in a clean directory with **no** `.ecosystem/` (so `plan` shows a first deployment), `uv venv && uv pip install -e ".[dev]"` done, terminal font 18 pt or larger, 1920x1080, light or dark theme but the same in every scene.
- For a future live recording of scenes 7 to 8 only: `agentpg login` done on a **dedicated demo account**, images public (GUIDE section 5.2), and a separate manifest copy with `fleet: demo` so names never show your working fleet.
- A browser profile signed in to nothing else, with notifications off. The identity admin key, database keys, `lg_` keys and merchant keys must never be on screen: do not run `ecoctl env ... --reveal`, `ecoctl db`, or `cat .ecosystem/state.json` while recording.
- A model path for the demo fleet: the gateway's built-in `echo` provider (canned answers, fine for showing wiring) or a real provider you have checked first. On 2026-10-25 the real provider answered HTTP 402 (see doc 01); check `ecoctl llm test trustdesk` before recording the TrustDesk moment.

## Recording plan: what is live, what is mocked

A live `apply --execute` takes many minutes (each sandbox waits up to 7 minutes for a URL), creates real databases, prints new URLs every time, and is not repeatable. So the video is built from two kinds of footage.

| Kind | Scenes | How | Why it is repeatable |
|---|---|---|---|
| **Offline, re-recordable at will** | 2-5, 12 (terminal parts) | `validate`, `plan --only`, `apply --only` (dry run) and its `--execute` answered `n`, run against the manifest and an empty state directory; `recreate paybroker` answered `n` needs a state that lists deployed apps, so it runs against a fixture state with made-up sandbox ids (no secrets). `docs/video/record.sh` does exactly this and records it (below). `env` is not used in any scene. These commands read only the manifest and the state file; `plan` and the dry run make no call to AgentPG or any service (checked in `cli.py` and `plan.py`). Secrets print as `***` | Same output every take, no account, no cost; rehearsed 2026-10-27 |
| **Captured stills (live fleet)** | 9-11 | Real screenshots of the apps and of `ecoctl insights`, captured on the live AgentPG fleet on 28 Oct 2026 and kept in `videos/live-captures/`; shown with an on-screen "CAPTURED" tag | Already captured; nothing to record again |
| **Slides, not captured** | 6-8 | Ansible recipe, the steps of `apply --execute`, `ecoctl status`, first sign-in and the interaction tour are slides tagged "SLIDE"; a fresh live run was not recorded for this cut | No output is invented

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

| # | Time | ON-SCREEN ACTION | CALLOUTS | NARRATION (mirror of narration.txt) |
|---|---|---|---|---|
| 1 | 0:00 to 0:50 | Problem slide (several AI apps on Postgres, weeks of plumbing), then the diagram built in stages with the three labels: PLATFORM (EDB Postgres on AgentPG), REFERENCE SCAFFOLDING (identity OIDC, llm-gateway, kit vault, ecoctl, Ansible recipe; swappable), SAMPLE APPS (TrustDesk, PagerLens, CityPulse + Simulator, PayBroker, ShopFront). | Category pills on every box; legend top right | "You want to show several AI apps on Postgres. Each one needs sign-in, a model gateway, secrets, and a database. And you do not want weeks of plumbing first. Here are three labels: platform, reference scaffolding, and sample apps. The platform is EDB Postgres on AgentPG. That is what we are showing. Reference scaffolding is identity, the LLM gateway, the kit vault, ecoctl, and an Ansible recipe. All of it is swappable. Use any OIDC provider that sends the roles the apps expect. Any OpenAI-compatible gateway. Your own vault, through the kit. Your own automation. The sample apps are TrustDesk, PagerLens, CityPulse with its simulator, PayBroker, and ShopFront. They exist to show the platform. The data is synthetic, and the cards are test cards. We will plan a deployment, see two ways to run it, and watch the apps work." (140 words) |
| 2 | 0:50 to 1:19 | Terminal: `.venv/bin/ecoctl validate` (real output), prerequisites overlay, caption panels for the creation order. | Caption panels | "First, what you need. The AgentPG command line tool, logged in. Then uv, with Python 3.11. And public container images. Install ecoctl in two commands. Then run validate. It lists eight apps after identity, with the gateway first, because the others depend on it. The PagerLens agent is only registered. It runs outside AgentPG. That order is worked out for you. And ecoctl itself is scaffolding: you can replace it." (70 words) |
| 3 | 1:19 to 2:00 | Real lines of `ecosystem.yaml` with the key lines highlighted. | Caption per key line | "Now the manifest: the whole fleet, in one file. Each app has a name, its database extensions, and its roles. Exposes lists the scopes an app offers. Calls lists the scopes it may ask of other apps. CityPulse may call PagerLens, and nothing else. A typo here fails validation, not production. Uses gives an app another app's address, with no scope. The simulator uses PayBroker, with a merchant key. And llm true wires an app to the gateway. Secrets never appear here. A name that looks like a secret is refused. List it under generate, and ecoctl creates the value. Groups at the bottom map people to roles across the fleet. So access is one line, not a round of clicking." (121 words) |
| 4 | 2:00 to 2:28 | Terminal: `.venv/bin/ecoctl plan --only trustdesk` (real output, replayed). | Badge: plan = read-only | "Before anything runs, ask for a plan. Asking for TrustDesk alone pulls in what it needs: the identity provider, and the gateway. The plan lists every real command and every identity call. Secrets are masked. Each app is registered for sign-on, gets its own gateway key, then a database and a sandbox. Last, its sign-in points at its real address, and two access groups are set. Plan changes nothing, and calls nothing. Run it as often as you like." (79 words) |
| 5 | 2:28 to 2:59 | Terminal: `apply --only trustdesk` (dry run) and `apply --only trustdesk --execute` answered `n` (real output), then a slide on safe re-runs. | Badge per step | "Now, applying. Without execute, apply is the same dry run, and it ends by saying nothing was changed. Add execute, and it shows the plan, then asks before it spends anything on your account. We answer no, and nothing is created. Apply is safe to run again. It finds what exists by name, and never rotates a secret unless you ask. If a step fails, progress is saved. Run the same command again. Everything is named after the fleet prefix, so one account can hold several fleets." (87 words) |
| 6 | 2:59 to 3:54 | Ansible as the customer-owned alternative (own scene, can be re-cut alone): slides for the same-manifest comparison, the commands (`site.yml --check`, `site.yml`, `verify.yml`, `destroy.yml -e eco_destroy_confirm=<fleet>`; text from docs/07, not run), a terminal with the real `ansible-playbook <playbook> --syntax-check` output of the three playbooks (offline, in a throw-away copy), the destroy guards, and the verified items. The last three paragraphs give the verified result of the complete eight-app apply on the throwaway fleet `ecoans` (29 Oct 2026: one run, 14 minutes, `ok=367 changed=96 failed=0`; second run `ok=254 changed=0 failed=0`; `verify.yml` passing; guarded destroy back to the starting 9 instances; docs/07 banner, docs/09 G9) and say that the hosts target on real hosts and recreate on a real fleet are not verified. | Tag: SLIDE, not captured output | "Some teams deploy with Ansible, not with ecoctl. So there is a recipe for that. It reads the same manifest, and it is yours to own. Three playbooks. Site creates the fleet. Verify checks it. Destroy removes it. Offline, all three pass a syntax check. Destroy is guarded. It refuses unless you pass the fleet name as the confirmation. It deletes exact names only, never a prefix match. On the platform, we ran it for real, on a throwaway fleet. A complete apply of all eight apps, from empty, in one run: fourteen minutes, no failures. A second run changed nothing. Verify passed: all eight sandboxes healthy, the gateway clients answering, masking drift zero. The dry run and the teardown are verified too. The guarded destroy returned the account to where it started, with the real fleet untouched. Not verified: the hosts target on real hosts, and recreate on a real fleet." (152 words) |
| 7 | 3:54 to 4:32 | Slides, labelled as slides: what `apply --execute` does in order, `ecoctl status`, first sign-in. No terminal output is invented. | Tag: SLIDE, not captured output | "Now what apply with execute does, in order. This part is described, not replayed. Identity comes first, because its address is the issuer every app trusts. Then the gateway. Then each app, with its own gateway key and limits. Secrets live in one private file next to the manifest. It is the only copy, so back it up. Status is the check you will use most. Every app answers health and readiness, each gateway client works, and the identity registry matches. Exit code zero means green. The one manual step is you. Create yourself in the identity console, join the admin group, and open the launcher: one tile per app, one sign-in." (112 words) |
| 8 | 4:32 to 4:53 | Interaction diagram (slide): simulator to CityPulse to PagerLens, ShopFront and PayBroker, scopes. | Tag: SLIDE | "Now watch the apps work together. The simulator sends readings to CityPulse with a short-lived identity token. Not a copied secret. A burst makes CityPulse degrade and report to PagerLens, which opens an incident. Payments go through PayBroker, which signs a webhook back to the shop. Every call is limited to the scope the manifest granted." (56 words) |
| 9 | 4:53 to 5:12 | CAPTURED stills of CityPulse (map of Gotham, Explain), `videos/live-captures`. | Tag: CAPTURED from the live AgentPG fleet, 28 Oct 2026 | "These are real screens, captured from the live fleet. This is CityPulse, on PostGIS. Districts are drawn from real sensor coordinates, colored by health. The Explain page answers a district question with one SQL statement. Relational, time series, JSON, geo, and vector data, in one round trip." (47 words) |
| 10 | 5:12 to 5:32 | CAPTURED stills of TrustDesk REQ-000016: the rejection, the verified quotes, the decision panel. | Tag: CAPTURED | "TrustDesk next. A request for fifteen days of leave, starting tomorrow. A model checks it against policy, and suggests rejection. Every reason quotes the policy. The app confirms each quote appears word for word in the document. A person still decides. Approving against the advice needs a written reason." (49 words) |
| 11 | 5:32 to 6:15 | CAPTURED stills of the Database pages (TrustDesk, PayBroker) and the text of `ecoctl insights`; a slide on the two MCP routes (docs/08). | Tag: CAPTURED; the MCP slide is a SLIDE | "Last, the platform itself. Every app has a Database page, fed by the EDB extensions. Top queries come from edb_stat_monitor. Settings suggestions come from edb_pg_tuner. Index suggestions come from query_advisor. These are suggestions only. A person decides what to change. And AI access to the database goes through a read-only user, with masked columns. PayBroker has the same page. Its monitor shows the masking the kit applied: revoked tables, and masked views. From the command line, ecoctl insights lists the advisor's suggestions for every app. Two MCP routes exist: each app's own MCP server, and AgentPG's database MCP. The guide compares them." (102 words) |
| 12 | 6:15 to 6:47 | Terminal: `ecoctl recreate paybroker` cancelled at the prompt (fixture state, made-up sandbox ids), then the diagram, then the end card. | Badge: sample state | "To wrap up: when you ship a new image, you recreate the app. A sandbox's address contains its id. So apps that depend on it are recreated too, and the connection is repaired. ecoctl lists that cascade, and asks first. We answer no, and nothing is deleted. That is the loop. One manifest, one state file, one guide. Add an app with a few lines. Rotating every secret is one flag, then a recreate. The state file is your backup target. Thanks for watching. The full guide is linked below." (90 words) |

Review cut (2026-10-28 redo): about 6:48, close to the 7:00 target. Each scene has one voice, alternating male and female, never the same voice twice in a row. No picture stays identical for more than about 8 s (freezedetect, -50 dB and -60 dB); long slides flip with a faint amber tint.

### Review cut: real versus placeholder scenes

| Scenes | In the review cut | Status |
|---|---|---|
| 1, 3, 8, 12 (diagram, manifest lines, end card) | rendered slides; scene 3 shows real lines of `ecosystem.yaml`; scene 8 is a diagram | slides, labelled; the diagram is the labelled platform / scaffolding / sample map |
| 2, 4, 5, 12 (terminal part) | replay of the real captured output of `validate`, `plan --only`, dry-run `apply`, cancelled prompts and `recreate` (fixture state) | real, offline; re-run `docs/video/build.py` after a manifest change |
| 6 | slides for the Ansible recipe, plus the real output of `ansible-playbook <playbook> --syntax-check` (offline, on a copy of `ansible/`) | the recap counters are tagged CAPTURED from the real run of 29 Oct 2026; the rest of the verified list is from docs/07 and docs/09; hosts target and recreate on a real fleet are stated as not verified |
| 7 | slides (steps of `apply --execute`, `ecoctl status`, first sign-in), tagged "SLIDE" | slides: no fresh live run was recorded; nothing invented |
| 9 | captured stills: CityPulse map and Explain | CAPTURED from the live AgentPG fleet, 28 Oct 2026 |
| 10 | captured stills: TrustDesk REQ-000016 | CAPTURED, same date |
| 11 | captured stills: TrustDesk and PayBroker Database pages; text of `ecoctl insights` (excerpt); slide on the two MCP routes (docs/08) | CAPTURED, same date; the MCP slide is a slide |

Not claimed anywhere: HA or failover, AIDB, autonomous database changes. Slides carry an amber "SLIDE" tag, captured stills an amber "CAPTURED" tag.

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

1. Record scenes 1-5, the syntax-check part of 6, and 12 offline from a clean clone; keep a printed copy of the section above (or run `docs/video/record.sh` and read `docs/video/out/transcripts/`) to check the narration against.
2. Optionally record a live `apply --execute` and `status` on the demo account (scene 7) and the recreate take for scene 12 only if you are willing to delete the sandbox; otherwise record scene 12's list at the **prompt** and answer `n` (the command asks before deleting, and cancelling exits with code 1).
3. The captured stills of scenes 9-11 are in `videos/live-captures/`; replace them from a warm fleet if the apps change. Re-check that no key, `lg_` value, merchant key or admin key is visible in any frame, including browser address bars and autofill.
4. Time the read-through; adjust with the extra sentences. Keep every on-screen claim equal to something in GUIDE.md.
5. After recording, delete the demo fleet (GUIDE section 13) and the demo `.ecosystem` directory, and rotate anything that appeared on screen by mistake.

## Not verified

- Timings of the live segment are estimates from the code's wait limits (7 minutes per sandbox), not from a measured run. Cut to the real timings after recording.
- The word counts are hand counts; read the script aloud with a timer once.
- Not run on purpose: `apply --execute` past the prompt, `recreate` past the prompt, `status`, `connect`, `llm ...`, `env`; the live-segment output in scenes 7 to 9 is still the script's description, not captured output.
- Appearance of the apps' UIs is taken from their READMEs and the e2e screenshots, not from a fresh look.
- Scene 6 (Ansible): the complete eight-app apply is verified (throwaway fleet `ecoans`, 29 Oct 2026). Not verified and not claimed: the `eco_target: hosts` path on real hosts, and `eco_env_change: recreate` on a real fleet.
- Scene 7 is slides: no fresh live run of `apply --execute`, `status` or the first sign-in was recorded for this cut.
