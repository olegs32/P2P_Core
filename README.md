# P2P Core

Distributed service orchestration platform with automatic certificate management, service discovery, and real-time monitoring.

## Overview

P2P Core is an enterprise-grade distributed system for managing services across multiple nodes. The platform uses a coordinator-worker topology with automatic SSL certificate provisioning, encrypted configuration storage, and real-time monitoring capabilities.

**Key characteristics:**
- Automatic service discovery and health monitoring
- Built-in Certificate Authority with ACME-like validation
- Encrypted storage for certificates and configurations
- Real-time metrics and log collection via WebSocket
- Multi-homed network support with VPN awareness
- Gossip protocol for cluster coordination

## System Requirements

**Minimum requirements:**
- Python 3.7 or higher
- 2 GB RAM (coordinator), 1 GB RAM (worker)
- Network connectivity between nodes
- Linux, macOS, or Windows

**Required Python packages:**
- FastAPI, uvicorn, httpx
- cryptography (SSL/TLS operations)
- lz4 (gossip compression)
- pyyaml (configuration)
- redis (optional, for distributed caching)

## Installation

### 1. Clone repository

```bash
git clone https://github.com/your-org/P2P_Core.git
cd P2P_Core
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Verify installation

```bash
python p2p.py --help
```

## Configuration

### Coordinator Configuration

Create `config/coordinator.yaml`:

```yaml
node_id: "coordinator-1"
port: 8001
bind_address: "0.0.0.0"
coordinator_mode: true

# SSL/TLS
https_enabled: true
ssl_cert_file: "certs/coordinator_cert.cer"
ssl_key_file: "certs/coordinator_key.key"
ssl_ca_cert_file: "certs/ca_cert.cer"
ssl_ca_key_file: "certs/ca_key.key"

# Gossip protocol
gossip_interval_min: 5
gossip_interval_max: 30
gossip_compression_enabled: true

# Rate limiting
rate_limit_enabled: true
rate_limit_rpc_requests: 100
rate_limit_rpc_burst: 20

# Storage
state_directory: "data/coordinator"
```

### Worker Configuration

Create `config/worker.yaml`:

```yaml
node_id: "worker-1"
port: 8002
bind_address: "0.0.0.0"
coordinator_mode: false

# Coordinator addresses
coordinator_addresses:
  - "192.168.1.100:8001"

# SSL/TLS
https_enabled: true
ssl_cert_file: "certs/worker_cert.cer"
ssl_key_file: "certs/worker_key.key"
ssl_ca_cert_file: "certs/ca_cert.cer"

# Storage
state_directory: "data/worker"
```

## Starting the System

### Coordinator Node

**Default startup (automatic password generation from plot file):**

```bash
python3 p2p.py --config config/coordinator.yaml
```

The system will automatically:
1. Generate a cryptographic plot file at `dist/services/plot`
2. Derive a secure password from the plot (100 characters, 7-layer hashing)
3. Initialize encrypted storage with the generated password
4. Start the coordinator with HTTPS enabled

**Manual password entry:**

```bash
python3 p2p.py --config config/coordinator.yaml --manual-password
```

**Provide password directly:**

```bash
python3 p2p.py --config config/coordinator.yaml --password "your-secure-password"
```

### Worker Nodes

**Start worker with automatic password generation:**

```bash
python3 p2p.py --config config/worker.yaml
```

**Override coordinator address:**

```bash
python3 p2p.py --config config/worker.yaml --coord 192.168.1.100:8001
```

### Certificate Provisioning

Workers automatically request SSL certificates from the coordinator on first start:

1. Worker detects missing certificate
2. Starts temporary HTTP server on port 8802
3. Sends certificate request with challenge to coordinator
4. Coordinator validates challenge via HTTP callback
5. Coordinator generates CA-signed certificate
6. Worker receives and saves certificate to encrypted storage
7. Worker starts HTTPS server on main port

**Certificate auto-renewal triggers:**
- Certificate expires (30 days before expiration)
- IP address changes
- DNS name changes

## Management

### Web Dashboard

Access the monitoring dashboard:

```
https://<coordinator-ip>:8001/dashboard
```

**Dashboard features:**
- Real-time metrics for all nodes (CPU, memory, disk)
- Service status and health checks
- Centralized log viewer with filtering
- Service control (start, stop, restart)
- Historical metrics graphs

### API Endpoints

**Health check:**
```bash
curl https://<node-ip>:<port>/health
```

**Cluster nodes:**
```bash
curl https://<coordinator-ip>:8001/cluster/nodes
```

**Service list:**
```bash
curl https://<node-ip>:<port>/services
```

**Logs (coordinator only):**
```bash
curl "https://<coordinator-ip>:8001/api/logs?node_id=worker-1&level=ERROR&limit=100"
```

### Service Management

**List available services:**
```bash
curl https://<node-ip>:<port>/services
```

**Restart service:**
```bash
curl -X POST https://<coordinator-ip>:8001/api/dashboard/control-service \
  -H "Content-Type: application/json" \
  -d '{
    "worker_id": "worker-1",
    "service_name": "my_service",
    "action": "restart"
  }'
