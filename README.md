# Sundial

![ci](https://github.com/rameswaran-mohan/sundial/actions/workflows/ci.yml/badge.svg)

Model lifecycle guard for LLM gateways.

Providers retire models every quarter, and each one publishes the dates on a different page in a
different shape. Sundial reads the official pages, keeps a registry of every model's lifecycle, and
runs inside your gateway to warn, block or redirect requests to models that are going away. It also
tells you when a new model launches.

It is a library, not a service. There is nothing extra to deploy.

```
$ sundial status --within 30
28 models retire within 30 days (485 tracked).
    3d  2026-09-21  azure      gpt-realtime-mini (2025-10-06)
   10d  2026-09-28  openai     gpt-3.5-turbo-1106  -> gpt-5.6-terra
   12d  2026-09-30  bedrock    amazon.nova-canvas-v1:0
   13d  2026-10-01  azure      gpt-4o (2024-05-13)  -> gpt-5.1
   14d  2026-10-02  gemini     gemini-2.5-flash-image  -> gemini-3.1-flash-image-preview
   26d  2026-10-14  bedrock    anthropic.claude-sonnet-4-20250514-v1:0
```

## Install

```bash
pip install sundial-llm            # core, CLI and ASGI middleware
pip install "sundial-llm[litellm]" # plus the litellm proxy plugin
```

## Use it with the litellm proxy

Add one line to your proxy config:

```yaml
litellm_settings:
  callbacks: sundial_llm.integrations.litellm_hook.sundial_hook
```

That is enough. With no Sundial config, the default policy never blocks anything. It adds standard
headers to responses from models that are retired or retire within 90 days:

```
HTTP/1.1 200 OK
deprecation: @1776816000
sunset: Fri, 23 Oct 2026 00:00:00 GMT
x-sundial-state: deprecated
x-sundial-successor: gpt-5.6-sol
x-sundial-rule: retiring-within-90-days
x-sundial-days-left: 35
```

`Deprecation` follows RFC 9745 and `Sunset` follows RFC 8594, so existing HTTP tooling understands
them. Sundial resolves each public model group to the deployments behind it through the proxy's
router, so Azure deployments with arbitrary names work as long as `model_info.base_model` is set.

## Use it with any other ASGI gateway

```python
from sundial_llm.integrations.asgi import SundialMiddleware

app = SundialMiddleware(app)  # FastAPI, Starlette or any ASGI app
```

The middleware inspects JSON bodies on `/chat/completions`, `/completions`, `/embeddings`,
`/responses` and `/messages`. Everything else passes through untouched.

## Where the data comes from

Your gateway does not have to read six provider pages itself. The `registry` workflow in this
repository scans them every night and commits the result to `registry/lifecycle.json` on `main`.
The file only changes when a lifecycle fact changed. A source that fails one night keeps its last
known records in the file, and the workflow run fails so the failure is visible.

Every install reads that file by default, through `registry_url`, at
`https://raw.githubusercontent.com/rameswaran-mohan/sundial/main/registry/lifecycle.json`. When the
local registry is older than `auto_scan_hours`, the plugin or middleware refreshes it in the
background by importing the file. It scans the provider pages directly only when the file is
unreachable, cannot be parsed, or is older than a direct scan this install already made and would
change records. Either way, the refresh is diffed and notified in the same way.

To never read the file and always scan the provider pages yourself:

```yaml
registry_url: null
```

`registry_url` can also point at a copy you host. The CLI does not use it: `sundial scan` always
reads the provider pages, and `sundial import` loads the file from a path or URL you give it. To
load the published file from the command line, without a checkout:

```bash
sundial import https://raw.githubusercontent.com/rameswaran-mohan/sundial/main/registry/lifecycle.json
```

## Policy

Point `SUNDIAL_CONFIG` at a YAML file. Rules are checked top to bottom and the first match wins.

```yaml
db: ~/.sundial/sundial.db
auto_scan_hours: 24          # 0 disables background refreshes
usage_tracking: true         # count requests for sundial status --in-use
usage_retention_days: 90     # usage rows older than this are deleted

rules:
  - name: block-retired
    when: {state: retired}
    action: block            # warn | block | redirect
    mode: apply              # propose (default) | apply
  - name: redirect-final-fortnight
    when: {days_until_retirement_lte: 14}
    action: redirect
    mode: apply
  - name: warn-90
    when: {days_until_retirement_lte: 90}
    action: warn

successors:                  # public model names, required for redirect
  gpt-4o-prod: gpt-5-prod

notify:
  slack_webhook: https://hooks.slack.com/services/...
  webhook: https://example.com/hooks/sundial

new_models:                  # which launches deserve a proposal
  - name: claude-sonnet-family
    platform: anthropic
    model_glob: "claude-sonnet-*"
```

`when` accepts `state`, `days_until_retirement_lte`, `platform`, `model_glob` and
`has_replacement`.

**Propose is the default.** A rule in `propose` mode never changes a request. It reports what it
would do through headers and logs. Only `mode: apply` blocks or redirects.

What an applied policy looks like through a real litellm proxy:

```
POST model=retired-chat
HTTP/1.1 410 Gone
{"error": {"message": "Model 'retired-chat' was retired on 2026-02-17 (Sundial rule
 'block-retired'). Use gpt-5.1-chat-latest instead."}}

POST model=leaving
HTTP/1.1 200 OK
x-sundial-redirected-from: leaving
x-sundial-successor: current
```

### Safety rules that are not configurable

- An approximate match is never enforced. Azure deployments with no version, Vertex models and
  rows the provider page contradicts itself about can warn, but they cannot block or redirect.
- A model group is blocked or redirected only when every deployment behind it agrees. One retired
  deployment in a healthy group produces a warning.
- Redirect needs an explicit entry in `successors`. Sundial never guesses a replacement for you.
- Sundial never raises into your gateway. If the registry is empty, the config is broken or a
  lookup fails, the request is allowed and the problem is logged.

## What is in use

The litellm plugin and the ASGI middleware count the requests they inspect, per day and public
model name, in the same SQLite file as the registry. A request is counted under the name the client
sent, before any block or redirect. The litellm plugin also records the key's team id. The
middleware records no team. `sundial status --in-use` turns those counts into the retirements your
traffic actually touches:

```
$ sundial status --in-use
3 models in use retire within 90 days (5 models in use, 485 tracked).
 -213d  2026-02-17  openai     chatgpt-4o-latest  calls=412  teams=support
   13d  2026-10-01  azure      prod-chat [gpt-4o (2024-05-13)]  calls=18230  teams=-
   35d  2026-10-23  openai     gpt-4-0613  calls=2741  teams=payments,search
Models in use with no lifecycle record:
  my-finetune  calls=96
```

Models already past their retirement date that still receive traffic are listed too, with negative
day counts, most overdue first. When the public name differs from the provider's model, the
provider's model follows in brackets. `teams=-` means no call carried a team. `--days` sets how far
back usage is counted (default 30) and `--within` sets the retirement horizon (default 90). Models
that match no lifecycle record are listed last, because Sundial knows nothing about their dates.
Each gateway process keeps at most 1,000 such names apart per day; requests for further ones are
counted under `(other)`.

Counts are buffered in memory and written by a background thread after 50 calls or on the first
call after 30 seconds, and at exit. Usage rows older than `usage_retention_days` (default 90) are
deleted once a day. Set `usage_tracking: false` to count nothing. Counts stay in the local database.
They are never exported or sent anywhere.

## Notifications and new models

Every scan and every import is diffed against the local registry. Changes become events:
`deprecation_announced`, `retirement_date_changed`, `replacement_named`, `model_retired`,
`model_added`, `state_changed`. Events go to Slack-compatible and generic webhooks. A new model that
matches a `new_models` rule also carries a ready-to-paste proposal:

```
[anthropic] claude-sonnet-6 is a new active model. Source: https://platform.claude.com/...

Proposal (rule 'claude-sonnet-family'): add claude-sonnet-6 to your gateway.
litellm model_list entry:
- model_name: claude-sonnet-6
  litellm_params:
    model: anthropic/claude-sonnet-6
```

The first scan or import is a silent baseline. A source that was down during the baseline joins
quietly later, so an outage never turns into a flood of events.

## CLI

```bash
sundial scan                                   # fetch every source, record and report changes
sundial scan --strict                          # the same, but exit 1 if any source failed
sundial import registry/lifecycle.json         # load a registry document from a path or URL
sundial export --out lifecycle.json            # write the local registry as a registry document
sundial status --within 60 --platform azure    # upcoming retirements
sundial status --in-use --days 30              # only the retirements your traffic touches
sundial check azure/prod-chat --base-model azure/gpt-4o-2024-05-13
sundial events --limit 20
sundial costmap-patch --cost-map model_prices_and_context_window.json \
    --out patch.json --report drift.md
```

Every command takes `--config` and `--db`. An import reports changes and sends notifications like a
scan. `export` rewrites the file only when a lifecycle fact changed, so a timestamp alone never
produces a new version.

`sundial check` exits with 0 when the model is fine and 3 when a rule flags it, so it works as a CI
gate over the models your code depends on. `sundial scan` exits with 1 only when every source fails,
or with `--strict` when any source fails. `sundial import` exits with 1 when it cannot read the
source and 2 when the file is not a registry document. `sundial export` and `sundial costmap-patch`
exit with 1 and write nothing when the local registry is empty, so a published file is never
replaced by an empty one. A config that cannot be found, parsed or validated exits with 2.

`costmap-patch` compares litellm's cost map with provider-published retirement dates. It proposes
only exact matches with firm dates. `--out` writes a small patch mapping. `--report` writes a
markdown drift report that names where the cost map came from (`--cost-map-url`, litellm's `main`
branch by default). The `costmap-drift` workflow runs it every night against the published registry
and commits the results to `registry/litellm-costmap-drift.md` and
`registry/litellm-costmap-patch.json` in this repository. Sundial never opens pull requests on
litellm.

## Sources

Everything is fetched as markdown over plain HTTP. There is no HTML scraping and no browser.

| Platform | Source | Notes |
|---|---|---|
| openai | `developers.openai.com/api/docs/deprecations.md` | dated announcement sections |
| openai | `developers.openai.com/api/docs/changelog.md` | new models, from `Feature` entries |
| anthropic | `platform.claude.com/docs/en/about-claude/model-deprecations.md` | "not sooner than" dates are kept as floors and never trigger rules |
| azure | `learn.microsoft.com/.../model-retirement-schedule` | every vendor on Foundry. Falls back to the public docs repository |
| gemini | `ai.google.dev/gemini-api/docs/deprecations.md.txt` | Google calls shutdown dates "earliest possible". Sundial treats them as firm so you are warned early |
| bedrock | `docs.aws.amazon.com/bedrock/latest/userguide/model-lifecycle-legacy.md` | legacy and end-of-life dates |

Each parser is a pure function tested against a real capture of its page. A page that parses to
nothing fails loudly as a changed format. Rows that contradict themselves are skipped or marked
ambiguous, with a warning that names them.

The nightly `registry` workflow reads these pages with the same parsers. An install reads them only
when it scans directly. Otherwise it imports `registry/lifecycle.json`.

## Limits

- Vertex AI has no single lifecycle page. Gemini models on Vertex are matched against the Gemini
  API page and flagged approximate. Claude on Vertex is not covered.
- Bedrock models launched under the policy that started on 7 September 2026 publish their dates on
  individual model cards, which are not read yet.
- Provider management APIs for Azure and Bedrock are not used yet, so lifecycle data is
  account-independent.
- Dates on a provider's own platform can differ from the same model on a partner platform. Sundial
  keeps them separate: `anthropic`, `bedrock` and `azure` each have their own record for a Claude
  model.
- Gateways other than litellm and generic ASGI are not integrated yet.
- With the defaults, a change on a provider page can take up to about two days to reach a gateway:
  up to a day for the nightly run, then up to `auto_scan_hours` for the next refresh.
- The published registry file is not signed. An install trusts whatever `registry_url` serves. Set
  it to `null`, or to a copy you control, if that matters to you.
- Usage is counted per process and per SQLite file. Each replica of a multi-replica gateway keeps
  its own counts, so `status --in-use` only sees the traffic recorded in the file it reads.
- When a provider page breaks the nightly parser, the nightly run goes red, but installs keep
  importing that provider's last good records and log nothing, because the published file only
  changes when a lifecycle fact changes.
- Model names longer than 256 characters are not counted. At most 10,000 distinct entries wait in
  memory between writes. Calls that would add another are dropped, and a warning is logged.
- Counts still in memory when a process is killed without a clean exit are lost.

## Development

```bash
uv sync
uv run pytest -q
uv run ruff check
uv run ruff format --check
```

Fixtures in `tests/fixtures` are real captures trimmed to headings and factual rows. See the
README there for how to refresh them.

## Releasing

Releases go to PyPI from a version tag through GitHub Actions.
[RELEASING.md](https://github.com/rameswaran-mohan/sundial/blob/main/RELEASING.md) has the one-time
setup, the steps for each release and what every workflow does.

## Licence

Apache-2.0
