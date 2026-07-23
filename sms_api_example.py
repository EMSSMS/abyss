"""
ABYSS SMS API - Python Example Script
=====================================
This script demonstrates how to use the ABYSS SMS API to:
1. Send SMS messages
2. Simulate SMS received (SCR)
3. Get SMS ranges
4. Get SMS numbers (your reserved numbers)
5. Reserve new numbers from range pool
6. View SMS CDR (Call Detail Records)

Usage:
    pip install requests
    python sms_api_example.py
"""

import requests
import json
from datetime import datetime

# Configuration
API_BASE_URL = "http://localhost:5000"  # Change to your API URL
API_TOKEN = "YOUR_API_TOKEN_HERE"  # Get from your profile page  # Your API token

# Headers for API authentication
HEADERS = {
    "X-API-Token": API_TOKEN,
    "Content-Type": "application/json"
}

def send_sms(number, destination, cli, message):
    """
    Send an SMS message via API

    Args:
        number: SMS number to send from (e.g., "+201012345678")
        destination: Recipient number (e.g., "+201234567890")
        cli: Sender application (e.g., "WhatsApp", "Viber", "Facebook")
        message: Message content
    """
    url = f"{API_BASE_URL}/api/sms/send"
    payload = {
        "number": number,
        "destination": destination,
        "cli": cli,
        "message": message
    }

    try:
        response = requests.post(url, json=payload, headers=HEADERS)
        data = response.json()

        if response.status_code == 200 and data.get('success'):
            print(f"✓ SMS sent successfully!")
            print(f"  CDR ID: {data.get('cdr_id')}")
            print(f"  Profit: ${data.get('profit', 0):.4f}")
            return data
        else:
            print(f"✗ Failed to send SMS: {data.get('error')}")
            return None
    except Exception as e:
        print(f"✗ Error: {str(e)}")
        return None

def send_bulk_sms(number, destinations, cli, message):
    """
    Send SMS to multiple recipients

    Args:
        number: SMS number to send from
        destinations: List of recipient numbers
        cli: Sender application
        message: Message content
    """
    url = f"{API_BASE_URL}/api/sms/send-bulk"
    payload = {
        "number": number,
        "destinations": destinations,
        "cli": cli,
        "message": message
    }

    try:
        response = requests.post(url, json=payload, headers=HEADERS)
        data = response.json()

        if response.status_code == 200 and data.get('success'):
            print(f"✓ Bulk SMS sent successfully!")
            print(f"  Count: {data.get('count')}")
            print(f"  Total Profit: ${data.get('total_profit', 0):.4f}")
            return data
        else:
            print(f"✗ Failed to send bulk SMS: {data.get('error')}")
            return None
    except Exception as e:
        print(f"✗ Error: {str(e)}")
        return None

def sms_scr(number, from_num, cli, message):
    """
    Simulate SMS received on a number (for testing)
    This is only works if you OWN the number!

    Args:
        number: The SMS number that receives the message (MUST BE YOUR NUMBER!)
        from_num: Sender number
        cli: Sender app
        message: Message content
    """
    url = f"{API_BASE_URL}/api/sms/scr"
    payload = {
        "number": number,
        "from": from_num,
        "cli": cli,
        "message": message
    }

    try:
        response = requests.post(url, json=payload, headers=HEADERS)
        data = response.json()

        if response.status_code == 200 and data.get('success'):
            print(f"✓ SMS received on your number!")
            print(f"  CDR ID: {data.get('cdr_id')}")
            print(f"  Profit credited: ${data.get('profit', 0):.4f}")
            return data
        else:
            print(f"✗ Failed: {data.get('error')}")
            return None
    except Exception as e:
        print(f"✗ Error: {str(e)}")
        return None

def get_sms_ranges(search=""):
    """Get all SMS ranges with available counts"""
    url = f"{API_BASE_URL}/api/sms/ranges"
    params = {"search": search} if search else {}

    try:
        response = requests.get(url, headers=HEADERS, params=params)
        data = response.json()

        if response.status_code == 200:
            print(f"\n📋 SMS Ranges ({data['pagination']['total']} total):")
            for r in data['results']:
                available = r.get('available_count', 'N/A')
                reserved = r.get('reserved_count', 'N/A')
                print(f"  [{r['id']}] {r.get('name') or r['prefix']} - {r['country']}")
                print(f"      Available: {available} | Reserved: {reserved}")
            return data['results']
        else:
            print(f"✗ Failed to get ranges: {data.get('error')}")
            return None
    except Exception as e:
        print(f"✗ Error: {str(e)}")
        return None

def get_sms_numbers(range_id=None):
    """Get YOUR reserved SMS numbers"""
    url = f"{API_BASE_URL}/api/sms/numbers"
    params = {}
    if range_id:
        params['range_id'] = range_id

    try:
        response = requests.get(url, headers=HEADERS, params=params)
        data = response.json()

        if response.status_code == 200:
            print(f"\n📞 Your SMS Numbers ({data['pagination']['total']} total):")
            for n in data['results'][:20]:  # Show first 20
                print(f"  {n['number']} ({n.get('range_name') or n.get('range', 'N/A')})")
            if data['pagination']['total'] > 20:
                print(f"  ... and {data['pagination']['total'] - 20} more")
            return data['results']
        else:
            print(f"✗ Failed to get numbers: {data.get('error')}")
            return None
    except Exception as e:
        print(f"✗ Error: {str(e)}")
        return None

