#!/usr/bin/env python3
"""
Test Service Editor API endpoints
"""
import asyncio
import httpx
import json
import urllib3

# Disable SSL warnings for self-signed certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

COORDINATOR_URL = "https://127.0.0.1:8001"

async def test_list_services():
    """Test list services endpoint"""
    print("Testing /api/services/list...")

    async with httpx.AsyncClient(verify=False) as client:
        response = await client.post(
            f"{COORDINATOR_URL}/api/services/list",
            json={"node_id": "coordinator"},
            timeout=10.0
        )

        print(f"Status: {response.status_code}")
        result = response.json()
        print(f"Response: {json.dumps(result, indent=2)}")
        return result

async def test_list_service_files():
    """Test list service files endpoint"""
    print("\nTesting /api/services/files...")

    # First get list of services
    services_response = await test_list_services()

    if services_response.get('success') and services_response.get('services'):
        service_name = services_response['services'][0]['name']
        print(f"\nTesting file list for service: {service_name}")

        async with httpx.AsyncClient(verify=False) as client:
            response = await client.post(
                f"{COORDINATOR_URL}/api/services/files",
                json={
                    "node_id": "coordinator",
                    "service_name": service_name
                },
                timeout=10.0
            )

            print(f"Status: {response.status_code}")
            result = response.json()
            print(f"Response: {json.dumps(result, indent=2)}")
            return result

async def main():
    print("=== Testing Service Editor API Endpoints ===\n")

    try:
        # Test 1: List services
        await test_list_services()

        # Test 2: List files
        await test_list_service_files()

        print("\n✅ All tests completed!")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
