# P5-R4 PR 1: Production Model Endpoint Safety

This change makes production model actions fail closed unless all three values
are explicit and internally consistent:

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `OPENAI_MODEL`

It does not change prompts, scientific algorithms, evidence rules, frozen P5
assets, or the accepted P5-R2/P5-R3B probe implementation.

## Runtime contract

`OPENAI_BASE_URL` must be an HTTPS URL with a syntactically valid host. User
information, query strings, fragments, malformed ports, whitespace, and known
placeholder hosts are rejected. The key and model must be non-empty,
non-placeholder values without whitespace.

Model variables are read separately from the live process environment and the
repository-root `.env.local`. They are never copied from the file into
`os.environ` by application startup. This preserves source identity across
repeated loads, file updates, module imports, tests, and worktrees.

If both sources contain the same non-empty value, it is accepted. If they
contain different non-empty values for any required variable, the action fails
closed. When only one source is non-empty, that source is used. Process values
therefore have normal deployment precedence without hiding conflicts.

An API preflight error is a safe HTTP 503 response such as:

```json
{
  "detail": {
    "code": "model_configuration_invalid",
    "blockers": ["model_configuration_source_conflict"],
    "variables": ["OPENAI_MODEL"]
  }
}
```

The response never includes the key, endpoint value, model value, environment
file contents, or a raw provider response. Background paths re-resolve the
configuration before constructing the SDK client, so a configuration change
after task acceptance also fails closed.

## Production coverage

| Production path | API preflight | Execution recheck | Request model source |
| --- | --- | --- | --- |
| P2-A paper understanding | yes | yes | validated session |
| P2-B professional review | yes | yes | validated session |
| P2-C candidate consolidation | yes | yes | validated session |
| P3 evidence verification | yes | yes | validated session |
| Legacy review proposal/challenge | yes | yes | validated session |
| Finding deep verification | yes | yes | validated session |
| Paper chat SSE | before stream | before request | validated session |
| Legacy English draft translation | when requested | before request | validated session |
| P4 writing and consistency checks | when content requires a model | before request | validated session |

All production OpenAI-compatible clients are constructed by
`backend/model_runtime.py`. The API key and endpoint passed to the client and
the model passed to `chat.completions.create` come from the same immutable
`ModelRuntimeSession`.

P4 read, edit, approval, and export operations remain available without model
configuration. An empty author draft that requires no generated report issue
also remains a local operation.

## Operator migration

1. Set the three required variables in the deployment environment or a local,
   ignored `.env.local` file.
2. Remove any stale duplicate value from the other source, or make the values
   identical.
3. Use an explicit provider HTTPS endpoint. Deployments that previously relied
   on the implicit relay now receive a safe 503 until this is configured.
4. Restart the backend when changing process environment variables. Changes to
   `.env.local` are freshly read at each model preflight, but coordinated
   deployment is still recommended.
5. Exercise a zero-network configuration test before separately authorizing
   any paid capability probe or paper workflow.

`.env.local` remains local and ignored. Real credentials must never be added to
the repository, copied into diagnostics, or placed in API error messages.

## Deliberately unchanged risks

- P4 keeps its existing retry behavior in this PR. Explicit request, Token,
  duration, Usage, and cost budgets belong to the separately reviewed P5-R4
  PR 2.
- Endpoint and requested-model validation do not prove the provider's backend
  model identity, routing, price, data handling, or actual billed amount.
- No real model request is made by this change or its tests.
- The first P5-R3B probe remains **FAIL**, P5 product acceptance remains
  **FAIL**, and the ChatGPT quality comparison remains **pending**.
