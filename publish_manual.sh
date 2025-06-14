#\!/bin/bash

# Build the package
echo "Building package..."
python -m build

# Check the built files
echo "Built files:"
ls -la dist/

# Upload to TestPyPI first (optional)
echo "Upload to TestPyPI? (y/n)"
read -r response
if [[ "$response" == "y" ]]; then
    python -m twine upload --repository testpypi dist/*
fi

# Upload to PyPI
echo "Upload to PyPI? (y/n)"
read -r response
if [[ "$response" == "y" ]]; then
    python -m twine upload dist/*
fi
