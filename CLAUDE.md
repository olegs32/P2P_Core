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
├── methods/                     # Built-in RPC methods
│   └── system.py                # System service
├── dist/services/               # Pluggable services
│   ├── orchestrator/            # Service orchestration
│   ├── metrics_dashboard/       # Metrics UI
│   ├── metrics_reporter/        # Metrics collection
│   └── update_manager/          # Service updates
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

---

**End of CLAUDE.md**

This document should be your primary reference when working with the P2P_Core codebase. Follow these patterns, conventions, and architectural decisions to maintain consistency and quality.
