# Dashboard Static Assets

This directory contains static assets (CSS, JavaScript) for the P2P Cluster Dashboard.

## Included Files

All required static assets are bundled with the project:

```
static/
├── css/
│   └── bootstrap.min.css          # Bootstrap 5.3.0 CSS (228 KB)
└── js/
    ├── bootstrap.bundle.min.js    # Bootstrap 5.3.0 JS (79 KB)
    ├── chart.umd.min.js           # Chart.js 4.4.0 (201 KB)
    └── htmx.min.js                # HTMX 1.9.10 (47 KB)
```

## Purpose

These files enable the dashboard to work completely offline without requiring external CDN access.

## Benefits

- ✅ **Offline Operation**: Dashboard works without internet connection
- ✅ **Air-Gapped Deployments**: Suitable for isolated/secure environments
- ✅ **No External Dependencies**: All resources served locally
- ✅ **Fast Loading**: No CDN latency
- ✅ **Privacy**: No external requests that could leak information

## Versions

The following library versions are included:

| Library | Version | License |
|---------|---------|---------|
| Bootstrap | 5.3.0 | MIT |
| Chart.js | 4.4.0 | MIT |
| HTMX | 1.9.10 | BSD 2-Clause |

## Updating

To update to newer versions:

1. Install via npm:
   ```bash
   cd /tmp
   npm install bootstrap@<version> chart.js@<version> htmx.org@<version>
   ```

2. Copy files:
   ```bash
   cp /tmp/node_modules/bootstrap/dist/css/bootstrap.min.css static/css/
   cp /tmp/node_modules/bootstrap/dist/js/bootstrap.bundle.min.js static/js/
   cp /tmp/node_modules/chart.js/dist/chart.umd.js static/js/chart.umd.min.js
   cp /tmp/node_modules/htmx.org/dist/htmx.min.js static/js/
   ```

3. Test the dashboard
4. Commit the updated files

## Security

- **Source**: All files installed from official npm packages
- **Integrity**: Files are version-pinned for consistency
- **No CDN**: Eliminates third-party CDN security risks
- **Updates**: Keep libraries updated for security patches

## License

These third-party libraries have their own licenses:
- **Bootstrap**: MIT License (https://github.com/twbs/bootstrap/blob/main/LICENSE)
- **Chart.js**: MIT License (https://github.com/chartjs/Chart.js/blob/master/LICENSE.md)
- **HTMX**: BSD 2-Clause License (https://github.com/bigskysoftware/htmx/blob/master/LICENSE)

Refer to each library's documentation for full license details.
