#!/bin/bash
# Quick release script for doc2mark
# Usage: ./release.sh [patch|minor|major]

set -e  # Exit on error

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check if bump type is provided
if [ $# -eq 0 ]; then
    echo "Usage: $0 [patch|minor|major]"
    echo "  patch: 0.1.1 -> 0.1.2"
    echo "  minor: 0.1.1 -> 0.2.0"
    echo "  major: 0.1.1 -> 1.0.0"
    exit 1
fi

BUMP_TYPE=$1

# Check if git is clean
if [[ -n $(git status -s) ]]; then
    echo -e "${RED}Error: Git working directory is not clean${NC}"
    git status -s
    exit 1
fi

# Get current version
CURRENT_VERSION=$(grep -E "__version__\s*=\s*['\"]" doc2mark/__init__.py | sed -E 's/.*['\''"]([0-9]+\.[0-9]+\.[0-9]+)['\''"].*/\1/')
echo -e "Current version: ${YELLOW}$CURRENT_VERSION${NC}"

# Calculate new version
IFS='.' read -ra VERSION_PARTS <<< "$CURRENT_VERSION"
MAJOR=${VERSION_PARTS[0]}
MINOR=${VERSION_PARTS[1]}
PATCH=${VERSION_PARTS[2]}

case $BUMP_TYPE in
    major)
        NEW_VERSION="$((MAJOR + 1)).0.0"
        ;;
    minor)
        NEW_VERSION="${MAJOR}.$((MINOR + 1)).0"
        ;;
    patch)
        NEW_VERSION="${MAJOR}.${MINOR}.$((PATCH + 1))"
        ;;
    *)
        echo -e "${RED}Invalid bump type: $BUMP_TYPE${NC}"
        exit 1
        ;;
esac

echo -e "New version: ${GREEN}$NEW_VERSION${NC}"

# Confirm
read -p "Continue with release? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 1
fi

# Update version in files
echo "Updating version in files..."
sed -i.bak "s/__version__ = \"$CURRENT_VERSION\"/__version__ = \"$NEW_VERSION\"/" doc2mark/__init__.py
sed -i.bak "s/version=\"$CURRENT_VERSION\"/version=\"$NEW_VERSION\"/" setup.py
rm -f doc2mark/__init__.py.bak setup.py.bak

# Commit version change
echo "Committing version change..."
git add doc2mark/__init__.py setup.py
git commit -m "Bump version to $NEW_VERSION"

# Create tag
echo "Creating tag v$NEW_VERSION..."
git tag -a "v$NEW_VERSION" -m "Release v$NEW_VERSION"

# Build package
echo "Building package..."
rm -rf dist/ build/ *.egg-info
python -m build

# Check package
echo "Checking package..."
twine check dist/*

# Push changes
echo "Pushing to GitHub..."
git push
git push --tags

# Upload to PyPI
echo -e "${YELLOW}Ready to upload to PyPI${NC}"
read -p "Upload to PyPI? (y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    twine upload dist/*
    echo -e "${GREEN}✓ Released v$NEW_VERSION successfully!${NC}"
    echo "Install with: pip install doc2mark==$NEW_VERSION"
else
    echo -e "${YELLOW}Skipped PyPI upload. You can upload later with:${NC}"
    echo "  twine upload dist/*"
fi