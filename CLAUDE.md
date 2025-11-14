# P2P Core - Architecture Guide for AI Assistants

This document provides a comprehensive overview of the P2P_Core codebase architecture, design patterns, and conventions that AI assistants should follow when working with this project.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Design Patterns](#design-patterns)
3. [Key Abstractions](#key-abstractions)
4. [Service Discovery](#service-discovery)
5. [Communication Patterns](#communication-patterns)
6. [Security Model](#security-model)
7. [Configuration Management](#configuration-management)
8. [Testing Approach](#testing-approach)
9. [Development Conventions](#development-conventions)
10. [Common Patterns](#common-patterns)

---

## Architecture Overview

### High-Level Architecture

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

### Layered Architecture

The system follows a strict layered architecture with clear separation of concerns:

1. **Transport Layer** (`layers/transport.py`): HTTP/2 connection management, pooling
2. **Network Layer** (`layers/network.py`): Gossip protocol, node discovery, failure detection
3. **Service Layer** (`layers/service.py`): Service management, RPC handling, metrics
4. **Application Context** (`layers/application_context.py`): Lifecycle orchestration

### Component-Based Design

All major system components inherit from `P2PComponent` base class:

- **TransportComponent**: HTTP/HTTPS transport with connection pooling
- **CacheComponent**: Multi-level caching (L1: in-memory, L2: Redis)
- **NetworkComponent**: Gossip protocol and node registry
- **ServiceComponent**: Service discovery, loading, and management
- **WebServerComponent**: FastAPI/Uvicorn web server with SSL

---

## Design Patterns

### 1. Dependency Injection via ApplicationContext

**CRITICAL**: The `P2PApplicationContext` is the **single source of truth** for all dependencies.

```python
# ✅ CORRECT: Access via context
context = P2PApplicationContext.get_current_context()
network = context.get_shared("network")
cache = context.get_shared("cache")

# ❌ WRONG: Global variables
global network_layer  # Never use global state
```

**Key principle**: All components receive `context` in their constructor and access dependencies through it.

### 2. Plugin Architecture for Services

Services are automatically discovered and loaded from `dist/services/*/main.py`:

```python
# Service structure
dist/services/my_service/
├── main.py          # Must contain 'Run' class inheriting from BaseService
├── requirements.txt # Optional dependencies
└── manifest.json    # Optional metadata
```

### 3. Component Lifecycle Management

Components follow a strict initialization and shutdown order:

```python
# Initialization order (defined in application_context.py)
1. Transport  (no dependencies)
2. Cache      (no dependencies)  
3. Network    (depends on Transport)
4. Service    (depends on Network + Cache)
5. WebServer  (depends on Service)

# Shutdown order is reversed
```

### 4. Factory Pattern for Component Creation

Components are created through the context:

```python
# Component registration
transport_component = TransportComponent(context)
context.register_component(transport_component)

# Automatic initialization with dependency resolution
await context.initialize_all()
```

### 5. Observer Pattern for Gossip

The gossip protocol uses observer pattern for node state changes:

```python
# Listeners are notified when nodes join/leave/fail
gossip.add_listener(lambda node_id, status, node_info: ...)
```

### 6. Proxy Pattern for Service Calls

Services are called through a transparent proxy that handles local vs remote routing:

```python
# Local call (direct method invocation)
result = await proxy.system.get_system_info()

# Remote call to specific node
result = await proxy.system.worker_node_123.get_system_info()

# Remote call by role
result = await proxy.system.coordinator.get_system_info()
```

---

## Key Abstractions

### P2PApplicationContext

**Location**: `layers/application_context.py`

The central orchestrator of the entire system. **Never bypass the context**.

**Responsibilities**:
- Component registration and lifecycle management
- Method registry (RPC methods)
- Shared state management
- Graceful shutdown coordination
- Signal handling (SIGINT, SIGTERM)

**Critical Methods**:
```python
# Component management
context.register_component(component)
context.get_component(name)
context.require_component(name)  # Throws if missing

# Method registry (SINGLE SOURCE OF TRUTH)
context.register_method(path, method)
context.get_method(path)
context._method_registry  # Direct access to registry dict

# Shared state
context.set_shared(key, value)
context.get_shared(key, default)

# Lifecycle
await context.initialize_all()
await context.shutdown_all()
await context.wait_for_shutdown()
```

### P2PComponent

**Location**: `layers/application_context.py`

Base class for all system components with built-in lifecycle management.

**Key Features**:
- Automatic state tracking (NOT_INITIALIZED, INITIALIZING, RUNNING, STOPPING, STOPPED, ERROR)
- Dependency declaration
- Metrics collection (start_time, error_count, etc.)
- Graceful shutdown support

**Usage Pattern**:
```python
class MyComponent(P2PComponent):
    def __init__(self, context: P2PApplicationContext):
        super().__init__("my_component", context)
        self.add_dependency("transport")  # Declare dependencies
        
    async def _do_initialize(self):
        # Component-specific initialization
        transport = self.context.get_shared("transport")
        self.my_service = MyService(transport)
        
    async def _do_shutdown(self):
        # Component-specific cleanup
        await self.my_service.close()
```

### BaseService

**Location**: `layers/service.py`

Base class for all services with automatic metrics, lifecycle hooks, and RPC support.

**Key Features**:
- Automatic method discovery via `@service_method` decorator
- Built-in metrics (counters, gauges, timers)
- Health reporting
- Service info exposure
- Proxy client injection for cross-service calls

**Service Template**:
```python
from layers.service import BaseService, service_method

class Run(BaseService):
    SERVICE_NAME = "my_service"
    
    def __init__(self, service_name: str, proxy_client=None):
        super().__init__(service_name, proxy_client)
        self.info.version = "1.0.0"
        self.info.description = "My custom service"
        
    async def initialize(self):
        """Called when service starts"""
        self.logger.info("Service initialized")
        
    async def cleanup(self):
        """Called when service stops"""
        self.logger.info("Service cleanup")
        
    @service_method(description="Example method", public=True)
    async def my_method(self, param: str) -> dict:
        # Call another service
        system_info = await self.proxy.system.get_system_info()
        
        # Metrics are automatic
        return {
            "result": f"Hello {param}",
            "system": system_info
        }
```

### ServiceManager

**Location**: `layers/service.py`

Manages the lifecycle of all services in the system.

**Responsibilities**:
- Service discovery and loading
- Service initialization and shutdown
- Metrics collection via `ReactiveMetricsCollector`
- Proxy client injection
- Service health monitoring

**Critical: Never modify services directly**. Always go through ServiceManager.

### NodeInfo

**Location**: `layers/network.py`

Represents a node in the P2P network.

**Key Fields**:
```python
@dataclass
class NodeInfo:
    node_id: str
    address: str
    port: int
    role: str  # "coordinator" or "worker"
    capabilities: List[str]
    last_seen: datetime
    metadata: Dict[str, Any]
    status: str  # "alive", "suspected", "dead"
    services: Dict[str, Dict[str, Any]]  # Service info from gossip
```

---

## Service Discovery

### Automatic Service Discovery

Services are automatically discovered and loaded from the `dist/services/` directory:

1. **ServiceLoader** scans `dist/services/` for subdirectories
2. Each subdirectory must contain `main.py` with a `Run` class
3. `Run` class must inherit from `BaseService`
4. Services are loaded, initialized, and their methods registered in the method registry

### Service Registration Flow

```
1. ServiceLoader.discover_and_load_services()
   ↓
2. ServiceLoader.load_service_from_directory()
   ↓
3. ServiceManager.load_service()
   ↓
4. ServiceManager.initialize_service()
   ↓
5. ServiceManager._register_service_methods()
   ↓
6. Methods registered in context._method_registry
```

### Method Registry

**CRITICAL**: The method registry has a **single source of truth**:

```python
# ✅ CORRECT: Access through context
registry = context._method_registry  # Dict[str, callable]

# ✅ CORRECT: Also via helper
from layers.service import get_method_registry
registry = get_method_registry()  # Returns context._method_registry

# ❌ WRONG: Never create separate registries
my_registry = {}  # This breaks everything
```

**Method path format**: `{service_name}/{method_name}`

Example: `"system/get_system_info"`

### Built-in Services vs Pluggable Services

**IMPORTANT DISTINCTION**: The system has two types of services:

#### 1. Built-in Core Services (`methods/`)

**Location**: `methods/` directory

**Characteristics**:
- Loaded at system startup (not via plugin discovery)
- Part of the core framework
- Cannot be stopped or reloaded
- Available on all nodes

**Services**:
- **system** (`methods/system.py`): System information, health checks, node management
- **log_collector** (`methods/log_collector.py`): Centralized log collection (coordinator only)

**Usage**:
```python
# Built-in services are always available
info = await proxy.system.get_system_info()
logs = await proxy.log_collector.get_logs(node_id="worker-1")
```

#### 2. Pluggable Services (`dist/services/`)

**Location**: `dist/services/` directory

**Characteristics**:
- Discovered and loaded automatically via ServiceLoader
- Can be added/removed/updated without system restart
- Each service in its own subdirectory
- Optional per deployment (can be enabled/disabled)

**Available Services**:

| Service | Description | Node Type |
|---------|-------------|-----------|
| **orchestrator** | Service orchestration, deployment, and management | Coordinator |
| **metrics_dashboard** | Web UI for monitoring cluster metrics and logs | Coordinator |
| **metrics_reporter** | Collect and report node metrics to coordinator | Worker |
| **update_manager** | Manage service updates on workers | Worker |
| **update_server** | Distribute service updates from coordinator | Coordinator |
| **certs_tool** | Legacy Windows CSP certificate management | Any |
| **test_monitoring** | Testing and monitoring utilities | Any |
| **test_metric_service** | Metrics testing and validation | Any |

**Service Structure**:
```
dist/services/my_service/
├── main.py              # Required: Run(BaseService) class
├── requirements.txt     # Optional: pip dependencies
├── manifest.json        # Optional: metadata
├── templates/           # Optional: web templates (for dashboard services)
└── static/              # Optional: static assets (CSS, JS)
```

**Key Differences**:

| Aspect | Built-in Services | Pluggable Services |
|--------|-------------------|-------------------|
| Location | `methods/` | `dist/services/` |
| Loading | At startup | Auto-discovery |
| Lifecycle | Always running | Can start/stop |
| Updates | Requires system restart | Hot-reload possible |
| Purpose | Core functionality | Extended features |

**When to Use Each**:

- **Built-in service**: Core system functionality that all nodes need (e.g., health checks, logging)
- **Pluggable service**: Optional features, extensions, or domain-specific logic (e.g., monitoring, deployment)

---

## Communication Patterns

### Gossip Protocol

**Location**: `layers/network.py` (`SimpleGossipProtocol` class)

**Features**:
- **Adaptive interval**: Adjusts between 5-30 seconds based on cluster load
- **LZ4 compression**: Reduces gossip traffic by 40-60%
- **Failure detection**: Marks nodes as "suspected" then "dead" if no heartbeat
- **Service propagation**: Shares service information across the cluster

**Key Configuration**:
```python
gossip_interval_min: 5      # seconds
gossip_interval_max: 30     # seconds
compression_enabled: true   # LZ4 compression
compression_threshold: 1024 # bytes
max_gossip_targets: 5       # nodes to gossip with per round
```

### Multi-Homed Node Support

**NEW FEATURE**: Automatic detection and handling of nodes with multiple network interfaces.

**Location**: `layers/network.py` (`SimpleGossipProtocol`)

**Features**:
- **Address probing**: Tests connectivity to each detected interface
- **Smart interface selection**: Chooses the most appropriate IP based on reachability
- **VPN-aware**: Detects VPN interfaces and prioritizes them when appropriate
- **Subnet awareness**: Prefers IPs in the same subnet as the coordinator

**How it works**:
1. Node starts and detects all local IP addresses
2. For each IP, attempts to bind and test connectivity
3. Gossip protocol probes each candidate address
4. Updates node info with the most reachable address
5. Continuously monitors and updates if network topology changes

**Configuration**:
```python
# In P2PConfig
enable_address_probing: bool = True  # Default: enabled
probe_timeout_seconds: float = 2.0   # Timeout for each probe
```

**Example scenario**:
```
Node has interfaces:
- 192.168.1.100 (LAN)
- 10.8.0.5 (VPN)
- 127.0.0.1 (loopback)

If coordinator is at 10.8.0.1 (VPN network):
→ System selects 10.8.0.5 (same subnet)

If coordinator is at 192.168.1.50 (LAN):
→ System selects 192.168.1.100 (same subnet)
```

**Benefits**:
- Automatic failover between interfaces
- No manual IP configuration needed
- Works across complex network topologies
- Handles VPN connections transparently

### VPN and Complex Network Support

**Features for enterprise environments**:

1. **Smart IP Detection**: Automatically detects the best IP for cluster communication
   - Filters out loopback, link-local, and APIPA addresses
   - Prefers non-VPN IPs when coordinator is on LAN
   - Prefers VPN IPs when coordinator is on VPN

2. **Subnet-Aware Routing**: Calculates network proximity
   ```python
   # Automatically detects if IPs are in same subnet
   coordinator: 10.8.0.1/24
   worker: 10.8.0.50/24  ✓ Same subnet, high priority
   worker: 192.168.1.10/24  ✗ Different subnet, lower priority
   ```

3. **Gossip Fallback Mechanisms**: Multiple strategies for node discovery
   - Direct address from configuration
   - Cached gossip state (persisted to disk)
   - Service info from last known state
   - Address probing on multiple interfaces

**Troubleshooting multi-homed nodes**:

```python
# Check detected addresses
info = await proxy.system.get_system_info()
print(info['network']['interfaces'])

# Force specific interface via config
bind_address: "10.8.0.5"  # Bind to specific IP

# Check gossip node registry
nodes = await proxy.system.get_cluster_nodes()
for node in nodes:
    print(f"{node['node_id']}: {node['address']}:{node['port']}")
```

### RPC Communication

**Protocol**: JSON-RPC 2.0 over HTTPS

**Request Format**:
```json
{
  "method": "service_name/method_name",
  "params": {"key": "value"},
  "id": "unique-request-id"
}
```

**Response Format**:
```json
{
  "result": {...},
  "error": null,
  "id": "unique-request-id"
}
```

### Local vs Remote Call Routing

The proxy automatically determines whether to call locally or remotely:

```python
# Local call (method exists in local registry)
await proxy.system.get_system_info()
→ Direct call via method_registry

# Remote call by node ID
await proxy.system.worker_123.get_system_info()
→ HTTP POST to https://worker_123:port/rpc

# Remote call by role
await proxy.metrics_dashboard.coordinator.report_metrics(...)
→ Resolves "coordinator" in node_registry
→ HTTP POST to coordinator URL
```

**How it works** (`layers/local_service_bridge.py`):
1. `ServiceMethodProxy.__getattr__` checks if attribute is a node/role or method
2. If node/role: returns new `ServiceMethodProxy` with `target_node` set
3. If method: returns `MethodCaller`
4. `MethodCaller.__call__` decides local vs remote based on `target_node`

### Connection Pooling

**Location**: `layers/network.py` (`ConnectionManager` class)

**Features**:
- HTTP/2 support
- Connection reuse with keepalive
- Per-node client pooling
- Automatic SSL context management

```python
# Connection manager is created with SSL settings
connection_manager = ConnectionManager(
    max_connections=100,
    max_keepalive=20,
    ssl_verify=True,
    ca_cert_file="certs/ca_cert.cer",
    context=context  # For secure storage access
)
```

---

## Log Collection System

### Overview

**NEW FEATURE**: Centralized log collection infrastructure for gathering logs from all nodes in the cluster.

**Location**: `methods/log_collector.py`

The log collection system provides syslog-like functionality built into the P2P framework:
- Centralized log storage on coordinator
- Real-time log streaming from workers
- Filtering by node, level, and logger name
- Integration with metrics dashboard web UI

### Architecture

```
Worker Nodes                    Coordinator
     │                               │
     ├─► P2PLogHandler              │
     │   (captures logs)             │
     │                               │
     ├─► Buffers in memory          │
     │   (deque, max 1000)           │
     │                               │
     ├─► Periodic flush ────────────►│ LogCollector
     │   (via RPC)                   │ (stores & indexes)
     │                               │
     │                               ├─► Query API
     │                               │   (filter, paginate)
     │                               │
     │                               └─► Metrics Dashboard
                                         (web UI for logs)
```

### LogCollector Service

**Built-in service** running on coordinator that collects and stores logs from all nodes.

**Key Features**:
- Per-node log storage (deque with configurable max size)
- Multi-level filtering (node_id, level, logger_name)
- Pagination support (limit/offset)
- Statistics and log sources tracking
- Thread-safe operations

**Configuration**:
```python
# In P2PConfig
max_log_entries: int = 1000  # Max logs per node
```

**RPC Methods**:
```python
# Add logs from worker
await proxy.log_collector.add_logs(
    node_id="worker-1",
    logs=[{
        "timestamp": "2025-11-14T10:30:00",
        "level": "INFO",
        "logger_name": "Service.system",
        "message": "Service started",
        "module": "system",
        "funcName": "initialize",
        "lineno": 42
    }]
)

# Get logs with filtering
logs = await proxy.log_collector.get_logs(
    node_id="worker-1",        # Optional: filter by node
    level="ERROR",              # Optional: filter by level
    logger_name="Gossip",       # Optional: filter by logger
    limit=100,                  # Max results
    offset=0                    # Pagination offset
)

# Get available log sources
sources = await proxy.log_collector.get_log_sources()
# Returns: {"nodes": [...], "loggers": [...], "log_levels": [...]}

# Clear logs
await proxy.log_collector.clear_logs(node_id="worker-1")  # or None for all

# Get statistics
stats = await proxy.log_collector.get_stats()
```

### P2PLogHandler

**Custom logging handler** that captures logs on worker nodes for transmission to coordinator.

**Usage in services**:
```python
import logging
from methods.log_collector import P2PLogHandler

# In service initialization
log_handler = P2PLogHandler(node_id=self.context.config.node_id)
log_handler.setLevel(logging.INFO)

# Attach to root logger or service logger
logging.getLogger().addHandler(log_handler)

# Later, flush logs to coordinator
new_logs = log_handler.get_new_logs()
if new_logs:
    await self.proxy.log_collector.add_logs(
        node_id=self.context.config.node_id,
        logs=new_logs
    )
```

**How it works**:
1. Handler captures all log records via `emit()`
2. Stores in memory buffer (deque, max 1000 entries)
3. `get_new_logs()` retrieves buffered logs
4. Logs are cleared from buffer after retrieval (prevents duplicates)
5. Worker services periodically flush to coordinator

### Log Entry Structure

```python
@dataclass
class LogEntry:
    timestamp: str        # ISO format datetime
    node_id: str          # Node identifier
    level: str            # DEBUG, INFO, WARNING, ERROR, CRITICAL
    logger_name: str      # e.g., "Service.system", "Gossip"
    message: str          # Log message
    module: str           # Python module name
    funcName: str         # Function name
    lineno: int           # Line number
```

### Web UI Integration

The metrics dashboard service (`dist/services/metrics_dashboard/`) includes a web-based log viewer:

**Access**: `https://coordinator:8001/dashboard` → Logs tab

**Features**:
- Real-time log updates
- Filter by node, level, logger
- Search functionality
- Color-coded log levels
- Timestamp display
- Auto-refresh

**API Endpoints** (in metrics_dashboard):
```bash
# Get logs via HTTP
GET /api/logs?node_id=worker-1&level=ERROR&limit=100

# Get log sources
GET /api/logs/sources

# Clear logs
DELETE /api/logs?node_id=worker-1
```

### Best Practices

**For Service Developers**:
1. Use standard Python logging, not print statements
2. Set appropriate log levels (DEBUG for diagnostics, INFO for events, ERROR for problems)
3. Include context in messages (node_id, operation, etc.)
4. Avoid logging sensitive data (passwords, tokens)

**For Operators**:
1. Monitor ERROR and CRITICAL logs regularly
2. Set `max_log_entries` based on cluster size and log volume
3. Use filtering to focus on specific issues
4. Clear old logs periodically to free memory

**Example: Service with logging**:
```python
class Run(BaseService):
    SERVICE_NAME = "my_service"

    async def initialize(self):
        # Standard logging - automatically captured by P2PLogHandler
        self.logger.info(f"Initializing {self.service_name}")

    @service_method(public=True)
    async def process_data(self, data: dict) -> dict:
        try:
            # Log important operations
            self.logger.debug(f"Processing data: {len(data)} items")
            result = await self._do_work(data)
            self.logger.info("Data processed successfully")
            return result
        except Exception as e:
            # Log errors with context
            self.logger.error(f"Failed to process data: {e}", exc_info=True)
            raise
```

---

## Security Model

### JWT Authentication

**Location**: `layers/service.py` (`JWTBlacklist`, `P2PAuthBearer`)

**Flow**:
1. Client requests token: `POST /auth/token` with `{"node_id": "..."}`
2. Server returns JWT token with expiration
3. Client includes token in `Authorization: Bearer <token>` header
4. Token can be revoked via `POST /auth/revoke`
5. Revoked tokens are stored in blacklist with persistence

**Configuration**:
```python
JWT_SECRET_KEY: str = "change-this-in-production"
JWT_EXPIRATION_HOURS: int = 24
jwt_blacklist_file: str = "jwt_blacklist.json"
```

### Mutual TLS (mTLS)

**Features**:
- Certificate Authority (CA) on coordinator node
- Automatic certificate generation and renewal
- Challenge-based validation (ACME-like)
- Certificate stored in secure encrypted storage

**Certificate Hierarchy**:
```
CA Certificate (10 years)
├── Coordinator Certificate (1 year)
└── Worker Certificates (1 year)
    - Auto-renewed when:
      * Certificate expires (30 days before)
      * IP address changes
      * DNS name changes
```

### ACME-like Certificate Issuance

**Location**: `layers/ssl_helper.py`, `layers/application_context.py` (WebServerComponent)

**Flow for worker nodes**:
1. Worker detects missing/expired certificate
2. Worker starts temporary HTTP server on port 8802
3. Worker generates random challenge
4. Worker sends certificate request to coordinator with challenge
5. Coordinator validates challenge by calling worker's `/internal/cert-challenge/{challenge}`
6. Coordinator generates CA-signed certificate
7. Coordinator returns certificate and private key
8. Worker saves to secure storage
9. Worker shuts down temporary server
10. Worker starts HTTPS server on main port

**Coordinator Endpoints**:
- `POST /internal/cert-request`: Worker requests certificate
- `GET /internal/ca-cert`: Worker downloads CA certificate

**Worker Endpoints**:
- `GET /internal/cert-challenge/{challenge}`: Challenge validation

### Secure Storage

**Location**: `layers/storage_manager.py`, `layers/secure_storage.py`

All sensitive data (certificates, keys, configs) is stored in an **encrypted archive**:

**Features**:
- AES-256-GCM encryption
- Password-based key derivation (PBKDF2-HMAC-SHA256)
- In-memory file operations (no temp files on disk)
- Automatic save on context shutdown

**CRITICAL RULE**: Never write certificates or keys to disk directly. Always use `storage_manager`:

```python
# ✅ CORRECT: Via storage manager
storage = context.get_shared("storage_manager")
cert_data = storage.read_cert("ca_cert.cer")
storage.write_cert("node_cert.cer", cert_bytes)

# ❌ WRONG: Direct file operations
with open("certs/ca_cert.cer", "rb") as f:  # NEVER DO THIS
    cert_data = f.read()
```

### Rate Limiting

**Location**: `layers/rate_limiter.py`, middleware in `layers/service.py`

**Algorithm**: Token Bucket

**Configuration**:
```python
rate_limit_enabled: bool = true
rate_limit_rpc_requests: int = 100    # requests/minute
rate_limit_rpc_burst: int = 20        # burst size
rate_limit_health_requests: int = 300
rate_limit_health_burst: int = 50
```

**Middleware**: Automatically applied to all endpoints when enabled in config.

---

## Configuration Management

### P2PConfig Dataclass

**Location**: `layers/application_context.py`

All configuration is centralized in the `P2PConfig` dataclass:

```python
@dataclass
class P2PConfig:
    node_id: str
    port: int
    bind_address: str = "0.0.0.0"
    coordinator_mode: bool = False
    
    # Redis
    redis_url: str = "redis://localhost:6379"
    redis_enabled: bool = True
    
    # Gossip
    gossip_interval_min: int = 5
    gossip_interval_max: int = 30
    gossip_compression_enabled: bool = True
    
    # SSL/TLS
    https_enabled: bool = True
    ssl_cert_file: str = "certs/node_cert.cer"
    ssl_key_file: str = "certs/node_key.key"
    ssl_ca_cert_file: str = "certs/ca_cert.cer"
    ssl_verify: bool = True
    
    # Rate Limiting
    rate_limit_enabled: bool = True
    rate_limit_rpc_requests: int = 100
    
    # Persistence
    state_directory: str = "data"
    jwt_blacklist_file: str = "jwt_blacklist.json"
    gossip_state_file: str = "gossip_state.json"
```

### Configuration Loading

**CRITICAL**: Configurations are stored in **secure encrypted storage**, not plain YAML files on disk.

```python
# ✅ CORRECT: Load from secure storage
config = P2PConfig.from_yaml("coordinator.yaml", context=context)

# This internally:
# 1. Gets storage_manager from context
# 2. Reads encrypted config from storage
# 3. Parses YAML and creates P2PConfig instance
# 4. If not found, creates default and saves to storage
```

### Default Configuration

Auto-generated defaults for coordinator vs worker:

```python
# Create default config
config = P2PConfig.create_default(
    node_id="coordinator-1",
    coordinator_mode=True
)

# Coordinator defaults:
# - port: 8001
# - ssl_cert_file: certs/coordinator_cert.cer
# - state_directory: data/coordinator

# Worker defaults:
# - port: 8002
# - ssl_cert_file: certs/worker_cert.cer
# - state_directory: data/worker
# - coordinator_addresses: ["127.0.0.1:8001"]
```

### Environment Variables

Limited use for secrets:

```python
JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY', 'fallback-dev-key-only')
JWT_ALGORITHM = os.getenv('JWT_ALGORITHM', 'HS256')
```

**Recommendation**: Use environment variables only for passwords/secrets, not for configuration.

---

## Metrics Dashboard and Web UI

### Overview

**Location**: `dist/services/metrics_dashboard/`

The metrics dashboard provides a comprehensive web interface for monitoring and managing the P2P cluster.

**Access**: `https://coordinator:port/dashboard`

**Key Features**:
- Real-time cluster metrics visualization
- Historical metrics with graphs (last 100 data points)
- Centralized log viewer with filtering
- Service management (start/stop/restart)
- Node status monitoring
- Interactive UI with auto-refresh

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Metrics Dashboard                         │
│                    (Coordinator Only)                        │
└─────────────────────────────────────────────────────────────┘
         ▲                  ▲                  ▲
         │                  │                  │
    RPC Calls          HTTP API        WebSocket (Real-time)
         │                  │                  │
         │                  │         ┌────────┴────────┐
         │                  │         │  Push updates:  │
         │                  │         │  - Metrics      │
         │                  │         │  - Logs         │
         │                  │         │  - History      │
         │                  │         └─────────────────┘
         └──────────────────┴──────────────────┘
                           │
         ┌─────────────────┴─────────────────┐
         │                                   │
    ┌────▼────┐                         ┌────▼────┐
    │ Worker  │                         │ Worker  │
    │  Node   │                         │  Node   │
    └─────────┘                         └─────────┘
         │                                   │
    Metrics Reporter                    Metrics Reporter
    (sends metrics via RPC)             (sends logs immediately)
```

### Dashboard Service

**Service Name**: `metrics_dashboard`

**Runs On**: Coordinator node only (automatically disabled on workers)

**Responsibilities**:
- Collect metrics from all worker nodes
- Store historical data (configurable retention)
- Provide HTTP endpoints for web UI
- Integrate with log_collector for log viewing
- Manage service lifecycle on workers

**Initialization**:
```python
async def initialize(self):
    # Only runs on coordinator
    if self.context.config.coordinator_mode:
        # Register HTTP endpoints
        self._register_http_endpoints()

        # Start background tasks
        self.cleanup_task = asyncio.create_task(self._cleanup_loop())
        self.coordinator_metrics_task = asyncio.create_task(
            self._coordinator_metrics_loop()
        )
```

### HTTP Endpoints

The dashboard registers the following HTTP endpoints on the coordinator's FastAPI app:

#### Web UI
```bash
GET /dashboard              # Main dashboard HTML
GET /dashboard/static/*     # Static assets (CSS, JS)
```

#### Metrics API
```bash
# Get all cluster metrics (single optimized request)
GET /api/dashboard/metrics
# Returns: {
#   "coordinator": {...},
#   "workers": {
#     "worker-1": {...},
#     "worker-2": {...}
#   },
#   "services": [...],
#   "cluster_stats": {...}
# }

# Get metrics history for a node
GET /api/dashboard/history/{node_id}?limit=100

# Get dashboard statistics
GET /api/dashboard/stats
```

#### Service Management API
```bash
# Control services on workers
POST /api/dashboard/control-service
{
  "worker_id": "worker-001",
  "service_name": "my_service",
  "action": "restart"  # start, stop, restart
}

# Get service states across cluster
GET /api/dashboard/services
```

#### Logs API
```bash
# Get logs with filtering
GET /api/logs?node_id=worker-1&level=ERROR&limit=100

# Get available log sources
GET /api/logs/sources

# Clear logs
DELETE /api/logs?node_id=worker-1
```

### Metrics Reporter Service

**Location**: `dist/services/metrics_reporter/`

**Service Name**: `metrics_reporter`

**Runs On**: Worker nodes

**Purpose**: Collects local metrics and reports them to the coordinator's dashboard.

**Features**:
- Adaptive reporting interval (30-300 seconds based on load)
- Collects system metrics (CPU, memory, disk)
- Collects service states and health
- Compresses data before transmission
- Handles coordinator unavailability gracefully

**Metrics Collected**:
```python
{
    "node_id": "worker-001",
    "timestamp": "2025-11-14T10:30:00",
    "system": {
        "cpu_percent": 45.2,
        "memory_percent": 62.8,
        "disk_usage": {
            "/": {"percent": 55.0, "total_gb": 500}
        },
        "uptime_seconds": 86400
    },
    "services": {
        "my_service": {
            "status": "running",
            "metrics": {...},
            "health": "healthy"
        }
    }
}
```

**Reporting Flow**:
```python
# Worker collects metrics
metrics = await self._collect_metrics()

# Send to coordinator dashboard
await self.proxy.metrics_dashboard.coordinator.report_metrics(
    node_id=self.node_id,
    metrics=metrics
)
```

### Web UI Features

#### Dashboard Tabs

1. **Overview Tab**:
   - Cluster statistics (total nodes, services, uptime)
   - Coordinator status and metrics
   - Active workers list with status indicators
   - Real-time graphs for CPU, memory, disk

2. **Workers Tab**:
   - Detailed view of each worker node
   - Individual worker metrics
   - Service status per worker
   - Historical metrics graphs

3. **Services Tab**:
   - All services across the cluster
   - Service health and status
   - Start/Stop/Restart controls
   - Service-specific metrics

4. **Logs Tab** (NEW):
   - Centralized log viewer
   - Filter by node, level, logger
   - Search functionality
   - Real-time updates
   - Color-coded log levels
   - Timestamp display

#### WebSocket Real-Time Updates

**NEW FEATURE**: The dashboard uses WebSocket for real-time push updates instead of HTTP polling.

**WebSocket Endpoint**: `wss://coordinator:port/ws/dashboard`

**Communication Pattern**:
```javascript
// Client connects to WebSocket
const ws = new WebSocket('wss://coordinator:8001/ws/dashboard');

// Client sends ping every 4 seconds
setInterval(() => {
    ws.send('ping');
}, 4000);

// Server responds with pong + data update
ws.onmessage = (event) => {
    const message = JSON.parse(event.data);

    if (message.type === 'initial') {
        // Initial load: metrics + logs + certificates
        updateDashboard(message.data);
    }

    if (message.type === 'update') {
        // Periodic update: metrics + history (every 4s with ping/pong)
        updateMetrics(message.data.metrics);
        updateCharts(message.data.history);
    }

    if (message.type === 'new_logs') {
        // Event-driven: immediate log delivery
        prependLogs(message.logs);
    }
};
```

**Server Implementation** (`metrics_dashboard/main.py`):
```python
@app.websocket("/ws/dashboard")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    self.active_websockets.add(websocket)

    # Send initial data immediately
    initial_data = await self._gather_ws_data()
    initial_data["logs"] = await self.proxy.log_collector.get_logs(limit=100)
    initial_data["log_sources"] = await self.proxy.log_collector.get_log_sources()
    await websocket.send_json({"type": "initial", "data": initial_data})

    # Keep connection alive and respond to pings
    while True:
        message = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)

        if message == "ping":
            # Send pong + update data with every ping
            update_data = await self._gather_ws_data()
            await websocket.send_json({"type": "pong"})
            await websocket.send_json({
                "type": "update",
                "data": update_data,
                "timestamp": datetime.now().isoformat()
            })
```

**Data Gathering**:
```python
async def _gather_ws_data(self):
    """Gather metrics and history for WebSocket updates"""
    metrics_data = await self.get_cluster_metrics()

    # Include last 50 points of history for charts
    history_data = {
        "coordinator": self.metrics_history["coordinator"][-50:],
        **{wid: self.metrics_history[wid][-50:]
           for wid in self.worker_metrics.keys()}
    }

    return {
        "metrics": metrics_data,
        "history": history_data,
        "timestamp": datetime.now().isoformat()
    }
```

**Benefits**:
- ✅ Real-time updates (push model, not polling)
- ✅ Reduced server load (no repeated HTTP requests)
- ✅ Lower latency (immediate updates on ping/pong cycle)
- ✅ Bidirectional communication
- ✅ Automatic reconnection on disconnect

**Control Actions Remain HTTP**:
Service management (start/stop/restart), certificate operations, and other control actions still use HTTP POST requests for reliability and simplicity.

#### Event-Driven Log Streaming

**NEW FEATURE**: Logs are delivered immediately as they are generated, not in batches.

**Architecture**:
```
Worker Node                  Coordinator
     │                            │
     ├─► P2PLogHandler            │
     │   .emit(log_record)        │
     │   │                        │
     │   ├─► immediate_callback ──►│ LogCollector
     │   │   (asyncio.create_task) │ .add_logs()
     │   │                         │ │
     │   │                         │ ├─► notify listeners
     │   │                         │ │
     │   │                         │ ├─► Dashboard
     │   │                         │     ._on_new_logs()
     │   │                         │     │
     │   │                         │     └─► Broadcast via WebSocket
     │   │                         │         to all connected clients
     │   │                         │
     │   └─► Fallback: buffer ─────►│ (every 60s)
```

**Immediate Callback in P2PLogHandler** (`methods/log_collector.py`):
```python
class P2PLogHandler(logging.Handler):
    def __init__(self, node_id: str, max_logs: int = 1000, immediate_callback=None):
        super().__init__()
        self.node_id = node_id
        self.buffer = deque(maxlen=max_logs)
        self.immediate_callback = immediate_callback  # Real-time streaming

    def emit(self, record: logging.LogRecord):
        log_entry = LogEntry(
            timestamp=datetime.fromtimestamp(record.created).isoformat(),
            node_id=self.node_id,
            level=record.levelname,
            logger_name=record.name,
            message=record.getMessage(),
            # ... other fields
        )

        self.buffer.append(log_entry)

        # Call callback immediately for real-time streaming
        if self.immediate_callback:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(
                        self.immediate_callback(self.node_id, [log_entry.to_dict()])
                    )
            except Exception:
                pass  # Fallback to buffered collection
```

**Setup with Immediate Callback** (`layers/application_context.py`):
```python
def _setup_log_handler(self):
    log_collector = self.context.get_shared("log_collector")

    # Create async callback for immediate log delivery
    async def immediate_log_callback(node_id, logs):
        try:
            await log_collector.add_logs(node_id, logs)
        except Exception as e:
            self.logger.debug(f"Failed to send immediate log: {e}")

    # Create handler with immediate callback
    log_handler = P2PLogHandler(
        node_id=self.context.config.node_id,
        max_logs=self.context.config.max_log_entries,
        immediate_callback=immediate_log_callback
    )

    logging.getLogger().addHandler(log_handler)
```

**Listener System in LogCollector** (`methods/log_collector.py`):
```python
class LogCollectorService:
    def __init__(self):
        self.new_log_listeners = []  # Publish-subscribe pattern

    def add_new_log_listener(self, listener):
        """Register callback for new logs"""
        if listener not in self.new_log_listeners:
            self.new_log_listeners.append(listener)

    @service_method(public=True)
    async def add_logs(self, node_id: str, logs: List[dict]) -> dict:
        # Store logs...

        # Notify listeners immediately (event-driven)
        for listener in self.new_log_listeners:
            if asyncio.iscoroutinefunction(listener):
                await listener(node_id, logs)
```

**Dashboard Registration** (`metrics_dashboard/main.py`):
```python
def _register_log_listener(self):
    """Register for immediate log notifications"""
    log_collector = service_manager.services.get('log_collector')
    if log_collector:
        log_collector.add_new_log_listener(self._on_new_logs)

async def _on_new_logs(self, node_id: str, new_logs: list):
    """Broadcast new logs to all WebSocket clients immediately"""
    message = {
        "type": "new_logs",
        "node_id": node_id,
        "logs": new_logs,
        "timestamp": datetime.now().isoformat()
    }

    for websocket in self.active_websockets:
        try:
            await websocket.send_json(message)
        except Exception as e:
            self.logger.debug(f"Failed to send to client: {e}")
```

**Client-Side Handling** (`dashboard.html`):
```javascript
websocket.onmessage = (event) => {
    const message = JSON.parse(event.data);

    if (message.type === 'new_logs') {
        // Prepend new logs to table immediately
        const tbody = document.getElementById('logsTableBody');
        message.logs.reverse().forEach(log => {
            const row = createLogRow(log);
            tbody.insertBefore(row, tbody.firstChild);
        });

        // Limit to 100 rows
        while (tbody.children.length > 100) {
            tbody.removeChild(tbody.lastChild);
        }
    }
};
```

**Benefits**:
- ✅ **Immediate delivery**: Logs appear in dashboard instantly (< 100ms)
- ✅ **No batching delay**: Previously logs were buffered and sent every 5 seconds
- ✅ **Event-driven architecture**: Clean publish-subscribe pattern
- ✅ **Fallback mechanism**: Buffered collection still runs every 60s for reliability
- ✅ **Scalable**: Multiple listeners can subscribe to log events

**Trade-offs**:
- Each log entry triggers a callback (more overhead than batching)
- Requires asyncio event loop to be running
- Falls back to buffered collection if immediate callback fails

### RPC Methods

**Dashboard Service Methods** (coordinator):
```python
# Report metrics from worker
@service_method(public=True)
async def report_metrics(
    self,
    node_id: str,
    metrics: Dict[str, Any]
) -> Dict[str, Any]:
    # Store metrics with timestamp
    # Update metrics history
    # Return acknowledgment

# Get all cluster metrics (optimized single call)
@service_method(public=True)
async def get_cluster_metrics(self) -> Dict[str, Any]:
    # Returns coordinator + all workers + services in one response

# Get metrics history
@service_method(public=True)
async def get_metrics_history(
    self,
    node_id: str,
    limit: int = 100
) -> List[Dict[str, Any]]:
    # Returns last N metric snapshots

# Control service on worker
@service_method(public=True)
async def control_service(
    self,
    worker_id: str,
    service_name: str,
    action: str
) -> Dict[str, Any]:
    # Forwards command to worker's orchestrator service
```

**Reporter Service Methods** (worker):
```python
# No public methods - reports via proxy calls
```

### Configuration

```python
# Dashboard (coordinator)
dashboard_enabled: bool = True
metrics_retention_count: int = 100  # Historical data points
metrics_cleanup_interval: int = 300  # Cleanup stale workers (seconds)

# Reporter (worker)
reporter_enabled: bool = True
reporter_interval_min: int = 30     # Minimum reporting interval (seconds)
reporter_interval_max: int = 300    # Maximum reporting interval (seconds)
```

### Integration Example

**Worker reports metrics**:
```python
# In metrics_reporter service (worker)
async def _report_loop(self):
    while self.running:
        # Collect metrics
        metrics = await self._collect_metrics()

        # Report to coordinator
        try:
            result = await self.proxy.metrics_dashboard.coordinator.report_metrics(
                node_id=self.node_id,
                metrics=metrics
            )
            self.logger.debug(f"Metrics reported: {result}")
        except Exception as e:
            self.logger.error(f"Failed to report metrics: {e}")

        # Wait before next report
        await asyncio.sleep(self.report_interval)
```

**Dashboard displays metrics**:
```python
# In metrics_dashboard service (coordinator)
@service_method(public=True)
async def get_cluster_metrics(self) -> Dict[str, Any]:
    # Collect coordinator metrics
    coordinator_metrics = await self._collect_coordinator_metrics()

    # Get all worker metrics from storage
    worker_metrics = self.worker_metrics

    # Get service states
    services = await self._get_all_services()

    # Return combined data
    return {
        "coordinator": coordinator_metrics,
        "workers": worker_metrics,
        "services": services,
        "cluster_stats": {
            "active_workers": len(worker_metrics),
            "total_services": len(services),
            "uptime": time.time() - self.start_time
        }
    }
```

### Best Practices

**For Operators**:
1. Monitor the dashboard regularly for cluster health
2. Set up alerts for high CPU/memory usage
3. Check logs tab for errors and warnings
4. Use service controls to restart unhealthy services
5. Archive old metrics if retention grows too large

**For Developers**:
1. Expose meaningful metrics in your services
2. Use appropriate health check responses
3. Log important events for dashboard visibility
4. Test service start/stop/restart handling
5. Handle coordinator disconnection gracefully in reporter

### Troubleshooting

**Issue: Dashboard not loading**
```bash
# Check coordinator is running
curl https://coordinator:8001/health

# Check dashboard service is loaded
curl https://coordinator:8001/services | grep metrics_dashboard

# Check logs
curl https://coordinator:8001/api/logs?logger_name=metrics_dashboard
```

**Issue: Workers not appearing**
```bash
# Check worker's metrics_reporter service
# Worker side:
curl https://worker:8002/services | grep metrics_reporter

# Check if worker can reach coordinator
# Worker side:
curl https://coordinator:8001/health

# Check dashboard received reports
# Coordinator side:
curl https://coordinator:8001/api/dashboard/stats
```

**Issue: Stale metrics**
```bash
# Check reporter interval (may be set too high)
# Check network connectivity between worker and coordinator
# Check coordinator's cleanup task is running
```

---

## Testing Approach

### Integration Testing

**Location**: `test_metrics.py`, `test_orchestrator.py`, `test_dashboard_api.py`

Tests are written as standalone scripts that interact with the running system via HTTP:

```python
class P2PMetricsTestClient:
    async def authenticate(self):
        # POST /auth/token
        
    async def get_health(self):
        # GET /health
        
    async def call_service_method(self, service, method, params):
        # POST /rpc with JSON-RPC payload
```

**Pattern**: Create test client → authenticate → call endpoints → verify responses

### Health Checks

Built-in health monitoring endpoints:

- `GET /health`: Overall system health
- `GET /metrics`: Aggregated metrics from all services
- `GET /services`: List all services and their status
- `GET /cluster/nodes`: Cluster node status

### Metrics Validation

Services automatically collect metrics:
- **Counters**: `method_calls`, `errors`, `heartbeat_count`
- **Gauges**: `cpu_usage`, `memory_usage`, `uptime_seconds`
- **Timers**: `method_duration_ms`

**Access metrics**:
```python
service_instance.metrics.increment("requests")
service_instance.metrics.gauge("active_connections", 42)
service_instance.metrics.timer("query_time", duration_ms)
```

### Service Testing Pattern

```python
# 1. Load service in test mode
service = MyService("test_service", proxy_client=None)
await service.start()

# 2. Call methods directly
result = await service.my_method(param="test")

# 3. Verify metrics
assert service.metrics.counters["method_my_method_calls"] > 0

# 4. Check health
health = service.get_health_report()
assert health["status"] == "running"

# 5. Cleanup
await service.stop()
```

---

## Development Conventions

### Code Organization

```
P2P_Core/
├── layers/                      # Core framework
│   ├── application_context.py   # Central orchestrator
│   ├── transport.py             # HTTP/2 transport
│   ├── network.py               # Gossip protocol
│   ├── service.py               # Service management
│   ├── cache.py                 # Multi-level caching
│   ├── ssl_helper.py            # SSL/TLS utilities
│   ├── storage_manager.py       # Secure storage
│   ├── secure_storage.py        # Encryption layer
│   ├── persistence.py           # State persistence
│   ├── rate_limiter.py          # Rate limiting
│   └── local_service_bridge.py  # Local/remote proxy
├── methods/                     # Built-in core services
│   ├── system.py                # System information service
│   └── log_collector.py         # Centralized log collection
├── dist/services/               # Pluggable services
│   ├── orchestrator/            # Service orchestration & deployment
│   ├── metrics_dashboard/       # Web UI for monitoring & logs
│   ├── metrics_reporter/        # Worker metrics collection
│   ├── update_manager/          # Service update management
│   ├── update_server/           # Service update distribution
│   ├── certs_tool/              # Legacy certificate management (Windows CSP)
│   ├── test_monitoring/         # Testing & monitoring utilities
│   └── test_metric_service/     # Metrics testing service
├── scripts/                     # Utility scripts
├── docs/                        # Documentation
├── p2p.py                       # Main entry point
└── requirements.txt
```

### Naming Conventions

**Components**:
- Class names: `TransportComponent`, `NetworkComponent`
- Instance names: `transport_component`, `network_component`
- Shared state keys: `"transport"`, `"network"`, `"cache"`

**Services**:
- Class name: `Run` (required)
- Class attribute: `SERVICE_NAME = "my_service"`
- File structure: `dist/services/my_service/main.py`

**Methods**:
- Registry path: `"{service_name}/{method_name}"`
- Decorator: `@service_method(description="...", public=True)`
- Async always: All service methods must be `async def`

**Configuration**:
- Snake_case: `gossip_interval`, `ssl_cert_file`
- Booleans: `coordinator_mode`, `https_enabled`
- Paths: Relative to project root or state_directory

### Logging

**Standard logger usage**:
```python
import logging

# In classes
self.logger = logging.getLogger("ComponentName")
self.logger.info("Component initialized")
self.logger.error(f"Failed to process: {e}")

# Module-level
logger = logging.getLogger("ModuleName")
```

**Log levels**:
- `DEBUG`: Detailed diagnostic info
- `INFO`: Startup, shutdown, major events
- `WARNING`: Recoverable issues
- `ERROR`: Errors that need attention
- `CRITICAL`: System-critical failures

### Error Handling

**Pattern**:
```python
try:
    # Operation
    result = await risky_operation()
except SpecificException as e:
    self.logger.error(f"Operation failed: {e}")
    # Cleanup if needed
    raise  # Re-raise or wrap in custom exception
finally:
    # Always cleanup resources
    await cleanup()
```

**Custom exceptions**:
```python
class ServiceOrchestratorError(Exception):
    """Base exception for service orchestrator"""
    pass

class ServiceInstallationError(ServiceOrchestratorError):
    """Specific error type"""
    pass
```

### Async/Await

**Rules**:
1. All service methods MUST be `async def`
2. Always use `await` for async operations
3. Use `asyncio.create_task()` for background tasks
4. Use `asyncio.gather()` for parallel operations

```python
# ✅ CORRECT
@service_method(public=True)
async def process_data(self, data: dict) -> dict:
    result = await self.proxy.other_service.process(data)
    return result

# ❌ WRONG
@service_method(public=True)
def process_data(self, data: dict) -> dict:  # Missing async
    result = self.proxy.other_service.process(data)  # Missing await
    return result
```

### Type Hints

**Always use type hints**:
```python
from typing import Dict, List, Optional, Any

async def my_method(
    self,
    required_param: str,
    optional_param: Optional[int] = None
) -> Dict[str, Any]:
    return {"result": "success"}
```

---

## Common Patterns

### 1. Creating a New Service

```python
# dist/services/my_service/main.py
from layers.service import BaseService, service_method
from typing import Dict, Any

class Run(BaseService):
    SERVICE_NAME = "my_service"
    
    def __init__(self, service_name: str, proxy_client=None):
        super().__init__(service_name, proxy_client)
        self.info.version = "1.0.0"
        self.info.description = "My custom service"
        
    async def initialize(self):
        """Startup logic"""
        self.logger.info("Initializing my_service")
        # Initialize resources
        
    async def cleanup(self):
        """Shutdown logic"""
        self.logger.info("Cleaning up my_service")
        # Close connections, save state
        
    @service_method(description="Do something", public=True)
    async def do_something(self, param: str) -> Dict[str, Any]:
        """
        Example method
        
        Args:
            param: Input parameter
            
        Returns:
            Result dictionary
        """
        # Call another service
        system_info = await self.proxy.system.get_system_info()
        
        # Track metrics
        self.metrics.increment("do_something_calls")
        
        return {
            "result": f"Processed: {param}",
            "node": system_info.get("hostname")
        }
```

### 2. Calling Services (Local and Remote)

```python
# In any service method

# Local call
result = await self.proxy.my_service.do_something(param="test")

# Remote call to specific node
result = await self.proxy.my_service.worker_123.do_something(param="test")

# Remote call to coordinator
result = await self.proxy.my_service.coordinator.do_something(param="test")

# Parallel calls to multiple nodes
tasks = []
for node_id in ["worker_1", "worker_2", "worker_3"]:
    task = self.proxy.my_service.__getattr__(node_id).do_something(param="test")
    tasks.append(task)

results = await asyncio.gather(*tasks, return_exceptions=True)
```

### 3. Accessing Context in Services

```python
class Run(BaseService):
    async def initialize(self):
        # Access application context
        if hasattr(self, 'context'):
            # Get configuration
            node_id = self.context.config.node_id
            
            # Get shared components
            network = self.context.get_shared("network")
            cache = self.context.get_shared("cache")
            
            # Access node registry
            nodes = network.gossip.node_registry
```

### 4. Using Secure Storage

```python
# In component initialization
storage = self.context.get_shared("storage_manager")

# Read configuration
config_yaml = storage.read_config("my_config.yaml")

# Read certificate
cert_data = storage.read_cert("my_cert.cer")

# Write certificate
storage.write_cert("new_cert.cer", cert_bytes)

# Save (happens automatically on shutdown, but can force)
storage.save()
```

### 5. Working with Metrics

```python
class Run(BaseService):
    @service_method(public=True, track_metrics=True)
    async def process_request(self, data: dict) -> dict:
        # Metrics are automatically tracked by decorator
        # But you can add custom metrics:
        
        # Counter
        self.metrics.increment("requests_processed")
        
        # Gauge
        self.metrics.gauge("queue_size", len(self.queue))
        
        # Timer (manual)
        start = time.time()
        result = await self._do_work(data)
        duration_ms = (time.time() - start) * 1000
        self.metrics.timer("work_duration", duration_ms)
        
        # Timer (context manager)
        async with self.metrics.timing_context("database_query"):
            result = await self.db.query(...)
        
        return result
```

### 6. Component with Dependencies

```python
class MyComponent(P2PComponent):
    def __init__(self, context: P2PApplicationContext):
        super().__init__("my_component", context)
        
        # Declare dependencies (ensures initialization order)
        self.add_dependency("transport")
        self.add_dependency("cache")
        
    async def _do_initialize(self):
        # Dependencies are guaranteed to be initialized
        transport = self.context.get_shared("transport")
        cache = self.context.get_shared("cache")
        
        self.my_service = MyService(transport, cache)
        await self.my_service.start()
        
        # Register in shared state
        self.context.set_shared("my_component", self.my_service)
        
    async def _do_shutdown(self):
        if hasattr(self, 'my_service'):
            await self.my_service.stop()
```

### 7. Graceful Shutdown

```python
# In main application
async def main():
    context = P2PApplicationContext(config)
    
    # Register shutdown handler
    context.add_shutdown_handler(my_cleanup_function)
    
    # Initialize all components
    await context.initialize_all()
    
    # Wait for shutdown signal (SIGINT, SIGTERM)
    await context.wait_for_shutdown()
    
    # Graceful shutdown (automatic, but can force)
    await context.shutdown_all()
```

### 8. Custom RPC Endpoint

```python
# In P2PServiceHandler._setup_endpoints()

@self.app.post("/my/custom/endpoint")
async def my_endpoint(request: Dict[str, Any]):
    """Custom endpoint outside of RPC"""
    service_manager = self.service_manager
    
    # Access services
    orchestrator = service_manager.services.get("orchestrator")
    
    # Call methods
    result = await orchestrator.my_method(**request)
    
    return {"result": result}
```

### 9. Broadcasting to All Nodes

```python
@service_method(public=True)
async def broadcast_message(self, message: str) -> Dict[str, Any]:
    """Send message to all nodes in cluster"""
    network = self.context.get_shared("network")
    nodes = network.gossip.node_registry
    
    results = {}
    tasks = []
    
    for node_id, node_info in nodes.items():
        if node_id != self.context.config.node_id:  # Skip self
            # Create task for each node
            task = self.proxy.my_service.__getattr__(node_id).receive_message(
                message=message
            )
            tasks.append((node_id, task))
    
    # Execute in parallel
    for node_id, task in tasks:
        try:
            result = await task
            results[node_id] = {"status": "success", "result": result}
        except Exception as e:
            results[node_id] = {"status": "error", "error": str(e)}
    
    return {"results": results}
```

### 10. State Persistence

```python
# Using persistence layer
from layers.persistence import StatePersistence

class Run(BaseService):
    async def initialize(self):
        # Setup persistence
        state_file = self.context.config.get_state_path("my_service_state.json")
        self.persistence = StatePersistence(state_file)
        
        # Load saved state
        self.state = self.persistence.load() or {"counter": 0}
        
    async def cleanup(self):
        # Save state on shutdown
        self.persistence.save(self.state)
        
    @service_method(public=True)
    async def increment(self) -> int:
        self.state["counter"] += 1
        self.persistence.save(self.state)  # Persist immediately
        return self.state["counter"]
```

---

## Important Reminders for AI Assistants

### 1. NEVER Use Global State

❌ **WRONG**:
```python
global_cache = {}
global_network = None
method_registry = {}
```

✅ **CORRECT**:
```python
context = P2PApplicationContext.get_current_context()
cache = context.get_shared("cache")
network = context.get_shared("network")
registry = context._method_registry
```

### 2. ALWAYS Use Secure Storage for Sensitive Data

❌ **WRONG**:
```python
with open("certs/ca_cert.cer", "wb") as f:
    f.write(cert_data)
```

✅ **CORRECT**:
```python
storage = context.get_shared("storage_manager")
storage.write_cert("ca_cert.cer", cert_data)
```

### 3. ALWAYS Declare Dependencies

❌ **WRONG**:
```python
class MyComponent(P2PComponent):
    async def _do_initialize(self):
        network = self.context.get_shared("network")  # May not be initialized!
```

✅ **CORRECT**:
```python
class MyComponent(P2PComponent):
    def __init__(self, context):
        super().__init__("my_component", context)
        self.add_dependency("network")  # Ensures network initializes first
        
    async def _do_initialize(self):
        network = self.context.get_shared("network")  # Guaranteed to exist
```

### 4. ALWAYS Use Async/Await

❌ **WRONG**:
```python
def my_method(self):
    result = self.proxy.service.method()  # Missing await
    return result
```

✅ **CORRECT**:
```python
async def my_method(self):
    result = await self.proxy.service.method()
    return result
```

### 5. ALWAYS Handle Graceful Shutdown

❌ **WRONG**:
```python
async def _do_shutdown(self):
    self.connection.close()  # Blocking operation
```

✅ **CORRECT**:
```python
async def _do_shutdown(self):
    if hasattr(self, 'connection'):
        await self.connection.aclose()
```

### 6. ALWAYS Use Type Hints

❌ **WRONG**:
```python
async def process(self, data):
    return data
```

✅ **CORRECT**:
```python
async def process(self, data: Dict[str, Any]) -> Dict[str, Any]:
    return data
```

### 7. NEVER Hardcode Paths

❌ **WRONG**:
```python
config_file = "config/coordinator.yaml"
cert_file = "/etc/certs/ca_cert.cer"
```

✅ **CORRECT**:
```python
config_file = self.context.config.ssl_cert_file
state_file = self.context.config.get_state_path("my_state.json")
```

### 8. ALWAYS Log Important Events

✅ **CORRECT**:
```python
self.logger.info(f"Service {self.service_name} started")
self.logger.error(f"Failed to connect to {node_id}: {e}")
self.logger.debug(f"Processing request: {request_id}")
```

### 9. ALWAYS Use Metrics

✅ **CORRECT**:
```python
# Track method calls
self.metrics.increment("api_calls")

# Track performance
self.metrics.timer("query_duration_ms", duration)

# Track state
self.metrics.gauge("active_connections", count)
```

### 10. ALWAYS Follow the Service Template

When creating a new service, copy the template from section "1. Creating a New Service" and modify it. Don't create from scratch.

---

## Quick Reference

### Essential Imports

```python
# Application context
from layers.application_context import P2PApplicationContext, P2PComponent, P2PConfig

# Service framework
from layers.service import BaseService, service_method, ServiceManager

# Storage
from layers.storage_manager import get_storage_manager

# SSL/TLS
from layers.ssl_helper import (
    generate_ca_certificate,
    generate_signed_certificate,
    create_client_ssl_context
)

# Networking
from layers.network import NodeInfo, SimpleGossipProtocol

# Caching
from layers.cache import P2PMultiLevelCache, CacheConfig

# Persistence
from layers.persistence import StatePersistence
```

### Common Context Operations

```python
# Get current context
context = P2PApplicationContext.get_current_context()

# Access config
node_id = context.config.node_id
port = context.config.port

# Get components
network = context.get_shared("network")
cache = context.get_shared("cache")
storage = context.get_shared("storage_manager")

# Method registry
registry = context._method_registry
context.register_method("service/method", callable)
```

### Common Service Operations

```python
# Call local service
result = await self.proxy.service.method(param="value")

# Call remote service (by node ID)
result = await self.proxy.service.worker_123.method(param="value")

# Call remote service (by role)
result = await self.proxy.service.coordinator.method(param="value")

# Metrics
self.metrics.increment("counter_name")
self.metrics.gauge("gauge_name", value)
self.metrics.timer("timer_name", duration_ms)

# Logging
self.logger.info("Message")
self.logger.error(f"Error: {e}")
```

### Common Gossip Operations

```python
# Get network component
network = context.get_shared("network")
gossip = network.gossip

# Get all nodes
nodes = gossip.node_registry  # Dict[str, NodeInfo]

# Get specific node
node_info = gossip.node_registry.get("worker_123")

# Get node URL
if node_info:
    url = node_info.get_url(https=True)

# Filter by role
coordinators = [
    node for node in gossip.node_registry.values()
    if node.role == "coordinator"
]
```

### Common WebSocket Operations

**NEW**: Working with WebSocket for real-time updates

```python
# Server-side: Add WebSocket endpoint to service
from fastapi import WebSocket, WebSocketDisconnect

class Run(BaseService):
    def __init__(self, service_name: str, proxy_client=None):
        super().__init__(service_name, proxy_client)
        self.active_websockets = set()  # Track connected clients

    def _register_http_endpoints(self):
        """Register WebSocket endpoint with FastAPI app"""
        app = self.context.get_shared("fastapi_app")

        @app.websocket("/ws/my_endpoint")
        async def websocket_endpoint(websocket: WebSocket):
            await websocket.accept()
            self.active_websockets.add(websocket)

            try:
                # Send initial data
                initial_data = await self._gather_initial_data()
                await websocket.send_json({"type": "initial", "data": initial_data})

                # Keep connection alive
                while True:
                    message = await asyncio.wait_for(
                        websocket.receive_text(), timeout=10.0
                    )

                    if message == "ping":
                        # Respond with pong + update
                        update_data = await self._gather_update_data()
                        await websocket.send_json({"type": "pong"})
                        await websocket.send_json({
                            "type": "update",
                            "data": update_data
                        })

            except WebSocketDisconnect:
                self.logger.info("Client disconnected")
            finally:
                self.active_websockets.discard(websocket)

    async def _broadcast_to_clients(self, message: dict):
        """Broadcast message to all connected WebSocket clients"""
        disconnected = []
        for websocket in self.active_websockets:
            try:
                await websocket.send_json(message)
            except Exception as e:
                self.logger.debug(f"Failed to send to client: {e}")
                disconnected.append(websocket)

        # Clean up disconnected clients
        for ws in disconnected:
            self.active_websockets.discard(ws)
```

```javascript
// Client-side: Connect to WebSocket (in HTML template)
let websocket = null;
let pingInterval = null;

function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/my_endpoint`;

    websocket = new WebSocket(wsUrl);

    websocket.onopen = () => {
        console.log('Connected to WebSocket');

        // Start ping interval
        pingInterval = setInterval(() => {
            if (websocket && websocket.readyState === WebSocket.OPEN) {
                websocket.send('ping');
            }
        }, 4000);  // Ping every 4 seconds
    };

    websocket.onmessage = (event) => {
        const message = JSON.parse(event.data);

        if (message.type === 'initial') {
            // Handle initial data load
            updateUI(message.data);
        }

        if (message.type === 'update') {
            // Handle periodic updates
            updateMetrics(message.data);
        }

        if (message.type === 'event') {
            // Handle real-time events
            handleEvent(message.data);
        }
    };

    websocket.onclose = () => {
        console.log('WebSocket disconnected');

        if (pingInterval) {
            clearInterval(pingInterval);
            pingInterval = null;
        }

        // Reconnect after 3 seconds
        setTimeout(connectWebSocket, 3000);
    };

    websocket.onerror = (error) => {
        console.error('WebSocket error:', error);
    };
}

// Connect on page load
document.addEventListener('DOMContentLoaded', () => {
    connectWebSocket();
});
```

**Event-Driven Broadcasting Pattern**:
```python
# Service with event-driven updates
class Run(BaseService):
    async def initialize(self):
        # Register as listener for events
        event_source = self.context.get_shared("event_source")
        if event_source:
            event_source.add_listener(self._on_event)

    async def _on_event(self, event_data: dict):
        """Called when event occurs - broadcast to WebSocket clients"""
        if self.active_websockets:
            message = {
                "type": "event",
                "data": event_data,
                "timestamp": datetime.now().isoformat()
            }
            await self._broadcast_to_clients(message)
```

**Best Practices**:
- ✅ Use ping/pong to keep connection alive and detect disconnects
- ✅ Track connected clients in a set for efficient broadcasting
- ✅ Clean up disconnected clients to prevent memory leaks
- ✅ Implement automatic reconnection on client side
- ✅ Use message types ("initial", "update", "event") for clarity
- ✅ Handle WebSocketDisconnect gracefully
- ✅ Use timeout on receive_text() to allow periodic updates
- ❌ Don't send large payloads (> 1MB) via WebSocket
- ❌ Don't use WebSocket for control actions (use HTTP POST)

---

## Troubleshooting Guide

### Issue: "Storage manager not available"

**Cause**: Trying to access storage before initialization

**Solution**:
```python
# Wait for context initialization
await context.initialize_all()

# Then access storage
storage = context.get_shared("storage_manager")
```

### Issue: "Method not found in registry"

**Cause**: Service not loaded or method not public

**Solution**:
1. Check service is in `dist/services/` with correct structure
2. Verify `@service_method(public=True)` decorator
3. Check method registered: `list(context._method_registry.keys())`

### Issue: "Target node not found in node registry"

**Cause**: Node not yet discovered via gossip

**Solution**:
```python
# Wait for gossip to discover nodes
await asyncio.sleep(5)

# Or check if node exists before calling
network = context.get_shared("network")
if "worker_123" in network.gossip.node_registry:
    result = await proxy.service.worker_123.method()
```

### Issue: "SSL verification failed"

**Cause**: CA certificate mismatch or missing

**Solution**:
1. Verify CA certificate in secure storage: `storage.read_cert("ca_cert.cer")`
2. Regenerate certificates if needed
3. Check `ssl_verify: true` in config
4. Ensure `ssl_ca_cert_file` points to correct cert

### Issue: "Component initialization failed"

**Cause**: Missing dependency or wrong initialization order

**Solution**:
```python
# Declare dependencies properly
class MyComponent(P2PComponent):
    def __init__(self, context):
        super().__init__("my_component", context)
        self.add_dependency("required_component")
```

---

## Architecture Decision Records (ADRs)

### ADR-001: Why ApplicationContext?

**Decision**: Use centralized ApplicationContext for all state management

**Rationale**:
- Eliminates global variables
- Enforces initialization order through dependency declaration
- Enables graceful shutdown with proper cleanup order
- Provides single source of truth for all system state

**Consequences**:
- All components must receive context in constructor
- No global state allowed
- Components must declare dependencies explicitly

### ADR-002: Why Secure Storage?

**Decision**: Store all sensitive data in encrypted archive

**Rationale**:
- Prevents credential theft from file system
- Single password protects all secrets
- No plaintext certificates or keys on disk
- Cross-platform (works on Linux, Windows, macOS)

**Consequences**:
- Must initialize storage before accessing certs/configs
- Password management becomes critical
- Cannot inspect files directly without decryption

### ADR-003: Why ACME-like Certificate Issuance?

**Decision**: Implement challenge-based certificate validation

**Rationale**:
- Proves ownership of IP address/hostname
- Prevents certificate misissuance
- Follows industry standard (ACME protocol)
- Enables automatic renewal

**Consequences**:
- Workers need temporary HTTP server for validation
- Coordinator must be able to reach workers
- Firewall rules must allow challenge validation

### ADR-004: Why Gossip Protocol?

**Decision**: Use gossip for node discovery instead of centralized registry

**Rationale**:
- No single point of failure
- Scales well with cluster size
- Self-healing (nodes auto-discover each other)
- Low network overhead with compression

**Consequences**:
- Eventual consistency (nodes may have stale info)
- Network traffic increases with cluster size
- Need failure detection mechanism

### ADR-005: Why Component-Based Architecture?

**Decision**: All major system parts inherit from P2PComponent

**Rationale**:
- Consistent lifecycle management
- Automatic state tracking
- Built-in metrics and health reporting
- Clear dependency relationships

**Consequences**:
- New components must implement _do_initialize and _do_shutdown
- Must declare dependencies explicitly
- Cannot skip initialization order

### ADR-006: Why Centralized Log Collection?

**Decision**: Implement centralized log collection infrastructure instead of relying on external logging systems

**Rationale**:
- **Integration**: Built directly into P2P framework, no external dependencies
- **Simplicity**: No need to configure syslog, Elasticsearch, or other log aggregators
- **Performance**: In-memory storage with bounded size (circular buffer)
- **Filtering**: Rich query capabilities (by node, level, logger, time)
- **Web UI**: Integrated with metrics dashboard for unified monitoring

**Consequences**:
- Logs are stored in memory (lost on coordinator restart)
- Limited retention (configurable max entries per node)
- Not suitable for long-term log archival (use external system for that)
- Coordinator becomes single point for log queries
- Workers must periodically flush logs to coordinator

**Trade-offs**:
- ✅ Simple deployment (no external services)
- ✅ Fast queries (in-memory)
- ✅ Unified UI (logs + metrics + services)
- ❌ No persistence across restarts
- ❌ Limited history retention
- ❌ Coordinator load increases with log volume

**When to use external logging**:
- Long-term log retention required (>1000 entries per node)
- Compliance/audit requirements
- Advanced analytics needed
- Multiple independent P2P clusters

### ADR-007: Why Multi-Homed Node Support?

**Decision**: Implement automatic interface detection and address probing instead of requiring manual IP configuration

**Rationale**:
- **Flexibility**: Handles complex network topologies (VPN, multi-NIC, cloud)
- **Zero-config**: No manual IP selection needed
- **Reliability**: Automatic failover between interfaces
- **Enterprise-ready**: Supports common enterprise scenarios (VPN, DMZ, etc.)

**Implementation**:
- Probe all non-loopback interfaces on startup
- Test connectivity to coordinator on each interface
- Select interface based on subnet proximity and reachability
- Update gossip info with selected address

**Consequences**:
- Increased startup time (probing adds ~2-5 seconds)
- More complex network code
- Potential for incorrect interface selection (can be overridden)
- Background probing may affect network monitoring tools

**Configuration override**:
```yaml
# Force specific interface if auto-detection fails
bind_address: "10.8.0.5"
enable_address_probing: false
```

### ADR-008: Why Separate Built-in and Pluggable Services?

**Decision**: Distinguish between core services (`methods/`) and pluggable services (`dist/services/`)

**Rationale**:
- **Stability**: Core services (system, log_collector) are always available
- **Extensibility**: Pluggable services can be added/removed without code changes
- **Modularity**: Clear separation between framework and features
- **Deployment flexibility**: Different nodes can run different service sets

**Core Services** (`methods/`):
- Loaded at startup, always running
- Part of base framework
- Cannot be disabled or updated independently
- Examples: system info, log collection

**Pluggable Services** (`dist/services/`):
- Auto-discovered via ServiceLoader
- Can be started/stopped dynamically
- Can be updated without restart (hot-reload)
- Examples: orchestrator, dashboard, metrics_reporter

**Consequences**:
- Clear mental model for developers
- Easier to maintain and test core vs. plugins
- More complex service loading logic
- Need to document which services are core vs. pluggable

### ADR-009: Why WebSocket for Real-Time Dashboard?

**Decision**: Use WebSocket for real-time dashboard updates with event-driven log streaming instead of HTTP polling

**Rationale**:
- **Lower latency**: Push model delivers updates immediately (< 100ms) vs polling every 5 seconds
- **Reduced load**: Single persistent connection vs repeated HTTP requests every 5 seconds
- **Bidirectional communication**: Enables client-initiated pings and server-initiated updates
- **Better UX**: Graphs and logs update smoothly without page refreshes
- **Event-driven architecture**: Logs appear instantly when generated, not batched

**Implementation Details**:
- Client sends ping every 4 seconds
- Server responds with pong + metrics/history update
- Separate event-driven channel for immediate log delivery
- Publish-subscribe pattern with listener system in LogCollector
- P2PLogHandler calls immediate_callback via asyncio.create_task()
- Falls back to buffered collection every 60 seconds for reliability
- Control actions (start/stop services) remain HTTP POST for simplicity

**Consequences**:
- ✅ Real-time updates with minimal delay
- ✅ Reduced server load (no polling overhead)
- ✅ Scalable to multiple concurrent dashboard clients
- ✅ Clean separation: WebSocket for data, HTTP for control actions
- ❌ More complex client code (connection management, reconnection)
- ❌ Requires persistent connection (more memory per client)
- ❌ WebSocket proxies may need special configuration

**Trade-offs**:
- **HTTP Polling** (old):
  - ✅ Simple implementation
  - ✅ Stateless, works through any proxy
  - ❌ 5-second batching delay
  - ❌ High server load (repeated requests)
  - ❌ Network overhead

- **WebSocket Push** (new):
  - ✅ Immediate updates (< 100ms)
  - ✅ Low overhead (persistent connection)
  - ✅ Event-driven architecture
  - ❌ Stateful connection
  - ❌ More complex implementation

**Metrics**:
- Old: HTTP poll every 5 seconds = 720 requests/hour/client
- New: WebSocket ping every 4 seconds = 1 connection + 900 tiny pings/hour/client
- Log delivery: Old = batched every 5 seconds, New = immediate (< 100ms)

**When to use HTTP vs WebSocket**:
- **WebSocket**: Real-time data display (metrics, logs, charts)
- **HTTP POST**: Control actions (start/stop services, certificate management)
- **HTTP GET**: One-time queries, static resources

---

**End of CLAUDE.md**

This document should be your primary reference when working with the P2P_Core codebase. Follow these patterns, conventions, and architectural decisions to maintain consistency and quality.

## Recent Updates

**2025-11-14 (Latest Session - WebSocket Real-Time Updates)**:
- **Implemented WebSocket-based real-time dashboard updates**
  - Replaced HTTP polling with WebSocket push model
  - Client sends ping every 4 seconds, server responds with pong + data update
  - Reduced server load and improved latency (from 5s polling to < 100ms push)
  - Added automatic reconnection on disconnect
- **Implemented event-driven log streaming**
  - Logs delivered immediately (< 100ms) instead of batched every 5 seconds
  - Added publish-subscribe pattern with listener system in LogCollector
  - P2PLogHandler now supports immediate_callback for real-time streaming
  - Falls back to buffered collection every 60 seconds for reliability
- **Added metrics history to WebSocket updates**
  - Charts update in real-time with last 50 data points
  - Smooth graph animations without page refresh
- **Fixed log spam from periodic certificate listing**
  - Removed service_data from periodic WebSocket updates
  - Certificates only loaded once on initial WebSocket connection
- **Updated CLAUDE.md documentation**
  - Added WebSocket Real-Time Updates section (line 1213)
  - Added Event-Driven Log Streaming section (line 1309)
  - Added Common WebSocket Operations section (line 2345)
  - Updated architecture diagrams to reflect push model
  - Added code examples for WebSocket implementation
  - Added ADR-009: Why WebSocket for Real-Time Dashboard

**2025-11-14 (Initial)**:
- Added Log Collection System documentation
- Added Built-in vs Pluggable Services distinction
- Added Multi-Homed Node Support documentation
- Added VPN and Complex Network Support
- Added Metrics Dashboard and Web UI comprehensive documentation
- Updated service listings with all current services
- Added ADR-006 (Centralized Log Collection)
- Added ADR-007 (Multi-Homed Node Support)
- Added ADR-008 (Built-in vs Pluggable Services)
