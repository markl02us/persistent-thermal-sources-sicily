#!/bin/bash
# One-shot GitHub publish script. Run AFTER `gh auth login`.
set -e

REPO_NAME="persistent-thermal-sources-sicily"
DESCRIPTION="Open-data catalog of persistent thermal anomalies in Sicily (volcanoes, refineries, glasshouses, solar farms, quarries) that cause false-positive wildfire detections."

if ! gh auth status >/dev/null 2>&1; then
    echo "ERROR: gh is not authenticated. Run 'gh auth login' first."
    exit 1
fi

OWNER=$(gh api user --jq .login)
echo "GitHub user: $OWNER"

if gh repo view "$OWNER/$REPO_NAME" >/dev/null 2>&1; then
    echo "Repo $OWNER/$REPO_NAME already exists. Push to existing remote:"
    git remote add origin "git@github.com:$OWNER/$REPO_NAME.git" 2>/dev/null || git remote set-url origin "git@github.com:$OWNER/$REPO_NAME.git"
    git branch -M main
    git push -u origin main
else
    echo "Creating new public repo $OWNER/$REPO_NAME..."
    gh repo create "$REPO_NAME" --public \
        --description "$DESCRIPTION" \
        --source . --remote origin --push
fi

echo ""
echo "==================================================================="
echo "Published! Next steps:"
echo "  1. Visit https://github.com/$OWNER/$REPO_NAME"
echo "  2. Enable Zenodo integration:"
echo "     - Go to https://zenodo.org/account/settings/github/"
echo "     - Sign in with GitHub, flip the toggle for this repo"
echo "  3. Create a release (gh release create v1.0.0 -t 'v1.0.0' -n 'Initial release')"
echo "     - This auto-creates the Zenodo DOI"
echo "==================================================================="
