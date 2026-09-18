# Fixtures

Captured on 2026-09-19 from the official provider pages below, then trimmed to headings, table rows
and key/value list lines. Prose was removed: the fixtures carry only the factual lifecycle rows the
parsers read. Refresh them with `curl` and the same trim when a provider changes its page shape.

| File | Source |
|---|---|
| `openai_deprecations.md` | https://developers.openai.com/api/docs/deprecations.md |
| `openai_changelog.md` | https://developers.openai.com/api/docs/changelog.md |
| `anthropic_deprecations.md` | https://platform.claude.com/docs/en/about-claude/model-deprecations.md |
| `azure_retirement_schedule.md` | https://raw.githubusercontent.com/MicrosoftDocs/azure-ai-docs/main/articles/foundry/openai/includes/concepts-model-retirement-schedule-content.md |
| `gemini_deprecations.md` | https://ai.google.dev/gemini-api/docs/deprecations.md.txt |
| `bedrock_lifecycle_legacy.md` | https://docs.aws.amazon.com/bedrock/latest/userguide/model-lifecycle-legacy.md |

The Bedrock capture contains a block whose labels are shifted by one field on the live page
(`cohere.command-r-v1:0`). It is kept on purpose: the parser must skip it with a warning.