def reserve_numbers(range_id, quantity):
    """
    Reserve numbers from a range pool
    Each user gets unique numbers - no duplicates!

    Args:
        range_id: ID of the range to take from
        quantity: How many numbers to reserve
    """
    url = f"{API_BASE_URL}/api/sms/numbers/request"
    payload = {
        "range_id": range_id,
        "quantity": quantity
    }

    try:
        response = requests.post(url, json=payload, headers=HEADERS)
        data = response.json()

        if response.status_code == 200 and data.get('success'):
            print(f"✓ Reserved {data.get('count')} numbers successfully!")
            print(f"  First 5: {data.get('numbers', [])[:5]}")
            return data
        else:
            print(f"✗ Failed to reserve numbers: {data.get('error')}")
            return None
    except Exception as e:
        print(f"✗ Error: {str(e)}")
        return None

def get_sms_cdr(sms_type=None):
    """Get YOUR SMS call detail records"""
    url = f"{API_BASE_URL}/api/sms/cdr"
    params = {}
    if sms_type:
        params['type'] = sms_type

    try:
        response = requests.get(url, headers=HEADERS, params=params)
        data = response.json()

        if response.status_code == 200:
            print(f"\n📜 Your SMS CDR Records ({data['pagination']['total']} total):")
            for cdr in data['results'][:10]:  # Show first 10
                sms_type = cdr.get('sms_type', 'N/A')
                profit = cdr.get('profit', 0)
                direction = "FROM" if sms_type == 'received' else "TO"
                other = cdr.get('caller_id') or cdr.get('destination') or 'N/A'
                print(f"  [{cdr['id']}] {sms_type.upper()} | {direction}: {other} | Profit: ${profit:.4f}")
            return data['results']
        else:
            print(f"✗ Failed to get CDR: {data.get('error')}")
            return None
    except Exception as e:
        print(f"✗ Error: {str(e)}")
        return None

def get_sms_stats():
    """Get YOUR SMS statistics"""
    url = f"{API_BASE_URL}/api/sms/cdr/stats"

    try:
        response = requests.get(url, headers=HEADERS)
        data = response.json()

        if response.status_code == 200:
            print(f"\n📊 Your SMS Statistics:")
            print(f"  Today: {data.get('today', 0)} SMS")
            print(f"  Received Today: {data.get('received_today', 0)} SMS")
            print(f"  This Week: {data.get('week', 0)} SMS")
            print(f"  This Month: {data.get('month', 0)} SMS")
            print(f"  Total: {data.get('total', 0)} SMS")
            revenue = data.get('revenue', {})
            print(f"  Revenue: ${revenue.get('total', 0):.4f}")
            return data
        else:
            print(f"✗ Failed to get stats: {data.get('error')}")
            return None
    except Exception as e:
        print(f"✗ Error: {str(e)}")
        return None

# ==================== EXAMPLE USAGE ====================

if __name__ == "__main__":
    print("=" * 60)
    print("ABYSS SMS API - Python Example")
    print("=" * 60)

    # Example 1: Get SMS ranges
    print("\n[1] Fetching SMS ranges with availability...")
    ranges = get_sms_ranges()

    # Example 2: Reserve numbers from a range
    if ranges:
        print("\n[2] Reserving 10 numbers from first range...")
        first_range_id = ranges[0]['id'] if ranges else 1
        reserve_numbers(first_range_id, 10)

    # Example 3: Get your SMS numbers
    print("\n[3] Fetching YOUR SMS numbers...")
    numbers = get_sms_numbers()

    # Example 4: Get SMS statistics
    print("\n[4] Fetching YOUR SMS statistics...")
    get_sms_stats()

    # Example 5: Get SMS CDR
    print("\n[5] Fetching YOUR SMS CDR records...")
    get_sms_cdr()

    # Example 6: Get received SMS only
    print("\n[6] Fetching YOUR received SMS only...")
    get_sms_cdr(sms_type='received')

    # Example 7: Send SMS (if you have numbers)
    if numbers:
        print("\n[7] Sending single SMS...")
        first_number = numbers[0]['number'] if numbers else None
        if first_number:
            send_sms(
                number=first_number,
                destination="+201234567890",
                cli="WhatsApp",
                message="Hello from ABYSS SMS API!"
            )

    # Example 8: Test SMS SCR (Simulate Received)
    if numbers:
        print("\n[8] Testing SMS SCR (Simulate Received)...")
        first_number = numbers[0]['number'] if numbers else None
        if first_number:
            sms_scr(
                number=first_number,
                from_num="+201234567891",
                cli="Facebook",
                message="Test message received"
            )

    print("\n" + "=" * 60)
    print("Examples completed!")
    print("=" * 60)
    print("\n💡 IMPORTANT NOTES:")
    print("  - SMS SCR only works on numbers YOU own!")
    print("  - Each user gets unique numbers from range pool")
    print("  - SMS only goes to the user who reserved the number")
    print("  - 0.005 profit credited for each SMS received")