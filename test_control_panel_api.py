#!/usr/bin/env python3
"""
Test Control Panel API endpoints
"""
import asyncio
import httpx
import json
import urllib3

# Disable SSL warnings for self-signed certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

COORDINATOR_URL = "https://127.0.0.1:8001"

async def test_get_config():
    """Test get-config endpoint"""
    print("Testing /api/dashboard/get-config...")

    async with httpx.AsyncClient(verify=False) as client:
        response = await client.post(
            f"{COORDINATOR_URL}/api/dashboard/get-config",
            json={"node_id": "coordinator"},
            timeout=10.0
        )

        print(f"Status: {response.status_code}")
        result = response.json()
        print(f"Response: {json.dumps(result, indent=2)}")
        return result

async def test_list_storage_files():
    """Test list-storage-files endpoint"""
    print("\nTesting /api/dashboard/list-storage-files...")

    async with httpx.AsyncClient(verify=False) as client:
        response = await client.post(
            f"{COORDINATOR_URL}/api/dashboard/list-storage-files",
            json={"node_id": "coordinator"},
            timeout=10.0
        )

        print(f"Status: {response.status_code}")
        result = response.json()
        print(f"Response: {json.dumps(result, indent=2)}")
        return result

async def test_get_metrics():
    """Test get metrics for worker nodes list"""
    print("\nTesting /api/dashboard/metrics (for worker nodes)...")

    async with httpx.AsyncClient(verify=False) as client:
        response = await client.get(
            f"{COORDINATOR_URL}/api/dashboard/metrics",
            timeout=10.0
        )

        print(f"Status: {response.status_code}")
        result = response.json()

        workers = result.get('workers', {})
        print(f"Found {len(workers)} workers:")
        for worker_id in workers.keys():
            print(f"  - {worker_id}")

        return result

async def main():
    print("=== Testing Control Panel API Endpoints ===\n")

    try:
        # Test 1: Get config
        await test_get_config()

        # Test 2: List storage files
        await test_list_storage_files()

        # Test 3: Get worker nodes
        await test_get_metrics()

        print("\n✅ All tests completed successfully!")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
