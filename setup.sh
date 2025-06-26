#!/bin/bash
# Setup script for P2P Admin System

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Functions
print_header() {
    echo -e "\n${BLUE}=== $1 ===${NC}\n"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

# Check dependencies
check_dependencies() {
    print_header "Checking Dependencies"

    # Python
    if command -v python3 &> /dev/null; then
        PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
        print_success "Python 3 found: $PYTHON_VERSION"
    else
        print_error "Python 3 is not installed"
        exit 1
    fi

    # pip
    if command -v pip3 &> /dev/null; then
        print_success "pip3 found"
    else
        print_error "pip3 is not installed"
        exit 1
    fi

    # Docker (optional)
    if command -v docker &> /dev/null; then
        print_success "Docker found (optional)"
    else
        print_warning "Docker not found (optional)"
    fi

    # Docker Compose (optional)
    if command -v docker-compose &> /dev/null; then
        print_success "Docker Compose found (optional)"
    else
        print_warning "Docker Compose not found (optional)"
    fi
}

# Create directory structure
create_directories() {
    print_header "Creating Directory Structure"

    directories=(
        "data"
        "logs"
        "certs"
        "cache"
        "temp"
        "monitoring/grafana/dashboards"
        "monitoring/grafana/datasources"
        "monitoring/alerts"
        "nginx/ssl"
        "tests/unit"
        "tests/integration"
        "tests/performance"
        "docs/source"
        "docs/build"
    )

    for dir in "${directories[@]}"; do
        mkdir -p "$dir"
        print_success "Created $dir"
    done
}

# Setup Python environment
setup_python_env() {
    print_header "Setting up Python Environment"

    # Create virtual environment
    if [ ! -d "venv" ]; then
        python3 -m venv venv
        print_success "Created virtual environment"
    else
        print_warning "Virtual environment already exists"
    fi

    # Activate virtual environment
    source venv/bin/activate

    # Upgrade pip
    pip install --upgrade pip
    print_success "Upgraded pip"

    # Install requirements
    if [ -f "requirements.txt" ]; then
        pip install -r requirements.txt
        print_success "Installed Python dependencies"
    else
        print_error "requirements.txt not found"
        exit 1
    fi
}

# Generate certificates
generate_certificates() {
    print_header "Generating SSL Certificates"

    if [ ! -f "certs/cert.pem" ] || [ ! -f "certs/key.pem" ]; then
        openssl req -x509 -newkey rsa:4096 \
            -keyout certs/key.pem \
            -out certs/cert.pem \
            -days 365 -nodes \
            -subj "/CN=localhost/O=P2P Admin System/C=US"
        print_success "Generated self-signed SSL certificates"
    else
        print_warning "SSL certificates already exist"
    fi

    # Copy to nginx
    cp certs/cert.pem nginx/ssl/
    cp certs/key.pem nginx/ssl/
    print_success "Copied certificates to nginx directory"
}

# Setup environment file
setup_env_file() {
    print_header "Setting up Environment Configuration"

    if [ ! -f ".env" ]; then
        if [ -f ".env.example" ]; then
            cp .env.example .env
            print_success "Created .env file from .env.example"

            # Generate random secret
            SECRET=$(openssl rand -hex 32)
            if [[ "$OSTYPE" == "darwin"* ]]; then
                # macOS
                sed -i '' "s/your-secret-key-change-in-production/$SECRET/g" .env
            else
                # Linux
                sed -i "s/your-secret-key-change-in-production/$SECRET/g" .env
            fi
            print_success "Generated random AUTH_SECRET"
        else
            print_error ".env.example not found"
            exit 1
        fi
    else
        print_warning ".env file already exists"
    fi
}

# Initialize database (if needed)
init_database() {
    print_header "Initializing Database"

    # Create SQLite database file
    touch data/p2p_admin.db
    print_success "Created SQLite database file"
}

# Setup systemd service (Linux only)
setup_systemd_service() {
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        print_header "Setting up Systemd Service"

        cat > p2p-admin.service << EOF
[Unit]
Description=P2P Admin System
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$(pwd)
Environment="PATH=$(pwd)/venv/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=$(pwd)/venv/bin/python run.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

        print_success "Created systemd service file"
        print_warning "To install: sudo cp p2p-admin.service /etc/systemd/system/"
        print_warning "To enable: sudo systemctl enable p2p-admin"
        print_warning "To start: sudo systemctl start p2p-admin"
    fi
}

# Quick test
quick_test() {
    print_header "Running Quick Test"

    # Activate virtual environment
    source venv/bin/activate

    # Test imports
    python -c "
import sys
sys.path.insert(0, '.')
try:
    from core import P2PNode, AsyncDHT
    from api import create_app
    from services import ProcessManagerService
    print('✓ All imports successful')
except Exception as e:
    print(f'✗ Import error: {e}')
    sys.exit(1)
"

    if [ $? -eq 0 ]; then
        print_success "Import test passed"
    else
        print_error "Import test failed"
        exit 1
    fi
}

# Docker setup
docker_setup() {
    if command -v docker &> /dev/null && command -v docker-compose &> /dev/null; then
        print_header "Docker Setup"

        echo -e "${YELLOW}Build Docker images? (y/n)${NC}"
        read -r response

        if [[ "$response" == "y" ]]; then
            docker-compose build
            print_success "Docker images built"
        fi
    fi
}

# Print final instructions
print_instructions() {
    print_header "Setup Complete!"

    echo -e "${GREEN}P2P Admin System is ready to use!${NC}\n"

    echo "To start the system:"
    echo -e "${BLUE}1. Activate virtual environment:${NC}"
    echo "   source venv/bin/activate"
    echo ""
    echo -e "${BLUE}2. Start a single node:${NC}"
    echo "   python run.py"
    echo ""
    echo -e "${BLUE}3. Or start with Docker:${NC}"
    echo "   docker-compose up -d"
    echo ""
    echo -e "${BLUE}4. Access the admin interface:${NC}"
    echo "   http://localhost:8501"
    echo ""
    echo -e "${BLUE}5. Run tests:${NC}"
    echo "   pytest"
    echo ""
    echo -e "${BLUE}6. For more commands:${NC}"
    echo "   make help"
}

# Main execution
main() {
    echo -e "${BLUE}"
    echo "╔═══════════════════════════════════════╗"
    echo "║       P2P Admin System Setup          ║"
    echo "╚═══════════════════════════════════════╝"
    echo -e "${NC}"

    check_dependencies
    create_directories
    setup_python_env
    generate_certificates
    setup_env_file
    init_database
    setup_systemd_service
    quick_test
    docker_setup
    print_instructions
}

# Run main function
main