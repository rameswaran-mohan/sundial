## Overview
## Migrating to replacements
## Notifications
## Auditing model usage
## Best practices
## Deprecation downsides and mitigations
## Model status
| API model name             | Current state | Deprecated        | Tentative retirement date          |
| :------------------------- | :------------ | :---------------- | :--------------------------------- |
| claude-fable-5-1           | Active        | N/A               | Not sooner than September 1, 2027  |
| claude-mythos-5-1          | Active        | N/A               | Not sooner than September 1, 2027  |
| claude-fable-5             | Active        | N/A               | Not sooner than June 9, 2027       |
| claude-mythos-5            | Active        | N/A               | Not sooner than June 9, 2027       |
| claude-mythos-preview      | Deprecated    | June 9, 2026      | To be announced                    |
| claude-opus-5              | Active        | N/A               | Not sooner than July 24, 2027      |
| claude-opus-4-8            | Active        | N/A               | Not sooner than May 28, 2027       |
| claude-opus-4-7            | Active        | N/A               | Not sooner than April 16, 2027     |
| claude-opus-4-6            | Active        | N/A               | Not sooner than February 5, 2027   |
| claude-opus-4-5-20251101   | Active        | N/A               | Not sooner than November 24, 2026  |
| claude-opus-4-1-20250805   | Retired       | June 5, 2026      | August 5, 2026                     |
| claude-opus-4-20250514     | Retired       | April 14, 2026    | June 15, 2026                      |
| claude-sonnet-5            | Active        | N/A               | Not sooner than June 30, 2027      |
| claude-sonnet-4-6          | Active        | N/A               | Not sooner than February 17, 2027  |
| claude-sonnet-4-5-20250929 | Active        | N/A               | Not sooner than September 29, 2026 |
| claude-sonnet-4-20250514   | Retired       | April 14, 2026    | June 15, 2026                      |
| claude-3-7-sonnet-20250219 | Retired       | October 28, 2025  | February 19, 2026                  |
| claude-haiku-4-5-20251001  | Active        | N/A               | Not sooner than October 15, 2026   |
| claude-3-5-haiku-20241022  | Retired       | December 19, 2025 | February 19, 2026                  |
| claude-3-haiku-20240307    | Retired       | February 19, 2026 | April 20, 2026                     |
## Deprecation history
### 2026-06-05: Claude Opus 4.1 model
| Retirement date | Deprecated model           | Recommended replacement |
| --------------- | -------------------------- | ----------------------- |
| August 5, 2026  | `claude-opus-4-1-20250805` | `claude-opus-4-8`       |
### 2026-04-14: Claude Sonnet 4 and Claude Opus 4 models
| Retirement date | Deprecated model           | Recommended replacement |
| --------------- | -------------------------- | ----------------------- |
| June 15, 2026   | `claude-sonnet-4-20250514` | `claude-sonnet-4-6`     |
| June 15, 2026   | `claude-opus-4-20250514`   | `claude-opus-4-8`       |
### 2026-02-19: Claude Haiku 3 model
| Retirement date | Deprecated model          | Recommended replacement     |
| --------------- | ------------------------- | --------------------------- |
| April 20, 2026  | `claude-3-haiku-20240307` | `claude-haiku-4-5-20251001` |
### 2025-12-19: Claude Haiku 3.5 model
| Retirement date   | Deprecated model            | Recommended replacement     |
| ----------------- | --------------------------- | --------------------------- |
| February 19, 2026 | `claude-3-5-haiku-20241022` | `claude-haiku-4-5-20251001` |
### 2025-10-28: Claude Sonnet 3.7 model
| Retirement date   | Deprecated model             | Recommended replacement |
| ----------------- | ---------------------------- | ----------------------- |
| February 19, 2026 | `claude-3-7-sonnet-20250219` | `claude-sonnet-4-6`     |
### 2025-08-13: Claude Sonnet 3.5 models
| Retirement date  | Deprecated model             | Recommended replacement |
| ---------------- | ---------------------------- | ----------------------- |
| October 28, 2025 | `claude-3-5-sonnet-20240620` | `claude-sonnet-4-6`     |
| October 28, 2025 | `claude-3-5-sonnet-20241022` | `claude-sonnet-4-6`     |
### 2025-06-30: Claude Opus 3 model
| Retirement date | Deprecated model         | Recommended replacement |
| --------------- | ------------------------ | ----------------------- |
| January 5, 2026 | `claude-3-opus-20240229` | `claude-opus-4-8`       |
### 2025-01-21: Claude 2, Claude 2.1, and Claude Sonnet 3 models
| Retirement date | Deprecated model           | Recommended replacement |
| --------------- | -------------------------- | ----------------------- |
| July 21, 2025   | `claude-2.0`               | `claude-opus-4-8`       |
| July 21, 2025   | `claude-2.1`               | `claude-opus-4-8`       |
| July 21, 2025   | `claude-3-sonnet-20240229` | `claude-sonnet-4-6`     |
### 2024-09-04: Claude 1 and Instant models
| Retirement date  | Deprecated model     | Recommended replacement     |
| ---------------- | -------------------- | --------------------------- |
| November 6, 2024 | `claude-1.0`         | `claude-haiku-4-5-20251001` |
| November 6, 2024 | `claude-1.1`         | `claude-haiku-4-5-20251001` |
| November 6, 2024 | `claude-1.2`         | `claude-haiku-4-5-20251001` |
| November 6, 2024 | `claude-1.3`         | `claude-haiku-4-5-20251001` |
| November 6, 2024 | `claude-instant-1.0` | `claude-haiku-4-5-20251001` |
| November 6, 2024 | `claude-instant-1.1` | `claude-haiku-4-5-20251001` |
| November 6, 2024 | `claude-instant-1.2` | `claude-haiku-4-5-20251001` |
## API parameter deprecations
| Parameter                       | Status                                 | Behavior                                                                                                                                         | Recommended replacement                                                                                                                                     |
| ------------------------------- | -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `temperature`, `top_p`, `top_k` | Deprecated (Claude Opus 4.7 and later) | Returns a 400 error when set to a non-default value on Claude 4.7 and later models and [Claude Mythos Preview](https://anthropic.com/glasswing). | Omit and use [prompting](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices) to guide model behavior. |
