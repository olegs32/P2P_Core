#!/bin/bash

# Download script for dashboard static assets
# Run this script in an environment with internet access

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATIC_DIR="$SCRIPT_DIR/static"

# Create directories
mkdir -p "$STATIC_DIR/css"
mkdir -p "$STATIC_DIR/js"

echo "Downloading Bootstrap 5.3.0 CSS..."
curl -L -o "$STATIC_DIR/css/bootstrap.min.css" \
  "https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css"

echo "Downloading Bootstrap 5.3.0 JS..."
curl -L -o "$STATIC_DIR/js/bootstrap.bundle.min.js" \
  "https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"

echo "Downloading Chart.js 4.4.0..."
curl -L -o "$STATIC_DIR/js/chart.umd.min.js" \
  "https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"

echo "Downloading HTMX 1.9.10..."
curl -L -o "$STATIC_DIR/js/htmx.min.js" \
  "https://unpkg.com/htmx.org@1.9.10/dist/htmx.min.js"

# Verify downloads
echo ""
echo "Verifying downloads..."
for file in \
  "$STATIC_DIR/css/bootstrap.min.css" \
  "$STATIC_DIR/js/bootstrap.bundle.min.js" \
  "$STATIC_DIR/js/chart.umd.min.js" \
  "$STATIC_DIR/js/htmx.min.js"
do
  if [ -f "$file" ] && [ $(stat -f%z "$file" 2>/dev/null || stat -c%s "$file" 2>/dev/null) -gt 1000 ]; then
    echo "✓ $(basename $file) downloaded successfully ($(du -h "$file" | cut -f1))"
  else
    echo "✗ $(basename $file) download failed or file too small"
  fi
done

echo ""
echo "Download complete! Static assets are ready for offline use."