```

## Security

### Password Management

The system uses plot-based password generation for secure storage encryption:

**Password generation process:**
1. Generate random plot file (3,347 bytes) with repeating pattern
2. Extract pattern using cryptographic analysis
3. Apply 7-layer hashing:
   - Layer 1: SHA-512 full file hash
   - Layer 2: SHA3-256 pattern positions
   - Layer 3: SHA-256 block hashing
   - Layer 4: BLAKE2b cascading extraction
   - Layer 5: XOR of file segments
   - Layer 6: SHA-256 frequency analysis
   - Layer 7: SHA-256 metadata hash
4. Final derivation: PBKDF2-HMAC-SHA512 (500,000+ iterations)
5. Generate 100-character password

**Plot file security:**
- Location: `dist/services/plot`
- Automatically added to `.gitignore`
- Each node generates unique plot by default
- For clusters: distribute plot via secure channel (SCP, rsync over SSH)
- Backup plot file - losing it means losing access to encrypted data

**Cluster deployment with shared plot:**

```bash
# On coordinator
python3 -m methods.plot_password  # Generate plot

# Copy to workers
scp dist/services/plot worker1:/path/to/P2P_Core/dist/services/
scp dist/services/plot worker2:/path/to/P2P_Core/dist/services/

# Start nodes (all will derive same password)
python3 p2p.py --config config/coordinator.yaml  # Coordinator
python3 p2p.py --config config/worker.yaml       # Workers
```

### Encrypted Storage

All sensitive data is stored in encrypted archives:

**Encrypted content:**
- SSL certificates and private keys
- Configuration files
- JWT blacklist
- Service state

**Encryption details:**
- Algorithm: AES-256-GCM
- Key derivation: PBKDF2-HMAC-SHA256
- No temporary files on disk (in-memory operations)
- Automatic save on shutdown
- Auto-save every 60 seconds during operation

### SSL/TLS

The system implements mutual TLS (mTLS) with automatic certificate management:

**Certificate hierarchy:**
```
CA Certificate (10 years)
├── Coordinator Certificate (1 year)
└── Worker Certificates (1 year)
```

**CA operations (coordinator only):**
- Generate root CA certificate
- Sign certificate requests from workers
- Validate certificate challenges (ACME-like)

**Certificate storage:**
- All certificates stored in encrypted storage
- Never written to disk in plaintext
- Automatic backup during storage save

### Rate Limiting

Protection against request flooding:

**Default limits:**
- RPC requests: 100 requests/minute
- Burst capacity: 20 requests
- Health checks: 300 requests/minute

**Configuration:**
```yaml
rate_limit_enabled: true
rate_limit_rpc_requests: 100
rate_limit_rpc_burst: 20
```

## Monitoring

### Real-Time Metrics

**Metrics collection:**
- CPU usage percentage
- Memory usage percentage
- Disk usage percentage
- Network traffic
- Service health status

**Update frequency:**
- WebSocket push: Every 4 seconds
- Historical data: Last 100 data points

### Log Collection

**Centralized logging:**
- All logs collected on coordinator
- Real-time delivery (under 100ms)
- Filtering by node, level, logger
- Search functionality
- Max 1,000 entries per node (configurable)

**Log levels:**
- DEBUG: Detailed diagnostics
- INFO: Normal operations
- WARNING: Recoverable issues
- ERROR: Errors requiring attention
- CRITICAL: System failures

**Query logs:**
```bash
# Get all errors from worker-1
curl "https://<coordinator>:8001/api/logs?node_id=worker-1&level=ERROR"

