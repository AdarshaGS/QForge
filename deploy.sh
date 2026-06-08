#!/bin/bash
# QForge — Upload DMG to GitHub Releases
# Assumes QForge.dmg is already built (run build.sh first)

set -euo pipefail

# ─── Configuration ──────────────────────────────────────────────
GITHUB_USER="AdarshaGS"
GITHUB_REPO="QForge"
DMG_NAME="QForge.dmg"
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
DMG_PATH="$REPO_DIR/$DMG_NAME"

VERSION="${1:-}"
if [ -z "$VERSION" ]; then
    read -rp "Enter release version (e.g. 1.1.0): " VERSION
fi
TAG="v${VERSION}"

# ─── Colors / helpers ───────────────────────────────────────────
GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'
RED='\033[0;31m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
step()    { echo -e "\n${BOLD}${BLUE}━━━ $1 ━━━${NC}"; }
substep() { echo -e "  ${CYAN}→ $1${NC}"; }
ok()      { echo -e "  ${GREEN}✔  $1${NC}"; }
warn()    { echo -e "  ${YELLOW}⚠  $1${NC}"; }
die()     { echo -e "\n${RED}✘  $1${NC}"; exit 1; }

echo -e "\n${BOLD}QForge Deploy — releasing ${TAG}${NC}"
echo -e "────────────────────────────────────────"

# ─── Step 1: Preflight ──────────────────────────────────────────
step "[1/4] Preflight checks"

substep "Checking GitHub CLI (gh)..."
command -v gh >/dev/null || die "GitHub CLI not found — run: brew install gh"
ok "gh found: $(gh --version | head -1)"

substep "Checking GitHub auth..."
gh auth status >/dev/null 2>&1 || die "Not logged in — run: gh auth login"
GH_USER=$(gh api user --jq .login 2>/dev/null)
[ "$GH_USER" = "$GITHUB_USER" ] || die "Logged in as '$GH_USER', expected '$GITHUB_USER'"
gh auth setup-git
ok "Authenticated as $GH_USER on github.com"

substep "Checking DMG file..."
[ -f "$DMG_PATH" ] || die "DMG not found: $DMG_PATH  →  run build.sh first"
DMG_SIZE_HR=$(du -sh "$DMG_PATH" | cut -f1)
DMG_SIZE_BYTES=$(stat -f%z "$DMG_PATH")
ok "Found: $DMG_NAME  ($DMG_SIZE_HR, $DMG_SIZE_BYTES bytes)"

# ─── Step 2: SHA256 ─────────────────────────────────────────────
step "[2/4] SHA256 checksum"

substep "Hashing $DMG_NAME..."
SHA256=$(shasum -a 256 "$DMG_PATH" | awk '{print $1}')
ok "$SHA256"

# ─── Step 3: Create GitHub release (no attachment yet) ──────────
step "[3/4] Creating GitHub release"

if gh release view "$TAG" --repo "${GITHUB_USER}/${GITHUB_REPO}" >/dev/null 2>&1; then
    warn "Release $TAG already exists — removing it first"
    substep "Deleting existing release $TAG..."
    gh release delete "$TAG" --repo "${GITHUB_USER}/${GITHUB_REPO}" --yes
    ok "Release deleted"
    substep "Removing local git tag $TAG..."
    git tag -d "$TAG" 2>/dev/null || true
    substep "Removing remote git tag $TAG..."
    git push origin ":refs/tags/$TAG" 2>/dev/null || true
    ok "Tags cleared"
fi

substep "Creating release $TAG on GitHub..."
gh release create "$TAG" \
    --repo "${GITHUB_USER}/${GITHUB_REPO}" \
    --title "QForge ${TAG}" \
    --notes "## QForge ${TAG}

Download \`${DMG_NAME}\` below, open it, and drag QForge to Applications.

**SHA256:** \`${SHA256}\`"
ok "Release created: https://github.com/${GITHUB_USER}/${GITHUB_REPO}/releases/tag/${TAG}"

# ─── Step 4: Upload DMG with progress ───────────────────────────
step "[4/4] Uploading $DMG_NAME ($DMG_SIZE_HR)"

substep "Fetching upload URL..."
UPLOAD_URL=$(gh api "repos/${GITHUB_USER}/${GITHUB_REPO}/releases/tags/${TAG}" \
    --jq '.upload_url' | sed 's/{?name,label}//')
ok "Upload URL ready"

substep "Getting auth token..."
GH_TOKEN=$(gh auth token)

echo ""
echo -e "  ${BOLD}Uploading to GitHub...${NC}"
echo -e "  (${DMG_SIZE_HR} — progress shown below)"
echo ""

# curl -# shows a live hash-mark progress bar:
#   ######################################## 100.0%
DOWNLOAD_URL=$(curl -# \
    -X POST \
    -H "Authorization: token $GH_TOKEN" \
    -H "Content-Type: application/octet-stream" \
    --data-binary "@$DMG_PATH" \
    "${UPLOAD_URL}?name=${DMG_NAME}" \
    2>&1 | tee /dev/stderr | tail -1 | python3 -c \
    "import sys,json; d=json.load(sys.stdin); print(d.get('browser_download_url',''))" \
    2>/dev/null || true)

# Simpler fallback: just run upload and let curl print its own progress
if [ -z "$DOWNLOAD_URL" ]; then
    curl --progress-bar \
        -X POST \
        -H "Authorization: token $GH_TOKEN" \
        -H "Content-Type: application/octet-stream" \
        --data-binary "@$DMG_PATH" \
        "${UPLOAD_URL}?name=${DMG_NAME}" \
        -o /tmp/qforge_upload_resp.json
    DOWNLOAD_URL=$(python3 -c \
        "import json; d=json.load(open('/tmp/qforge_upload_resp.json')); print(d.get('browser_download_url',''))" \
        2>/dev/null || true)
fi

echo ""
ok "Upload complete!"
[ -n "$DOWNLOAD_URL" ] && ok "Download URL: $DOWNLOAD_URL"

# ─── Done ───────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${GREEN}║   ✅  QForge ${TAG} released successfully!${NC}${BOLD}${GREEN}            ║${NC}"
echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════════════╝${NC}"
echo -e "  ${BOLD}Release:${NC} https://github.com/${GITHUB_USER}/${GITHUB_REPO}/releases/tag/${TAG}"
echo -e "  ${BOLD}Size:${NC}    ${DMG_SIZE_HR}"
echo -e "  ${BOLD}SHA256:${NC}  ${SHA256}"
echo ""
