# Sundial

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

## Policy

Point `SUNDIAL_CONFIG` at a YAML file. Rules are checked top to bottom and the first match wins.

```yaml
db: ~/.sundial/sundial.db
auto_scan_hours: 24          # 0 disables background scans

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

## Notifications and new models

Every scan is diffed against the registry. Changes become events: `deprecation_announced`,
`retirement_date_changed`, `replacement_named`, `model_retired`, `model_added`, `state_changed`.
Events go to Slack-compatible and generic webhooks. A new model that matches a `new_models` rule
also carries a ready-to-paste proposal:

```
[anthropic] claude-sonnet-6 is a new active model. Source: https://platform.claude.com/...

Proposal (rule 'claude-sonnet-family'): add claude-sonnet-6 to your gateway.
litellm model_list entry:
- model_name: claude-sonnet-6
  litellm_params:
    model: anthropic/claude-sonnet-6
```

The first scan is a silent baseline. A source that was down during the baseline joins quietly
later, so an outage never turns into a flood of events.

## CLI

```bash
sundial scan                                   # fetch every source, record and report changes
sundial status --within 60 --platform azure    # upcoming retirements
sundial check azure/prod-chat --base-model azure/gpt-4o-2024-05-13
sundial events --limit 20
sundial costmap-patch --cost-map model_prices_and_context_window.json --out patch.json
```

`sundial check` exits with 0 when the model is fine and 3 when a rule flags it, so it works as a CI
gate over the models your code depends on. `sundial scan` exits with 1 only when every source fails.

`costmap-patch` compares litellm's cost map with provider-published retirement dates and writes a
small patch mapping. It proposes only exact matches with firm dates.

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

## Development

```bash
uv sync
uv run pytest -q
uv run ruff check
```

Fixtures in `tests/fixtures` are real captures trimmed to headings and factual rows. See the
README there for how to refresh them.

## Licence

Apache-2.0
