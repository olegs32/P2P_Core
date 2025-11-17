# Dashboard Static Assets

This directory contains static assets (CSS, JavaScript) for the P2P Cluster Dashboard.

## Purpose

These files enable the dashboard to work offline or in air-gapped environments without requiring external CDN access.

## Required Files

The dashboard requires the following files:

```
static/
├── css/
│   └── bootstrap.min.css          # Bootstrap 5.3.0 CSS
└── js/
    ├── bootstrap.bundle.min.js    # Bootstrap 5.3.0 JS (includes Popper)
    ├── chart.umd.min.js           # Chart.js 4.4.0
    └── htmx.min.js                # HTMX 1.9.10
```

## Download Instructions

### Automated Download (Recommended)

Run the download script from the metrics_dashboard directory:

```bash
cd dist/services/metrics_dashboard
./download_static_assets.sh
```

This script will:
1. Create the directory structure
2. Download all required files from their respective CDNs
3. Verify the downloads

### Manual Download

If the script doesn't work (e.g., network restrictions), download the files manually:

1. **Bootstrap 5.3.0 CSS**
   ```bash
   curl -L -o static/css/bootstrap.min.css \
     "https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css"
   ```

2. **Bootstrap 5.3.0 JS**
   ```bash
   curl -L -o static/js/bootstrap.bundle.min.js \
     "https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"
   ```

3. **Chart.js 4.4.0**
   ```bash
   curl -L -o static/js/chart.umd.min.js \
     "https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"
   ```

4. **HTMX 1.9.10**
   ```bash
   curl -L -o static/js/htmx.min.js \
     "https://unpkg.com/htmx.org@1.9.10/dist/htmx.min.js"
   ```

### Using wget

If curl is not available, use wget:

```bash
cd static/css
wget -O bootstrap.min.css "https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css"

cd ../js
wget -O bootstrap.bundle.min.js "https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"
wget -O chart.umd.min.js "https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"
wget -O htmx.min.js "https://unpkg.com/htmx.org@1.9.10/dist/htmx.min.js"
```

## CDN Fallback

The dashboard.html template includes automatic CDN fallback:
- If local files are not found, the dashboard will automatically load from CDN
- This ensures the dashboard works even without local assets
- For air-gapped deployments, ensure all files are downloaded before deployment

## Verification

After downloading, verify the files exist and have reasonable sizes:

```bash
ls -lh static/css/
ls -lh static/js/
```

Expected approximate sizes:
- bootstrap.min.css: ~190 KB
- bootstrap.bundle.min.js: ~200 KB
- chart.umd.min.js: ~250 KB
- htmx.min.js: ~40 KB

## Troubleshooting

### Files not loading

1. Check that files exist in the correct directories
2. Verify file permissions (should be readable by the web server)
3. Check FastAPI logs for static file mounting errors

### Download failures

If downloads fail due to network restrictions:
1. Download files on a machine with internet access
2. Transfer files to the coordinator using scp, rsync, or USB
3. Place files in the correct directories

### Permission errors

```bash
chmod -R 644 static/css/*
chmod -R 644 static/js/*
```

## Air-Gapped Deployment

For completely offline/air-gapped environments:

1. Download all static assets on an internet-connected machine
2. Create a tarball:
   ```bash
   cd dist/services/metrics_dashboard
   tar czf dashboard-static-assets.tar.gz static/
   ```
3. Transfer the tarball to the coordinator
4. Extract on the coordinator:
   ```bash
   cd dist/services/metrics_dashboard
   tar xzf dashboard-static-assets.tar.gz
   ```
5. Restart the coordinator to mount static files

## Updating Assets

To update to newer versions:

1. Edit `download_static_assets.sh` with new version numbers
2. Run the download script
3. Test the dashboard
4. If issues occur, revert to previous versions

## Security Considerations

- **Integrity**: Verify file checksums if security is critical
- **Updates**: Keep libraries updated for security patches
- **Source**: Only download from official CDNs (jsdelivr.net, unpkg.com)
- **HTTPS**: Always use HTTPS URLs when downloading

## License

These third-party libraries have their own licenses:
- **Bootstrap**: MIT License
- **Chart.js**: MIT License
- **HTMX**: BSD 2-Clause License

Refer to each library's documentation for full license details.
