# Releasing

Releases go to PyPI as `sundial-llm` from GitHub Actions, using PyPI trusted publishing. No PyPI
token is stored anywhere: not in repository secrets, not in environment secrets, not in the
workflow. PyPI trusts the short-lived identity that GitHub issues to the `publish` job of
`release.yml`, and nothing else.

## One-time setup

Do both of these before pushing the first tag. Until the trusted publisher exists on PyPI, the
`publish` job fails: PyPI refuses the upload because it does not recognise the workflow. The `build`
job still succeeds, and the built files stay attached to the run as the `dist` artifact.

1. On PyPI, add a trusted publisher. For a project that has never been published, use "Your
   account", then "Publishing", then "Add a new pending publisher". Enter exactly:

   | Field             | Value              |
   | ----------------- | ------------------ |
   | PyPI project name | `sundial-llm`      |
   | Owner             | `rameswaran-mohan` |
   | Repository name   | `sundial`          |
   | Workflow name     | `release.yml`      |
   | Environment name  | `pypi`             |

2. On GitHub, open the `rameswaran-mohan/sundial` repository, then "Settings", then "Environments",
   then "New environment", and create an environment named exactly `pypi`. The `publish` job runs in
   this environment. Adding required reviewers there is optional and makes every release wait for
   an approval click.

## Each release

1. Bump the version to the same `X.Y.Z` in both files:
   - `version = "X.Y.Z"` in `pyproject.toml`
   - `__version__ = "X.Y.Z"` in `src/sundial_llm/__init__.py`

   Then run `uv lock` so `uv.lock` records the new version too.
2. Commit: `git commit -am "release: vX.Y.Z"`
3. Tag: `git tag vX.Y.Z`
4. Push: `git push origin main --tags`

The tag push starts `release.yml`. The `build` job runs `uv build` and uploads `dist/`; the
`publish` job downloads it and uploads it to PyPI. The workflow does not compare the tag with the
version in the files, so check they match before tagging.

## The other workflows

- `ci.yml` runs ruff and the test suite on Python 3.10 and 3.12 for every pull request and every
  push to `main`.
- `registry.yml` runs nightly at 03:17 UTC and on demand. It seeds a fresh database from the
  committed `registry/lifecycle.json` when that file exists, scans every source, exports the result
  back to `registry/lifecycle.json` and commits it to `main` when it changed. That file is the
  default `registry_url` every Sundial install reads, so this workflow is what keeps installs
  current. If the seed import fails the job stops before export, so the published file is never
  overwritten from an empty database. If some sources fail the scan, the job still exports what it
  has and commits it when it changed, then fails so the run shows red.
- `costmap-drift.yml` runs nightly at 03:47 UTC and on demand. It imports the published registry,
  compares it with litellm's cost map, and commits `registry/litellm-costmap-patch.json` and
  `registry/litellm-costmap-drift.md` when they changed.

Both nightly workflows push to `main` as `github-actions[bot]` and share one concurrency group, so
they never push at the same time. If `main` is protected against direct pushes, allow GitHub
Actions to bypass that rule or both workflows fail at `git push`. They install Sundial without the
dev dependency group, so litellm and the test tools never run in a job that can push. GitHub
disables scheduled workflows in a public repository after 60 days without repository activity; if
the nightly registry stops, re-enable it from the Actions tab.

After the first push to `main`, run the `registry` workflow once by hand: open "Actions", then
"registry", then "Run workflow". That creates `registry/lifecycle.json` before any install depends
on it. Until it exists, installs find nothing at the default `registry_url` and scan the provider
pages directly, and `costmap-drift.yml` fails because it has no registry to import.
