# Vivek GitHub Profile

This is based on the automatic-statistics approach from Andrew Grant's profile repository, customized for Vivek Chittibothula.

## Files

- `README.md` — minimal theme-switching profile.
- `light_mode.svg` / `dark_mode.svg` — profile artwork and live stats.
- `today.py` — queries GitHub and updates the SVG stats.
- `.github/workflows/build.yaml` — runs on pushes and daily at 04:00 UTC.
- `cache/` — generated cache for repository LOC calculations.

## GitHub secrets

In **Settings → Secrets and variables → Actions**, add:

- `USER_NAME` = `VivekChittibothula`
- `ACCESS_TOKEN` = optional for public statistics; add a fine-grained GitHub token with the read permissions needed by `today.py` if you want private repositories included. The workflow falls back to its built-in token for public data.
- `BIRTH_DATE` = your birth date in `YYYY-MM-DD` format. This remains a secret; only your calculated age in years, months, and days is written to the SVG.

`BIRTH_DATE` is required: if it is missing or invalid, the updater stops before writing either SVG, so the repository stats will appear frozen too.

The workflow also needs permission to write repository contents, which is configured in the workflow.

## First run

After pushing the repository:

1. Open **Actions**.
2. Select **README build**.
3. Run **Run workflow** once.
4. Check that `light_mode.svg` and `dark_mode.svg` were updated.
5. Visit the profile and switch GitHub between light/dark mode.

The scheduled runs keep the displayed stats and time-based greeting current. They run at 08:30, 14:30, and 20:30 India time. The age is calculated using India time (`Asia/Kolkata`), so it changes after midnight in your time zone. If a GitHub API/LOC request has a temporary failure, the age and repository totals are still written and the previous LOC totals are retained.
