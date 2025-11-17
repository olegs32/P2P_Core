# Dashboard Static Assets

This directory contains static assets (CSS, JavaScript) for the P2P Cluster Dashboard.

## Included Files

All required static assets are bundled with the project:

```
static/
├── css/
│   ├── bootstrap.min.css          # Bootstrap 5.3.0 CSS (228 KB)
│   └── bootstrap-icons.min.css    # Bootstrap Icons 1.11.0 CSS (79 KB)
├── fonts/
│   ├── bootstrap-icons.woff2      # Bootstrap Icons fonts (128 KB)
│   └── bootstrap-icons.woff       # Bootstrap Icons fonts (173 KB)
└── js/
    ├── bootstrap.bundle.min.js    # Bootstrap 5.3.0 JS (79 KB)
    ├── chart.umd.min.js           # Chart.js 4.4.0 (201 KB)
    ├── htmx.min.js                # HTMX 1.9.10 (47 KB)
    └── ace/                       # Ace Editor 1.32.2 (248 files, ~2.8 MB)
        ├── ace.js                 # Core editor
        ├── ext-language_tools.js  # Language tools extension
        ├── mode-*.js              # Syntax highlighting modes
        ├── theme-*.js             # Editor themes
        └── worker-*.js            # Background workers
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
| Bootstrap Icons | 1.11.0 | MIT |
| Chart.js | 4.4.0 | MIT |
| HTMX | 1.9.10 | BSD 2-Clause |
| Ace Editor | 1.32.2 | BSD 3-Clause |

## Updating

To update to newer versions:

1. Install via npm:
   ```bash
   cd /tmp
   npm install bootstrap@<version> bootstrap-icons@<version> chart.js@<version> htmx.org@<version> ace-builds@<version>
   ```

2. Copy files:
   ```bash
   # Bootstrap CSS and JS
   cp /tmp/node_modules/bootstrap/dist/css/bootstrap.min.css static/css/
   cp /tmp/node_modules/bootstrap/dist/js/bootstrap.bundle.min.js static/js/

   # Bootstrap Icons
   cp /tmp/node_modules/bootstrap-icons/font/bootstrap-icons.min.css static/css/
   mkdir -p static/fonts
   cp /tmp/node_modules/bootstrap-icons/font/fonts/bootstrap-icons.woff* static/fonts/

   # Chart.js
   cp /tmp/node_modules/chart.js/dist/chart.umd.js static/js/chart.umd.min.js

   # HTMX
   cp /tmp/node_modules/htmx.org/dist/htmx.min.js static/js/

   # Ace Editor (248 files)
   mkdir -p static/js/ace
   cp -r /tmp/node_modules/ace-builds/src-min-noconflict/* static/js/ace/
   ```

   Note: After copying bootstrap-icons.min.css, fix the font paths:
   ```bash
   sed -i 's|url("fonts/bootstrap-icons|url("../fonts/bootstrap-icons|g' static/css/bootstrap-icons.min.css
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
- **Bootstrap Icons**: MIT License (https://github.com/twbs/icons/blob/main/LICENSE.md)
- **Chart.js**: MIT License (https://github.com/chartjs/Chart.js/blob/master/LICENSE.md)
- **HTMX**: BSD 2-Clause License (https://github.com/bigskysoftware/htmx/blob/master/LICENSE)
- **Ace Editor**: BSD 3-Clause License (https://github.com/ajaxorg/ace/blob/master/LICENSE)

Refer to each library's documentation for full license details.
