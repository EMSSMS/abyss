#!/usr/bin/env python3
"""
ABYSS SMS - SMPP Gateway Runner

Usage:
    python run_smpp.py                    # Run with default settings
    python run_smpp.py --port 2775       # Custom port
    python run_smpp.py --host 0.0.0.0    # Custom host

Environment Variables:
    SMPP_HOST           - Host to bind (default: 0.0.0.0)
    SMPP_PORT            - Port to listen (default: 2775)
    SMPP_ALLOWED_IPS     - Comma-separated allowed IPs
    SMPP_SYSTEM_ID       - System identifier
    DATABASE_URL         - Database connection string
    SECRET_KEY           - Flask secret key
"""

import os
import sys
import argparse

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load environment variables from .env if exists
def load_env():
    env_file = os.path.join(os.path.dirname(__file__), '.env.smpp')
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ.setdefault(key.strip(), value.strip())


def main():
    # Load environment
    load_env()

    # Parse arguments
    parser = argparse.ArgumentParser(description='ABYSS SMS SMPP Gateway')
    parser.add_argument('--host', default=os.environ.get('SMPP_HOST', '0.0.0.0'),
                        help='Host to bind (default: 0.0.0.0)')
    parser.add_argument('--port', type=int, default=int(os.environ.get('SMPP_PORT', 2775)),
                        help='Port to listen (default: 2775)')
    parser.add_argument('--allowed-ips', default=os.environ.get('SMPP_ALLOWED_IPS', ''),
                        help='Comma-separated allowed IPs')
    parser.add_argument('--system-id', default=os.environ.get('SMPP_SYSTEM_ID', 'abyss_sms'),
                        help='SMPP System ID')
    parser.add_argument('--debug', action='store_true',
                        help='Enable debug logging')

    args = parser.parse_args()

    # Import SMPP server
    from smpp_server import SMPPServerConfig, start_smpp_server
    import logging

    # Configure logging
    if args.debug:
        logging.getLogger('smpp_server').setLevel(logging.DEBUG)

    # Create configuration
    config = SMPPServerConfig()
    config.HOST = args.host
    config.PORT = args.port
    config.SYSTEM_ID = args.system_id

    if args.allowed_ips:
        config.ALLOWED_IPS = [ip.strip() for ip in args.allowed_ips.split(',')]

    print("=" * 60)
    print("ABYSS SMS - SMPP Gateway Server")
    print("=" * 60)
    print(f"Host: {config.HOST}")
    print(f"Port: {config.PORT}")
    print(f"Allowed IPs: {', '.join(config.ALLOWED_IPS)}")
    print(f"System ID: {config.SYSTEM_ID}")
    print("=" * 60)

    # Start server
    try:
        start_smpp_server(config)
    except KeyboardInterrupt:
        print("\nSMPP Server stopped by user")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
