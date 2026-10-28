# Making the ecosystem a showcase

> **Dated report, 2026-10-25.** This is a snapshot of the showcase plan as of that date and is not updated. The current reference is [GUIDE.md](GUIDE.md) (see its section on differences); where the two disagree, GUIDE.md wins.

Goal: each project is something EDB can put in front of a customer or a conference audience as proof of **AIDB** and **AgentPG**. "State of the art" is not a feeling; this plan turns it into checkable work. Companion to [01](01-state-of-the-projects.md), [02](02-deployment-operations-integration.md) and [03](03-aidb-integration.md).

## 1. Where we are against that bar (honest assessment)

Strong already: a coherent architecture (one kit, one identity, one gateway, one deploy tool), tested security design, apps that really run on AgentPG, and several non-obvious engineering stories (pure-Python XGBoost scoring with a parity test, fenced and verified model output, quote-checked citations, a fleet that re-addresses itself on recreate).

Not at the bar yet, in order of how much it hurts a showcase:

1. **Nobody has seen the UIs in a real browser.** Every visual claim is unverified. A showcase fails on a misaligned table or a form that wipes input long before anyone reads the architecture. The typed-input-wiped bug class recurred in several apps before it was audited.
2. **The AI is not measured.** No accuracy numbers for PagerLens AI methods, TrustDesk pre-validation or PayBroker on real data. "AI" without a number is a demo, not a showcase.
3. **AIDB is absent.** The apps demonstrate Postgres + pgvector + our code. The mandate is to demonstrate AIDB (document 03).
4. **No story.** There is no guided path: what to click, what to say, what the audience should conclude, in five minutes.
5. **CI proves less than it should.** App repos run `ruff` and `pytest` before building the image (the image job needs the test job), which is good. But the workflows define no Postgres service, so tests that need a database cannot run there (checked: no `services:` block in the paybroker and trustdesk workflows); there is no type check, dependency audit, browser test, SBOM or signing.
6. **No screenshots, diagrams or recordings** in any README; no changelog; no versioned releases of the apps (images are `latest`).
7. **Data is synthetic** where it should be realistic (PayBroker model, city generator scale).
8. **Operational rough edges:** vault-unlock banner redraw, auto-pause 503s, URL changes on recreate, `state.json` as a single point of loss.

## 2. Definition of "showcase-ready" (per project)

A project is ready when all of these are true and recorded in its repo:
- **Runs in one command** locally and on AgentPG, from a clean account, in under 15 minutes, with the steps in the README tested by someone who did not write them.
- **CI** runs lint, type check, unit and database tests, dependency audit and image build on every PR; main is green.
- **Browser-verified:** end-to-end tests drive the main flows in a real browser (Playwright) and store screenshots as CI artifacts; zero console errors on the main paths.
- **A five-minute demo script** with expected on-screen results, and a seeded data set that makes it work first time.
- **A measured claim:** one number the audience can check (latency, accuracy, throughput) with the method and the command that reproduces it.
- **README** with a hero screenshot, an architecture diagram, "what it demonstrates about AIDB and AgentPG", limits stated.
- **Security review** clean: threat model, no secret in git or logs, SBOM and vulnerability scan, signed images.
- **Accessibility** pass (keyboard, contrast, labels) and phone layout checked.

## 3. Work packages

### WP1 Verification foundation (do first; everything else depends on it)
- Playwright suite per app against a local docker-compose stack and, nightly, against the live fleet. Screenshots uploaded as artifacts and committed under `docs/img/` for READMEs.
- A fleet **smoke test** command in ecoctl (`ecoctl verify`): signs in through identity, creates the minimum data in each app, calls each integration (simulator → CityPulse → PagerLens, TrustDesk request → advice, PayBroker payment → decision, gateway call), prints pass/fail.
- Regression tests for the typed-input class already exist as fake-DOM tests; add real-browser tests for every form with a timer on the page.
- Exit: all apps pass in a real browser; screenshots reviewed by a person.

### WP2 CI and release engineering
- GitHub Actions per repo: keep `ruff` and `pytest`; add `mypy` (or pyright), a Postgres + pgvector service so the database tests run, `pip-audit`, image build with tags `vX.Y.Z` and `sha-…` (not only `latest`), SBOM (Syft), image signing (cosign), Dependabot.
- Pin one kit version across apps (today `>=0.2.0` to `>=0.2.2`); release train: kit → identity → gateway → apps; CHANGELOG per repo; ecoctl manifest pins image tags and `ecoctl status` shows the running version.
- Migration safety: upgrade tests from the previous release's schema (already started for some apps) as a required check.
- Exit: a tagged release of every project, reproducible from CI, with a fleet upgrade command.

### WP3 AIDB integration (document 03)
Spike, then TrustDesk → PagerLens → PayBroker Tier 3 → CityPulse/Simulator, with the retrieval-backend switch and ecoctl `aidb:` provisioning. Exit: each AI app runs in `aidb` mode in the live fleet, with the governance dashboard showing real decisions.

### WP4 AI quality you can quote
- **PagerLens:** run `pagerlens eval --mode hybrid` against the real model; widen the scenario set beyond the nine written by the rules' authors (add held-out scenarios written separately, a few real-shaped incidents); publish top-1/top-3 for Rules, AI, Rules + AI, cold and warm, with token cost.
- **TrustDesk:** a labelled evaluation set (requests with known correct outcomes and the policy sections that justify them) to measure recommendation agreement and **citation precision** (quote found, quote supports claim); report overrides.
- **PayBroker:** build the missing **real-data fitting step**: choose a public dataset (candidates discussed: TabFormer, Sparkov, PaySim; you decide, and tell me where to download), fit the generator's profile to it, retrain, report PR-AUC and recall at 1% FPR on a real holdout, keep the reproducible command; make the training-data export loop demonstrable (capture → label → retrain → compare model versions).
- Gateway: per-alias latency and cost report in the dashboard for the demo.
- Exit: a one-page "numbers" table per app, regenerated by a command.

