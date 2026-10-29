# Sundial with Portkey

Portkey's webhook guardrail calls `sundial serve` before each request. Sundial answers with a
verdict. It can refuse the request, rewrite the model to a successor you name, or let the request
through with a warning in Portkey's hook results. This setup was run live against the
open-source Portkey gateway 1.15.2 on 2026-10-30. Portkey's hosted gateway was not tried.

## Run sundial serve

```bash
pip install "sundial-llm[serve]"
sundial import --config sundial.yaml \
    https://raw.githubusercontent.com/rameswaran-mohan/sundial/main/registry/lifecycle.json
export SUNDIAL_SERVE_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
sundial serve --config sundial.yaml --host 127.0.0.1 --port 8765
```

```
Serving lifecycle decisions on http://127.0.0.1:8765 (544 records)
```

Import the registry before the first start: on an empty database the service starts with 0 records
and allows every model until its first background refresh has landed and the guard next reloads
its records, which it does every `reload_seconds` (300 by default).

The service reads the same config file as the other integrations: the policy, the `successors`
and the database. Run it where the gateway can reach it. On an address that is not loopback, set
`SUNDIAL_SERVE_TOKEN`, or anything that can reach the port can call it. `GET /healthz` answers
`{"status": "ok", "records": N}` without the token, for a liveness probe.

## Add the guardrail

Put a `before_request_hooks` entry in the Portkey config your requests use. With the open-source
gateway, send the config in the `x-portkey-config` request header. A config sent that way applies
only to the requests that carry it, and every client that sends it holds Sundial's token. A config
or guardrail saved in Portkey avoids both, because requests then refer to it instead of carrying
it. Saved configs live in Portkey's platform, not in the open-source gateway: 1.15.2 on its own
reads `x-portkey-config` as inline JSON. The live run did not try a saved config.

This is the config from the live run, without its mock upstream and with the secrets replaced:

```json
{
  "provider": "openai",
  "api_key": "sk-...",
  "before_request_hooks": [
    {
      "id": "sundial-lifecycle",
      "type": "guardrail",
      "deny": true,
      "checks": [
        {
          "id": "default.webhook",
          "parameters": {
            "webhookURL": "http://127.0.0.1:8765/portkey/before-request",
            "headers": {"Authorization": "Bearer <SUNDIAL_SERVE_TOKEN>"},
            "timeout": 3000
          }
        }
      ]
    }
  ]
}
```

- `webhookURL` is the Portkey route of `sundial serve`.
- `headers` carries the token. Sundial ignores the header when no token is set.
- `deny: true` turns Sundial's `"verdict": false` into a refused request.
- `timeout` is in milliseconds. 3000 is also Portkey's default.
- Leave `failOnError` unset. Portkey lets a request through when a check errors, unless
  `failOnError` is set. A check errors when Sundial cannot be reached, times out or refuses the
  token. With `failOnError` set, a Sundial outage would refuse requests.
- Use `before_request_hooks` only. Sundial decides before the request, and answers an
  `afterRequestHook` call with `"verdict": true` and an explanation.

### Self-hosted gateways

Gateway 1.15.2 does not check `webhookURL` against `TRUSTED_CUSTOM_HOSTS`. That list only governs
`custom_host` upstreams: it names the local and private hosts the gateway accepts as one, and
setting it replaces the default list (`localhost`, `127.0.0.1`, `::1` and `host.docker.internal`).
So the host that runs `sundial serve` need not be listed. The live run did use a listed host: it
started the gateway with `TRUSTED_CUSTOM_HOSTS=127.0.0.1,localhost` for its local mock upstream,
and `sundial serve` ran on `127.0.0.1`.

## What the client sees

These are the results of the live run, with a policy that blocks retired models in apply mode,
redirects one model to a configured successor in apply mode, and warns 90 days ahead.

