# FP-catalog repo migration plan

**Goal:** retire the personal username `markl02us` from all public-facing PHOENIX surfaces by moving the FP-catalog repo under an Alessandria Della Rocca Applications organization, while preserving the Zenodo DOI lineage and existing inbound citations.

**Status (2026-05-29):** prose author/credit lines have already been scrubbed to "Alessandria Della Rocca Applications" in CITATION.cff, .zenodo.json, README.md, LICENSE.code, methodology.md, and the in-repo `adr_wildfire_solution.py` mirror. The remaining attribution to `markl02us` lives only in the canonical GitHub URL `github.com/markl02us/persistent-thermal-sources-sicily`. That URL is embedded in inbound references (Zenodo deposit, INGV pack PDFs, live wins.html anchor) so it cannot be silently changed.

## Constraints

1. **Zenodo DOI continuity.** The existing 1.0.0 deposit DOI is `10.5281/zenodo.20369891`. Changing the linked GitHub repo URL breaks the GitHub-Zenodo webhook; the concept-DOI must be preserved so prior citations resolve.
2. **CC-BY 4.0 attribution.** Any downstream user who cited the catalog as "Ludwikowski, M. (2026)" did so under the prior CITATION.cff. The new "Alessandria Della Rocca Applications" attribution must be retroactively published as v1.0.1 (no data changes) so the historic citation is still valid + the new one is preferred going forward.
3. **No URL breakage.** Old `markl02us/persistent-thermal-sources-sicily` links should redirect to the new URL via GitHub's built-in repo-redirect behavior for 12+ months.

## Migration sequence

### Phase 1 — Create the org (manual, ~10 min)

Mark logs into github.com, creates the org `adr-applications` (or chosen name; the live `adrwildfi-ship-it` user holds the private PHOENIX repo already, but an org with multiple admins is preferred for the public repo).

Settings to mirror from `markl02us/persistent-thermal-sources-sicily`:
- visibility: public
- description: "Sicilian persistent-thermal-source catalog (CC-BY 4.0)"
- topics: wildfire-detection, false-positives, sicily, eumetsat, firms

Add @gaetanozambito as org member (Owner role) so attribution to Gaetano is concrete.

### Phase 2 — Transfer the repo (one click)

Repo Settings → Transfer ownership → target org. GitHub automatically issues HTTP 301 redirects from the old URL to the new URL for the foreseeable future. All inbound clones, issues, PRs, and stars carry across.

### Phase 3 — Update Zenodo coupling

1. In Zenodo dashboard, edit the v1.0.0 deposit metadata so the related-identifier (URL) points to the new repo URL.
2. Mint v1.0.1: bump version in CITATION.cff + .zenodo.json (already shows "Alessandria Della Rocca Applications" as author; the v1.0.1 deposit will pick that up automatically through the GitHub-Zenodo webhook).
3. Zenodo's concept-DOI `10.5281/zenodo.20369891` continues to resolve to "latest"; existing citations resolve to v1.0.0 (preserved for historical accuracy).

### Phase 4 — Update inbound references

- `/wins.html` and `/come-funziona` HTML (already covered by adr_wildfire_solution.py — only the URL string needs the swap once Phase 2 completes).
- INGV pack PDFs: rebuild `dist/pdf/PHOENIX_*.pdf` from sources with new URLs and re-attach to the INGV email thread.
- adr-wildfire.com OpenAPI spec contact URL (line ~2706 of adr_wildfire_solution.py).
- Any external citations in INGV correspondence, academic outreach, or social posts — track in a follow-up issue.

### Phase 5 — Verify and retire

- For 30 days, both old and new URLs resolve (GitHub redirect).
- Confirm Zenodo new-version webhook fires correctly on next git tag.
- After 30 days of clean operation, retire `markl02us/persistent-thermal-sources-sicily` only if no inbound clones/forks remain (Insights → Traffic).

## Decision items for Mark

1. **Target org name.** Options: `adr-applications`, `alessandria-della-rocca-apps`, `adrwildfi`. The shorter the better for URL ergonomics.
2. **Whether to nuke or archive the source repo after redirect period.** Archiving is safer — the redirect stays alive indefinitely. Recommended: archive, do not delete.
3. **Whether to bump to v2.0.0 instead of v1.0.1.** v2.0.0 signals the authorship/governance change but breaks the implicit "data unchanged" promise. Recommended: v1.0.1 with a CHANGELOG entry calling out the authorship change explicitly.

## Estimated effort

Phase 1-2: ~30 min (Mark + GitHub UI). Phase 3-4: ~1 hour (mostly mechanical edits + one Zenodo edit). Phase 5: passive monitoring, ~0 time. Total: ~90 minutes from go-decision to redirect-stable.
