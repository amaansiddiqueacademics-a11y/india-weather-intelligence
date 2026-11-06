# Contributing

Thanks for helping with India Weather Intelligence. This started as a Data Warehousing and Mining mini project at Vidyalankar Institute of Technology, so it is kept small and easy to read. Bug reports, fixes and clear improvements are all welcome.

## Before you start

- For anything bigger than a small fix, open an issue first so we can agree on the approach.
- Be kind and constructive. Everyone here follows the [Code of Conduct](CODE_OF_CONDUCT.md).

## Set up

Follow the [Quick start](README.md#quick-start): Docker (`docker compose up -d --build`, then `docker compose run --rm app python -m wx.pipeline`) or your own Python 3.12+/PostgreSQL 16 with `pip install -r requirements.txt`.

## Checks to run before a pull request

```bash
pytest                                   # from the repository root: 59 tests (40 need the loaded warehouse)
ruff check .                             # lint rules in ruff.toml
for f in web/*.js; do node --check "$f"; done
```

CI runs the same checks on Python 3.12 and 3.13 and builds the Docker image. A pull request should keep it green.

## Things to know about the code

- **Pipeline order matters.** `python -m wx.pipeline` runs the steps in order (`--from STEP` resumes, `--skip-download` reuses data). If you add a step, add it to `STEPS` in `src/wx/pipeline.py` and to the README; `tests/test_pipeline.py` checks they match.
- **ETL must stay idempotent.** Loads go through staging tables and `ON CONFLICT`, so re-running a step never duplicates rows. Keep it that way and add a test for any new rule.
- **No future leakage.** Model features may only use values at or before the issue time, and splits are chronological (train 2002-2015, validate 2016-2018, test 2019-2022). Never tune on the test years.
- **Compare with baselines.** A new model or metric is only reported next to the "No change" and "Usual climate" baselines.
- **Saved models are tied to scikit-learn 1.9.1.** If you change a dependency version, retrain (`python -m wx.pipeline --from ml --skip-download`) and commit the new `models/` and `reports/ml_report.json` together.
- **Dashboard scripts share one global scope.** The files in `web/` are plain `<script>` tags, not modules, so top-level names must be unique across all of them.
- **No secrets in the repo.** Database settings come from `WX_DSN` and `WX_ADMIN_DSN`. Raw data is downloaded, not committed.

## Pull requests

- Keep each pull request to one change, and describe what changed and how you tested it.
- Write commit messages in the imperative ("Fix rain threshold check"), with a short body when the reason isn't obvious.
- Update the README or `docs/` if behaviour, commands or results change.

By contributing, you agree that your work is released under the project's [MIT License](LICENSE).
