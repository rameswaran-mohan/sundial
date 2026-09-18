# Deprecations
## Overview
## Model deprecation notice periods
- **Generally available models:** At least 6 months.
- **Specialized variants of generally available models:** At least 3 months. Examples include chat variants such as `gpt-5.1-chat-latest`, Codex variants such as `gpt-5.3-codex`, and deep research variants such as `o3-deep-research`.
- **Preview models:** Preview models, identified by `preview` in the model name, may be retired with much shorter notice, such as 2 weeks. Examples include `computer-use-preview` and `gpt-4o-audio-preview`. We don't recommend using preview models for business-critical production workloads unless you can migrate on short notice.
## Deprecation vs. legacy
## Upcoming deprecations
### 2026-09-11: GPT-5.4-Cyber
| Shutdown date | Model / system  | Recommended replacement |
| ------------- | --------------- | ----------------------- |
| Oct 1, 2026   | `gpt-5.4-cyber` | `gpt-5.6-cyber`         |
### 2026-08-26: Transcription models
| Shutdown date | Model / system              | Recommended replacement                   |
| ------------- | --------------------------- | ----------------------------------------- |
| Feb 26, 2027  | `whisper-1`                 | `gpt-live-transcribe` or `gpt-transcribe` |
| Feb 26, 2027  | `gpt-4o-transcribe`         | `gpt-live-transcribe` or `gpt-transcribe` |
| Feb 26, 2027  | `gpt-4o-mini-transcribe`    | `gpt-live-transcribe` or `gpt-transcribe` |
| Feb 26, 2027  | `gpt-4o-transcribe-diarize` | `gpt-live-transcribe` or `gpt-transcribe` |
### 2026-07-20: Legacy audio, realtime, and transcription models
| Shutdown date | Model family / snapshot             | Recommended replacement             |
| ------------- | ----------------------------------- | ----------------------------------- |
| Jan 20, 2027  | `gpt-realtime`                      | `gpt-realtime-2.1`                  |
| Jan 20, 2027  | `gpt-audio`                         | `gpt-audio-1.5`                     |
| Jan 20, 2027  | `gpt-4o-audio`                      | `gpt-audio-1.5`                     |
| Jan 20, 2027  | `gpt-4o-realtime`                   | `gpt-realtime-2.1`                  |
| Jan 20, 2027  | `gpt-realtime-mini`                 | `gpt-realtime-2.1-mini`             |
| Jan 20, 2027  | `gpt-audio-mini`                    | `gpt-audio-1.5`                     |
| Jan 20, 2027  | `gpt-4o-mini-realtime`              | `gpt-realtime-2.1-mini`             |
| Jan 20, 2027  | `gpt-4o-mini-audio`                 | `gpt-audio-1.5`                     |
| Jan 20, 2027  | `gpt-4o-mini-transcribe-2025-03-20` | `gpt-4o-mini-transcribe-2025-12-15` |
### 2026-06-11: GPT-5 and o3 model deprecations
| Shutdown date | Model / system          | Recommended replacement               |
| ------------- | ----------------------- | ------------------------------------- |
| Dec 11, 2026  | `gpt-5-2025-08-07`      | `gpt-5.6-sol`                         |
| Dec 11, 2026  | `gpt-5-mini-2025-08-07` | `gpt-5.6-terra`                       |
| Dec 11, 2026  | `gpt-5-nano-2025-08-07` | `gpt-5.6-luna`                        |
| Dec 11, 2026  | `gpt-5-pro-2025-10-06`  | `gpt-5.6-sol` (`reasoning.mode: pro`) |
| Dec 11, 2026  | `o3-2025-04-16`         | `gpt-5.6-sol`                         |
| Dec 11, 2026  | `o3-pro-2025-06-10`     | `gpt-5.6-sol` (`reasoning.mode: pro`) |
### 2026-06-03: Reusable prompts
| Date         | Update                                                                       |
| ------------ | ---------------------------------------------------------------------------- |
| June 3, 2026 | Deprecation announced and prompt creation de-emphasized in the platform.     |
| Nov 30, 2026 | The `v1/prompts` API and reusable prompt objects are scheduled to shut down. |
### 2026-06-03: Evals platform
| Date         | Update                                                  |
| ------------ | ------------------------------------------------------- |
| June 3, 2026 | Deprecation announced for the Evals platform.           |
| Oct 31, 2026 | Existing evals become read-only.                        |
| Nov 30, 2026 | The Evals dashboard and API are scheduled to shut down. |
### 2026-06-03: Agent Builder
| Date         | Update                                   |
| ------------ | ---------------------------------------- |
| June 3, 2026 | Deprecation announced for Agent Builder. |
| Nov 30, 2026 | Agent Builder is scheduled to shut down. |
### 2026-06-02: GPT Image model deprecations
| Shutdown date | Model / system         | Recommended replacement |
| ------------- | ---------------------- | ----------------------- |
| Dec 1, 2026   | `gpt-image-1-mini`     | `gpt-image-2`           |
| Dec 1, 2026   | `gpt-image-1.5`        | `gpt-image-2`           |
| Dec 1, 2026   | `chatgpt-image-latest` | `gpt-image-2`           |
### Update to OpenAI’s self-serve fine-tuning
| Date         | Update                                                                                                                                                                                           |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| May 7, 2026  | Creating fine-tuning jobs or training is not available to organizations that have not previously run fine-tuning.                                                                                |
| July 2, 2026 | Creating fine-tuning jobs is no longer available to organizations that have not run inference on a fine-tuned model in the past 60 days.                                                         |
| Jan 6, 2027  | Active existing customers will no longer be able to create new fine-tuning jobs on this date. Inference on fine-tuned models will be disabled only when the underlying base model is deprecated. |
### 2026-04-22: Legacy GPT model snapshots
| Shutdown date    | Model snapshot                                                         | Substitute model                      |
| ---------------- | ---------------------------------------------------------------------- | ------------------------------------- |
| October 23, 2026 | `gpt-3.5-turbo-0125` \| `gpt-3.5-turbo`, `gpt-3.5-turbo-completions`   | `gpt-5.6-terra`                       |
| October 23, 2026 | `gpt-4-0613` \| `gpt-4`, `gpt-4-0613-completions`, `gpt-4-completions` | `gpt-5.6-sol`                         |
| October 23, 2026 | `gpt-4-1106-preview`                                                   | `gpt-5.6-sol`                         |
| October 23, 2026 | `gpt-4-turbo` \| `gpt-4-turbo-2024-04-09`, `gpt-4-turbo-completions`   | `gpt-5.6-sol`                         |
| October 23, 2026 | `gpt-4.1-nano` \| `gpt-4.1-nano-2025-04-14`                            | `gpt-5.6-luna`                        |
| October 23, 2026 | `gpt-4o-2024-05-13`                                                    | `gpt-5.6-sol`                         |
| October 23, 2026 | `gpt-image-1`                                                          | `gpt-image-2`                         |
| October 23, 2026 | `o1-2024-12-17` \| `o1`                                                | `gpt-5.6-sol`                         |
| October 23, 2026 | `o1-pro-2025-03-19` \| `o1-pro`                                        | `gpt-5.6-sol` (`reasoning.mode: pro`) |
| October 23, 2026 | `o3-mini-2025-01-31` \| `o3-mini`                                      | `gpt-5.6-sol`                         |
| October 23, 2026 | `ft-o4-mini-2025-04-16`                                                | `gpt-5.6-terra`                       |
| October 23, 2026 | `o4-mini-2025-04-16` \| `o4-mini`                                      | `gpt-5.6-terra`                       |
| Shutdown date    | Model snapshot               | Recommended replacement base model |
| ---------------- | ---------------------------- | ---------------------------------- |
| October 23, 2026 | `ft-gpt-3.5-turbo`           | `gpt-5.6-terra`                    |
| October 23, 2026 | `ft-gpt-4`                   | `gpt-5.6-sol`                      |
| October 23, 2026 | `ft-gpt-4.1-nano-2025-04-14` | `gpt-5.6-luna`                     |
| October 23, 2026 | `ft-babbage-002`             | `gpt-5.6-terra`                    |
| October 23, 2026 | `ft-davinci-002`             | `gpt-5.6-terra`                    |
### 2026-03-24: Sora 2 video generation models and Videos API
| Shutdown date | Model / system          | Recommended replacement |
| ------------- | ----------------------- | ----------------------- |
| 2026-09-24    | Videos API              | ---                     |
| 2026-09-24    | `sora-2`                | ---                     |
| 2026-09-24    | `sora-2-pro`            | ---                     |
| 2026-09-24    | `sora-2-2025-10-06`     | ---                     |
| 2026-09-24    | `sora-2-2025-12-08`     | ---                     |
| 2026-09-24    | `sora-2-pro-2025-10-06` | ---                     |
### 2025-09-26: Legacy GPT model snapshots
| Shutdown date | Model / system           | Recommended replacement |
| ------------- | ------------------------ | ----------------------- |
| 2026-09-28    | `gpt-3.5-turbo-instruct` | `gpt-5.6-terra`         |
| 2026-09-28    | `babbage-002`            | `gpt-5.6-terra`         |
| 2026-09-28    | `davinci-002`            | `gpt-5.6-terra`         |
| 2026-09-28    | `gpt-3.5-turbo-1106`     | `gpt-5.6-terra`         |
## Past deprecations
### 2026-05-08: `gpt-5.2-chat-latest` and `gpt-5.3-chat-latest` model snapshots
| Shutdown date | Model / system        | Recommended replacement |
| ------------- | --------------------- | ----------------------- |
| Aug 10, 2026  | `gpt-5.2-chat-latest` | `gpt-5.6-sol`           |
| Aug 10, 2026  | `gpt-5.3-chat-latest` | `gpt-5.6-sol`           |
### 2026-04-22: Legacy GPT model snapshots (July 2026 shutdown)
| Shutdown date | Model snapshot                                                | Substitute model        |
| ------------- | ------------------------------------------------------------- | ----------------------- |
| July 23, 2026 | `computer-use-preview-2025-03-11` \| `computer-use-preview`   | `gpt-5.6-terra`         |
| July 23, 2026 | `gpt-4o-mini-search-preview-2025-03-11`                       | `gpt-5.6-terra`         |
| July 23, 2026 | `gpt-4o-search-preview-2025-03-11`                            | `gpt-5.6-terra`         |
| July 23, 2026 | `gpt-5-chat-latest`                                           | `gpt-5.6-sol`           |
| July 23, 2026 | `gpt-5-codex`                                                 | `gpt-5.6-sol`           |
| July 23, 2026 | `gpt-5.1-chat-latest`                                         | `gpt-5.6-sol`           |
| July 23, 2026 | `gpt-5.1-codex`                                               | `gpt-5.6-sol`           |
| July 23, 2026 | `gpt-5.1-codex-max`                                           | `gpt-5.6-sol`           |
| July 23, 2026 | `gpt-5.1-codex-mini`                                          | `gpt-5.6-terra`         |
| July 23, 2026 | `gpt-audio-mini-2025-10-06`                                   | `gpt-audio-1.5`         |
| July 23, 2026 | `gpt-realtime-mini-2025-10-06`                                | `gpt-realtime-2.1-mini` |
| July 23, 2026 | `o3-deep-research-2025-06-26` \| `o3-deep-research`           | `gpt-5.6-sol`           |
| July 23, 2026 | `o4-mini-deep-research-2025-06-26` \| `o4-mini-deep-research` | `gpt-5.6-sol`           |
| July 23, 2026 | `gpt-5.2-codex`                                               | `gpt-5.6-sol`           |
### 2025-11-18: `chatgpt-4o-latest` snapshot
| Shutdown date | Model / system      | Recommended replacement |
| ------------- | ------------------- | ----------------------- |
| 2026-02-17    | `chatgpt-4o-latest` | `gpt-5.1-chat-latest`   |
### 2025-11-17: `codex-mini-latest` model snapshot
| Shutdown date | Model / system      | Recommended replacement |
| ------------- | ------------------- | ----------------------- |
| 2026-02-12    | `codex-mini-latest` | `gpt-5-codex-mini`      |
### 2025-11-14: DALL·E model snapshots
| Shutdown date | Model / system | Recommended replacement                             |
| ------------- | -------------- | --------------------------------------------------- |
| 2026-05-12    | `dall-e-2`     | `gpt-image-2`, `gpt-image-1`, or `gpt-image-1-mini` |
| 2026-05-12    | `dall-e-3`     | `gpt-image-2`, `gpt-image-1`, or `gpt-image-1-mini` |
### 2025-09-26: Legacy GPT model snapshots (March 2026 shutdown)
| Shutdown date | Model / system                                                                                                             | Recommended replacement |
| ------------- | -------------------------------------------------------------------------------------------------------------------------- | ----------------------- |
| 2026‑03‑26    | `gpt-4-0314`                                                                                                               | `gpt-5` or `gpt-4.1*`   |
| 2026‑03‑26    | `gpt-4-1106-preview`                                                                                                       | `gpt-5` or `gpt-4.1*`   |
| 2026‑03‑26    | `gpt-4-0125-preview` (including `gpt-4-turbo-preview` and `gpt-4-turbo-preview-completions`, which point to this snapshot) | `gpt-5` or `gpt-4.1*`   |
### 2025-09-15: Realtime API Beta
| Shutdown date | Model / system           | Recommended replacement |
| ------------- | ------------------------ | ----------------------- |
| 2026‑05‑12    | OpenAI-Beta: realtime=v1 | Realtime API            |
### 2025-09-15: `gpt-4o-realtime-preview` models
| Shutdown date | Model / system                       | Recommended replacement |
| ------------- | ------------------------------------ | ----------------------- |
| 2026-05-07    | `gpt-4o-realtime-preview`            | `gpt-realtime-1.5`      |
| 2026-05-07    | `gpt-4o-realtime-preview-2025-06-03` | `gpt-realtime-1.5`      |
| 2026-05-07    | `gpt-4o-realtime-preview-2024-12-17` | `gpt-realtime-1.5`      |
| 2026-05-07    | `gpt-4o-mini-realtime-preview`       | `gpt-realtime-mini`     |
| 2026-05-07    | `gpt-4o-audio-preview`               | `gpt-audio-1.5`         |
| 2026-05-07    | `gpt-4o-mini-audio-preview`          | `gpt-audio-mini`        |
### 2025-08-20: Assistants API
| Shutdown date | Model / system | Recommended replacement             |
| ------------- | -------------- | ----------------------------------- |
| 2026‑08‑26    | Assistants API | Responses API and Conversations API |
### 2025-06-10: `gpt-4o-realtime-preview-2024-10-01`
| Shutdown date | Model / system                       | Recommended replacement |
| ------------- | ------------------------------------ | ----------------------- |
| 2025-10-10    | `gpt-4o-realtime-preview-2024-10-01` | `gpt-realtime-1.5`      |
### 2025-06-10: `gpt-4o-audio-preview-2024-10-01`
| Shutdown date | Model / system                    | Recommended replacement |
| ------------- | --------------------------------- | ----------------------- |
| 2025-10-10    | `gpt-4o-audio-preview-2024-10-01` | `gpt-audio-1.5`         |
### 2025-04-28: `text-moderation`
| Shutdown date | Model / system           | Recommended replacement |
| ------------- | ------------------------ | ----------------------- |
| 2025-10-27    | `text-moderation-007`    | `omni-moderation`       |
| 2025-10-27    | `text-moderation-stable` | `omni-moderation`       |
| 2025-10-27    | `text-moderation-latest` | `omni-moderation`       |
### 2025-04-28: `o1-preview` and `o1-mini`
| Shutdown date | Model / system | Recommended replacement |
| ------------- | -------------- | ----------------------- |
| 2025-07-28    | `o1-preview`   | `o3`                    |
| 2025-10-27    | `o1-mini`      | `o4-mini`               |
### 2025-04-14: GPT-4.5-preview
| Shutdown date | Model / system    | Recommended replacement |
| ------------- | ----------------- | ----------------------- |
| 2025-07-14    | `gpt-4.5-preview` | `gpt-4.1`               |
### 2024-10-02: Assistants API beta v1
| Shutdown date | Model / system             | Recommended replacement    |
| ------------- | -------------------------- | -------------------------- |
| 2024-12-18    | OpenAI-Beta: assistants=v1 | OpenAI-Beta: assistants=v2 |
### 2024-08-29: Fine-tuning training on babbage-002 and davinci-002 models
| Shutdown date | Model / system                            | Recommended replacement |
| ------------- | ----------------------------------------- | ----------------------- |
| 2024-10-28    | New fine-tuning training on `babbage-002` | `gpt-4o-mini`           |
| 2024-10-28    | New fine-tuning training on `davinci-002` | `gpt-4o-mini`           |
### 2024-06-06: GPT-4-32K and Vision Preview models
| Shutdown date | Deprecated model            | Deprecated model price                             | Recommended replacement |
| ------------- | --------------------------- | -------------------------------------------------- | ----------------------- |
| 2025-06-06    | `gpt-4-32k`                 | $60.00 / 1M input tokens + $120 / 1M output tokens | `gpt-4o`                |
| 2025-06-06    | `gpt-4-32k-0613`            | $60.00 / 1M input tokens + $120 / 1M output tokens | `gpt-4o`                |
| 2025-06-06    | `gpt-4-32k-0314`            | $60.00 / 1M input tokens + $120 / 1M output tokens | `gpt-4o`                |
| 2024-12-06    | `gpt-4-vision-preview`      | $10.00 / 1M input tokens + $30 / 1M output tokens  | `gpt-4o`                |
| 2024-12-06    | `gpt-4-1106-vision-preview` | $10.00 / 1M input tokens + $30 / 1M output tokens  | `gpt-4o`                |
### 2023-11-06: Chat model updates
| Shutdown date | Deprecated model         | Deprecated model price                             | Recommended replacement |
| ------------- | ------------------------ | -------------------------------------------------- | ----------------------- |
| 2024-09-13    | `gpt-3.5-turbo-0613`     | $1.50 / 1M input tokens + $2.00 / 1M output tokens | `gpt-3.5-turbo`         |
| 2024-09-13    | `gpt-3.5-turbo-16k-0613` | $3.00 / 1M input tokens + $4.00 / 1M output tokens | `gpt-3.5-turbo`         |
### 2023-08-22: Fine-tunes endpoint
#### Fine-tunes endpoint
| Shutdown date | System           | Recommended replacement |
| ------------- | ---------------- | ----------------------- |
| 2024-01-04    | `/v1/fine-tunes` | `/v1/fine_tuning/jobs`  |
### 2023-07-06: GPT and embeddings
#### InstructGPT models
| Shutdown date | Deprecated model   | Deprecated model price | Recommended replacement  |
| ------------- | ------------------ | ---------------------- | ------------------------ |
| 2024-01-04    | `text-ada-001`     | $0.40 / 1M tokens      | `gpt-3.5-turbo-instruct` |
| 2024-01-04    | `text-babbage-001` | $0.50 / 1M tokens      | `gpt-3.5-turbo-instruct` |
| 2024-01-04    | `text-curie-001`   | $2.00 / 1M tokens      | `gpt-3.5-turbo-instruct` |
| 2024-01-04    | `text-davinci-001` | $20.00 / 1M tokens     | `gpt-3.5-turbo-instruct` |
| 2024-01-04    | `text-davinci-002` | $20.00 / 1M tokens     | `gpt-3.5-turbo-instruct` |
| 2024-01-04    | `text-davinci-003` | $20.00 / 1M tokens     | `gpt-3.5-turbo-instruct` |
#### Base GPT models
| Shutdown date | Deprecated model   | Deprecated model price | Recommended replacement  |
| ------------- | ------------------ | ---------------------- | ------------------------ |
| 2024-01-04    | `ada`              | $0.40 / 1M tokens      | `babbage-002`            |
| 2024-01-04    | `babbage`          | $0.50 / 1M tokens      | `babbage-002`            |
| 2024-01-04    | `curie`            | $2.00 / 1M tokens      | `davinci-002`            |
| 2024-01-04    | `davinci`          | $20.00 / 1M tokens     | `davinci-002`            |
| 2024-01-04    | `code-davinci-002` | ---                    | `gpt-3.5-turbo-instruct` |
#### Edit models & endpoint
| Shutdown date | Model / system          | Recommended replacement |
| ------------- | ----------------------- | ----------------------- |
| 2024-01-04    | `text-davinci-edit-001` | `gpt-4o`                |
| 2024-01-04    | `code-davinci-edit-001` | `gpt-4o`                |
| 2024-01-04    | `/v1/edits`             | `/v1/chat/completions`  |
#### Fine-tuning GPT models
| Shutdown date | Deprecated model | Training price     | Usage price         | Recommended replacement                  |
| ------------- | ---------------- | ------------------ | ------------------- | ---------------------------------------- |
| 2024-01-04    | `ada`            | $0.40 / 1M tokens  | $1.60 / 1M tokens   | `babbage-002`                            |
| 2024-01-04    | `babbage`        | $0.60 / 1M tokens  | $2.40 / 1M tokens   | `babbage-002`                            |
| 2024-01-04    | `curie`          | $3.00 / 1M tokens  | $12.00 / 1M tokens  | `davinci-002`                            |
| 2024-01-04    | `davinci`        | $30.00 / 1M tokens | $120.00 / 1K tokens | `davinci-002`, `gpt-3.5-turbo`, `gpt-4o` |
#### First-generation text embedding models
| Shutdown date | Deprecated model                | Deprecated model price | Recommended replacement  |
| ------------- | ------------------------------- | ---------------------- | ------------------------ |
| 2024-01-04    | `text-similarity-ada-001`       | $4.00 / 1M tokens      | `text-embedding-3-small` |
| 2024-01-04    | `text-search-ada-doc-001`       | $4.00 / 1M tokens      | `text-embedding-3-small` |
| 2024-01-04    | `text-search-ada-query-001`     | $4.00 / 1M tokens      | `text-embedding-3-small` |
| 2024-01-04    | `code-search-ada-code-001`      | $4.00 / 1M tokens      | `text-embedding-3-small` |
| 2024-01-04    | `code-search-ada-text-001`      | $4.00 / 1M tokens      | `text-embedding-3-small` |
| 2024-01-04    | `text-similarity-babbage-001`   | $5.00 / 1M tokens      | `text-embedding-3-small` |
| 2024-01-04    | `text-search-babbage-doc-001`   | $5.00 / 1M tokens      | `text-embedding-3-small` |
| 2024-01-04    | `text-search-babbage-query-001` | $5.00 / 1M tokens      | `text-embedding-3-small` |
| 2024-01-04    | `code-search-babbage-code-001`  | $5.00 / 1M tokens      | `text-embedding-3-small` |
| 2024-01-04    | `code-search-babbage-text-001`  | $5.00 / 1M tokens      | `text-embedding-3-small` |
| 2024-01-04    | `text-similarity-curie-001`     | $20.00 / 1M tokens     | `text-embedding-3-small` |
| 2024-01-04    | `text-search-curie-doc-001`     | $20.00 / 1M tokens     | `text-embedding-3-small` |
| 2024-01-04    | `text-search-curie-query-001`   | $20.00 / 1M tokens     | `text-embedding-3-small` |
| 2024-01-04    | `text-similarity-davinci-001`   | $200.00 / 1M tokens    | `text-embedding-3-small` |
| 2024-01-04    | `text-search-davinci-doc-001`   | $200.00 / 1M tokens    | `text-embedding-3-small` |
| 2024-01-04    | `text-search-davinci-query-001` | $200.00 / 1M tokens    | `text-embedding-3-small` |
### 2023-06-13: Updated chat models
| Shutdown date          | Legacy model | Legacy model price                                   | Recommended replacement |
| ---------------------- | ------------ | ---------------------------------------------------- | ----------------------- |
| at earliest 2024-06-13 | `gpt-4-0314` | $30.00 / 1M input tokens + $60.00 / 1M output tokens | `gpt-4o`                |
| Shutdown date | Deprecated model     | Deprecated model price                                | Recommended replacement |
| ------------- | -------------------- | ----------------------------------------------------- | ----------------------- |
| 2024-09-13    | `gpt-3.5-turbo-0301` | $15.00 / 1M input tokens + $20.00 / 1M output tokens  | `gpt-3.5-turbo`         |
| 2025-06-06    | `gpt-4-32k-0314`     | $60.00 / 1M input tokens + $120.00 / 1M output tokens | `gpt-4o`                |
### 2023-03-20: Codex models
| Shutdown date | Deprecated model   | Recommended replacement |
| ------------- | ------------------ | ----------------------- |
| 2023-03-23    | `code-davinci-002` | `gpt-4o`                |
| 2023-03-23    | `code-davinci-001` | `gpt-4o`                |
| 2023-03-23    | `code-cushman-002` | `gpt-4o`                |
| 2023-03-23    | `code-cushman-001` | `gpt-4o`                |
### 2022-06-03: Legacy endpoints
| Shutdown date | System                | Recommended replacement                                                                               |
| ------------- | --------------------- | ----------------------------------------------------------------------------------------------------- |
| 2022-12-03    | `/v1/engines`         | [/v1/models](https://platform.openai.com/docs/api-reference/models/list)                              |
| 2022-12-03    | `/v1/search`          | [View transition guide](https://help.openai.com/en/articles/6272952-search-transition-guide)          |
| 2022-12-03    | `/v1/classifications` | [View transition guide](https://help.openai.com/en/articles/6272941-classifications-transition-guide) |
| 2022-12-03    | `/v1/answers`         | [View transition guide](https://help.openai.com/en/articles/6233728-answers-transition-guide)         |
### Plain-text aliases