# Get logs from specific logger
curl "https://<coordinator>:8001/api/logs?logger_name=Gossip&limit=50"
```

### Metrics History

Access historical metrics via WebSocket or HTTP API:

**WebSocket connection:**
```javascript
const ws = new WebSocket('wss://<coordinator>:8001/ws/dashboard');
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log('Metrics update:', data.metrics);
  console.log('History:', data.history);
};
```

**HTTP API:**
```bash
curl https://<coordinator>:8001/api/dashboard/metrics
```

## Troubleshooting

### Worker cannot connect to coordinator

**Symptoms:**
- Worker fails to start
- Certificate request timeout
- Connection refused errors

**Solutions:**
1. Verify coordinator is running: `curl https://<coordinator>:8001/health`
2. Check coordinator address in worker config
3. Verify network connectivity: `ping <coordinator-ip>`
4. Check firewall rules allow port 8001 (HTTPS)
5. Verify CA certificate exists on worker

### Certificate validation fails

**Symptoms:**
- SSL handshake errors
- Certificate verification failed
- IP address mismatch

**Solutions:**
1. Check certificate validity: `openssl x509 -in cert.cer -text -noout`
2. Verify CA certificate matches on all nodes
3. For IP changes: restart node (auto-renewal will trigger)
4. Check certificate SAN includes current IP/hostname

### High memory usage on coordinator

**Causes:**
- Too many log entries stored
- Large number of worker nodes
- Frequent WebSocket connections

**Solutions:**
1. Reduce `max_log_entries` in config
2. Clear old logs: `curl -X DELETE https://<coordinator>:8001/api/logs`
3. Reduce metrics history retention
4. Monitor WebSocket connections

### Plot file lost or corrupted

**Recovery:**
1. If plot backed up: restore from backup
2. If no backup: cannot decrypt existing storage
3. Must create new plot and re-encrypt storage
4. For clusters: regenerate plot and redistribute to all nodes

**Prevention:**
```bash
# Backup plot file immediately after generation
cp dist/services/plot dist/services/plot.backup
tar czf plot-backup-$(date +%Y%m%d).tar.gz dist/services/plot
```

### Rate limiting blocking legitimate requests

**Symptoms:**
- 429 Too Many Requests errors
- Service calls failing intermittently

**Solutions:**
1. Increase rate limits in config:
   ```yaml
   rate_limit_rpc_requests: 500
   rate_limit_rpc_burst: 100
   ```
2. Disable rate limiting for debugging:
   ```yaml
   rate_limit_enabled: false
   ```
3. Check for request loops in services

## Architecture

### System Layers

```
┌─────────────────────────────────────────────────────────────┐
│                  P2PApplicationContext                       │
│         (Lifecycle Management & Dependency Injection)        │
└─────────────────────────────────────────────────────────────┘
         │                  │                  │
         ▼                  ▼                  ▼
┌─────────────┐   ┌─────────────┐   ┌─────────────┐
│  Transport  │   │   Network   │   │   Service   │
│   Layer     │◄─►│   Layer     │◄─►│   Layer     │
│  (HTTP/2)   │   │  (Gossip)   │   │ (RPC/REST)  │
└─────────────┘   └─────────────┘   └─────────────┘
         │                  │                  │
         └──────────────────┴──────────────────┘
                           │
                    ┌──────┴──────┐
                    │    Cache    │
                    │ Redis/Memory│
                    └─────────────┘
```

### Component Responsibilities

**Transport Layer:**
- HTTP/2 connection management
- Connection pooling and reuse
- SSL/TLS handshake

**Network Layer:**
- Gossip protocol coordination
- Node discovery and failure detection
- Service metadata propagation

**Service Layer:**
- Service discovery and loading
- RPC method routing
- Metrics collection

**Application Context:**
- Component lifecycle orchestration
- Dependency injection
- Graceful shutdown coordination

### Communication Patterns

**Gossip Protocol:**
- Adaptive interval: 5-30 seconds
- LZ4 compression: 40-60% traffic reduction
- Failure detection: Mark suspected, then dead
- Service propagation: Share service info across cluster