### WP5 The demo story (one per app plus one for the fleet)
- **Fleet story (8 minutes):** simulator starts a city and a payment workload → press burst → CityPulse degrades → PagerLens opens the incident and investigates with cited evidence → mark the cause → repeat and show memory → a policy request in TrustDesk with quoted advice and override justification → show the gateway's per-app usage and the governance decisions in AIDB.
- **One-click demo mode** per app: seeded data, a visible "Demo" menu with the steps, a reset button, and a banner stating that data is synthetic.
- **Landing page** (identity's launcher, restyled): one tile per app with live status, plus the "what this shows" sentence.
- Exit: someone else runs the story from the script, unaided, twice.

### WP6 Polish, accessibility and documentation
- Visual design pass on the shared kit CSS (type scale, spacing, empty states, loading and error states, dark mode parity); confirm phone layouts.
- Accessibility: keyboard paths, focus order, labels, contrast checks (axe in the Playwright run).
- Every README gets: hero screenshot, architecture diagram, "demonstrates AIDB / AgentPG" section, honest limits, link to its demo script and numbers.
- Docs site: one navigable set (these four documents plus per-app pages) published to a stable URL.
- Exit: README review by someone new to the project.

### WP7 Security and trust
- Rotate the CityPulse client secrets that were printed in a session; move `state.json` secrets to a proper store (an encrypted backup, or the gateway/identity vault) and document recovery; remove plaintext `keys/` files from the project root.
- Identity gaps that an enterprise audience asks about: MFA, consent screens, access-token revocation, SAML (decide which to build; at minimum state them as non-goals).
- Threat model per app; run a dependency and container scan; add rate limits where missing; document the data each app sends to an external model.
- Exit: a security summary page per app and a clean scan.

### WP8 Performance and scale (the AgentPG showcase)
- Use the simulator to produce **published benchmarks**: PayBroker authorizations per second and p95 on a given plan; CityPulse readings per second with a 10,000-sensor city; TrustDesk retrieval latency at 100, 10,000 and 100,000 chunks; PagerLens time to first hypothesis; AIDB pipeline lag under inserts.
- Show scale-to-zero and wake behavior honestly (time to first response after auto-pause), and turn auto-pause back on for the demo-ready fleet with a "resume everything" command in ecoctl.
- Exit: a benchmark page with method, plan sizes, dates and commands.

### WP9 Operations
- `ecoctl backup/restore` of `state.json` (encrypted) and of each database; `ecoctl upgrade` that recreates in dependency order with a post-check; `ecoctl verify` (WP1).
- Fix the vault-unlock banner redraw in the kit; show clear states for locked vaults and paused sandboxes in every app.
- PayBroker reset executed and re-seeded from the fitted generator; TrustDesk gateway-limit drift resolved; simulator literal URLs moved to fleet references.

## 4. Order of work

| Phase | Packages | Why this order |
|---|---|---|
| 0 (now, hours) | Your decisions in section 5; PayBroker reset; TrustDesk drift; secret rotation | Unblocks data and removes known risk |
| 1 (about 1 week) | WP1, WP2, spike of WP3 | You cannot polish what you cannot verify; AIDB availability gates everything else |
| 2 (about 2 weeks) | WP3 TrustDesk and PagerLens, WP4 evaluations, WP9 fixes | The main demonstrations and their numbers |
| 3 (about 2 weeks) | WP3 PayBroker/CityPulse/Simulator, WP4 real data, WP8 | Remaining depth and scale evidence |
| 4 (about 1 week) | WP5, WP6, WP7, release | Story, polish and trust, then tag v1.0 of each project |

Estimates assume one person plus background agents and your availability for browser checks and dataset choices.

## 5. Decisions needed from you
1. **AIDB on AgentPG:** can you run `agentpg login` and `agentpg extensions list <instance> -s aidb` (or tell me who owns the platform), and which AIDB features must the showcase hit first (agents, governance, pipelines, MCP endpoint)?
2. **Audience and venue:** customers, conference, internal? It decides how much polish goes to the UIs versus the numbers.
3. **PayBroker dataset:** which real dataset, and where may I download it (size, license)?
4. **Local-model idea:** still deferred, or part of the AIDB story (AIDB local models)?
5. **TrustDesk limits:** apply the manifest (5M hard) or keep your 1M soft.
6. **Identity scope:** build MFA/consent/SAML, or declare non-goals?
7. **Browser verification:** I cannot open the live apps from my browser; can you or a colleague run the browser checks (or give me access to Claude in Chrome), at least for a first pass?
8. **Auto-pause:** keep off until the launch, then on?

## 6. Risks to this plan
- AIDB may not be offered on AgentPG's current version: the plan changes shape (run AIDB on a Hybrid Manager or self-hosted Postgres for the AIDB half of the showcase).
- Model-dependent numbers drift when the underlying model changes; pin and record model, alias target and date in every published number.
- Scope: nine projects is a lot of polish. If time is short, ship a **core five** at full quality (identity, gateway, TrustDesk, PagerLens, PayBroker) and keep CityPulse and the Simulator as supporting actors.