| Case | What the client got |
|---|---|
| Retired model, block rule in apply mode | HTTP 446 with error type `hooks_failed`. Sundial's message is in `hook_results.before_request_hooks[].checks[].data.responseData`. The upstream was never called |
| Redirect rule in apply mode, successor configured | HTTP 200. The upstream received the successor model |
| Warn rule | HTTP 200. Served unchanged. The warning summary is in the hook results |
| Healthy model | HTTP 200. Served. No Sundial data |
| Sundial unreachable | HTTP 200. Served. Portkey's explanation reads `Webhook error: fetch failed` |

## What Sundial answers

The route answers 200 every time, except 401 for a missing or wrong token.

| Sundial decision | Answer |
|---|---|
| allow, or a model Sundial does not track | `{"verdict": true}` |
| warn, or a block or redirect in propose mode or on an approximate match | `{"verdict": true, "data": {...}}` |
| enforced block | `{"verdict": false, "data": {...}}` |
| enforced redirect | `{"verdict": true, "transformedData": {"request": {"json": ...}}, "data": {...}}` |
| `afterRequestHook`, no model, a provider Sundial does not map, bad JSON, a body over 8 MiB, a failure while building or encoding the answer | `{"verdict": true, "data": {"explanation": "..."}}` |

Any failure inside Sundial answers `"verdict": true`, and is logged. Most failures, such as a
database that cannot be read or a lookup that fails, give a plain `{"verdict": true}`, the same as
a healthy model. Only a failure while building or encoding the answer gives an explanation. To
detect failures, watch the `sundial` log, not the hook results.

Portkey shows Sundial's `data` in its hook results as `responseData`. For an enforced block,
Sundial answers:

```json
{"verdict": false,
 "data": {"action": "block", "enforced": true, "state": "retired", "retire_on": "2026-02-17",
          "days_left": -255, "replacements": ["gpt-5.1-chat-latest"], "rule": "block-retired",
          "approximate": false,
          "message": "Model 'chatgpt-4o-latest' was retired on 2026-02-17 (Sundial rule 'block-retired'). Use gpt-5.1-chat-latest instead."}}
```

`message` is the same text the litellm plugin returns when it refuses a request.

For an enforced redirect, `transformedData.request.json` is the original request body with only
`model` changed, because Portkey replaces the whole body with it. `data` adds `redirected_from` and
`target`. A redirect needs an entry in `successors` for the model name in the request body.

## Provider slugs

Portkey names the provider with its own slug. Sundial maps these and treats every other slug as
untracked, which answers `"verdict": true` with an explanation.

| Portkey slug | Sundial prefix |
|---|---|
| `openai` | `openai` |
| `anthropic` | `anthropic` |
| `azure-openai` | `azure` |
| `azure-ai` | `azure_ai` |
| `bedrock` | `bedrock` |
| `google` | `gemini` |
| `vertex-ai` | `vertex_ai` |

Every warn, block or redirect for `azure-openai` and `azure-ai` is marked approximate and never
enforced. Portkey routes Azure by deployment, and the model in the request body need not name the
deployment, so Sundial can warn there but never blocks or redirects. Vertex is matched against the
Gemini API page, so it is approximate too.

## Usage counts

Every call that names a model is counted under the model in the request body, except
`afterRequestHook` calls. `team` is taken from Portkey's `metadata.team` when it is a string.
`sundial status --in-use` reads those counts when it uses the same database as `sundial serve`.

## Catalog listing text

Text for a guardrail catalog listing, for the owner to submit.

**Name:** Sundial

**Description:** Sundial checks each request against the lifecycle of the model it names. It reads
the official lifecycle pages of OpenAI, Anthropic, Azure, Gemini and Bedrock into a registry that
is refreshed nightly. You run its small service, `sundial serve`, and point Portkey's webhook
guardrail at it. Each policy rule warns, refuses the request, or rewrites the model to a successor
you name. The default policy only warns, and refusing or rewriting needs a rule in apply mode. With
`failOnError` unset, a Sundial outage never blocks a request. Open source under Apache-2.0.

**Link:** https://github.com/rameswaran-mohan/sundial