**RPC Communication:**
- Protocol: JSON-RPC 2.0 over HTTPS
- Local routing: Direct method invocation
- Remote routing: HTTP POST to target node
- Automatic node/role resolution

**WebSocket (Dashboard):**
- Protocol: WSS (WebSocket Secure)
- Push model: Server-initiated updates
- Ping/pong: Client sends ping every 4 seconds
- Real-time: Log delivery under 100ms

## Development

### Project Structure

```
P2P_Core/
├── p2p.py                        # Application entry point
├── layers/                       # Core framework
│   ├── application_context.py    # Lifecycle orchestration
│   ├── transport.py              # HTTP/2 transport
│   ├── network.py                # Gossip protocol
│   ├── service.py                # Service management
│   ├── cache.py                  # Multi-level caching
│   ├── ssl_helper.py             # Certificate operations
│   ├── storage_manager.py        # Encrypted storage
│   ├── secure_storage.py         # AES-256-GCM encryption
│   ├── persistence.py            # State persistence
│   ├── rate_limiter.py           # Token bucket limiter
│   └── local_service_bridge.py   # Local/remote proxy
├── methods/                      # Built-in core services
│   ├── system.py                 # System information
│   ├── log_collector.py          # Log collection
│   └── plot_password.py          # Password generation
├── dist/services/                # Pluggable services
│   ├── orchestrator/             # Service orchestration
│   ├── metrics_dashboard/        # Web UI monitoring
│   ├── metrics_reporter/         # Worker metrics
│   ├── update_manager/           # Service updates
│   └── ...
├── config/                       # YAML configurations
├── scripts/                      # Utility scripts
├── docs/                         # Documentation
└── requirements.txt              # Python dependencies
```

### Creating Services

Services are automatically discovered from `dist/services/*/main.py`:

**Minimum service structure:**
```python
from layers.service import BaseService, service_method

class Run(BaseService):
    SERVICE_NAME = "my_service"

    def __init__(self, service_name: str, proxy_client=None):
        super().__init__(service_name, proxy_client)
        self.info.version = "1.0.0"

    async def initialize(self):
        """Called when service starts"""
        self.logger.info("Service initialized")

    @service_method(description="Example method", public=True)
    async def my_method(self, param: str) -> dict:
        return {"result": f"Hello {param}"}
```

**Service directory structure:**
```
dist/services/my_service/
├── main.py              # Required: Run class
├── requirements.txt     # Optional: dependencies
└── manifest.json        # Optional: metadata
```

### Documentation

**Primary references:**
- `CLAUDE.md` - Architecture guide for AI assistants
- `docs/` - Additional documentation
- Code comments - Implementation details

**API documentation:**
- Swagger UI: `https://<node>:<port>/docs`
- ReDoc: `https://<node>:<port>/redoc`
- OpenAPI schema: `https://<node>:<port>/openapi.json`

## Version History

### v2.3.0 - Plot-Based Authentication (2025-12-02)
- Plot-based password generation (7-layer hashing, PBKDF2 500k+ iterations)
- CPU-aware plot generation (multiprocessing, hardware info display)
- Automatic password derivation from cryptographic plot file
- New CLI arguments: --use-plot-auth, --manual-password
- Enhanced security: plot in .gitignore, secure distribution workflow

### v2.2.0 - Real-Time Updates (2025-11-17)
- WebSocket real-time dashboard updates (under 100ms)
- Event-driven log streaming with publish-subscribe
- Secure encrypted storage (AES-256-GCM)
- Multi-homed node support with VPN awareness
- Centralized log collection with filtering

### v2.1.0 - Certificate Automation (2025-11-14)
- ACME-like certificate provisioning
- Automatic certificate renewal
- Challenge-based validation
- Multi-homed network detection

### v2.0.0 - Major Refactoring (2025-11-01)
- YAML configuration support
- Adaptive gossip protocol
- Rate limiting with token bucket
- Certificate Authority infrastructure
- Application context architecture

## License

Internal enterprise use only. All rights reserved.

## Support

For issues and questions:
- GitHub Issues: https://github.com/your-org/P2P_Core/issues
- Documentation: See `docs/` directory
- Architecture guide: `CLAUDE.md`
