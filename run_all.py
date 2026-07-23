#!/usr/bin/env python3
"""
ABYSS SMS - Combined Server Runner

Starts both the HTTP API server and the SMPP Gateway server together.

Usage:
    python run_all.py                    # Default settings
    python run_all.py --http-port 5977   # Custom HTTP port
    python run_all.py --smpp-port 2775   # Custom SMPP port
"""

import os
import sys
import argparse
import threading
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger('abyss_main')

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def load_env():
    """Load environment variables from .env files"""
    env_files = ['h.env', 'h.env.smpp']
    for env_file in env_files:
        if os.path.exists(env_file):
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        os.environ.setdefault(key.strip(), value.strip())


def run_http_server(host='0.0.0.0', port=10075):
    """Run the Flask HTTP API server"""
    try:
        from run import create_app
        app = create_app()
        logger.info(f"Starting HTTP API server on {host}:{port}")
        app.run(host=host, port=port, debug=False, threaded=True)
    except Exception as e:
        logger.error(f"HTTP Server error: {e}")


def run_smpp_server(host='0.0.0.0', port=2775):
    """Run the SMPP Gateway server"""
    try:
        from smpp_server import start_smpp_server, SMPPServerConfig
        config = SMPPServerConfig()
        config.HOST = host
        config.PORT = port
        logger.info(f"Starting SMPP Gateway on {host}:{port}")
        start_smpp_server(config)
    except Exception as e:
        logger.error(f"SMPP Server error: {e}")


def print_banner():
    """Print startup banner"""
    print("""
╔═══════════════════════════════════════════════════════════════╗
║                                                               ║
║     ██████╗ ██╗███╗   ███╗███████╗██╗  ██╗                    ║
║     ██╔══██╗██║████╗ ████║██╔════╝╚██╗██╔╝                    ║
║     ██║  ██║██║██╔████╔██║█████╗   ╚███╔╝                     ║
║     ██║  ██║██║██║╚██╔╝██║██╔══╝   ██╔██╗                     ║
║     ██████╔╝██║██║ ╚═╝ ██║███████╗██╔╝ ██╗                    ║
║     ╚═════╝ ╚═╝╚═╝     ╚═╝╚══════╝╚═╝  ╚═╝                    ║
║                                                               ║
║     SMS Platform - Combined Server                            ║
║                                                               ║
╚═══════════════════════════════════════════════════════════════╝
    """)


def main():
    # Load environment
    load_env()

    # Parse arguments
    parser = argparse.ArgumentParser(description='ABYSS SMS - Combined Server')
    parser.add_argument('--http-host', default='0.0.0.0',
                        help='HTTP API host (default: 0.0.0.0)')
    parser.add_argument('--http-port', type=int, default=5977,
                        help='HTTP API port (default: 10075)')
    parser.add_argument('--smpp-host', default=os.environ.get('SMPP_HOST', '0.0.0.0'),
                        help='SMPP Gateway host (default: 0.0.0.0)')
    parser.add_argument('--smpp-port', type=int, default=int(os.environ.get('SMPP_PORT', 2775)),
                        help='SMPP Gateway port (default: 2775)')
    parser.add_argument('--smpp-only', action='store_true',
                        help='Run only SMPP server')
    parser.add_argument('--http-only', action='store_true',
                        help='Run only HTTP server')
    parser.add_argument('--debug', action='store_true',
                        help='Enable debug mode')

    args = parser.parse_args()

    # Configure logging level
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    print_banner()

    print(f"Configuration:")
    print(f"  HTTP API:   {args.http_host}:{args.http_port}")
    print(f"  SMPP GW:    {args.smpp_host}:{args.smpp_port}")

    allowed_ips = os.environ.get('SMPP_ALLOWED_IPS', '')
    if allowed_ips:
        print(f"  Allowed IPs: {allowed_ips}")

    print("-" * 60)

    try:
        if args.http_only:
            # Run HTTP only
            run_http_server(args.http_host, args.http_port)

        elif args.smpp_only:
            # Run SMPP only
            run_smpp_server(args.smpp_host, args.smpp_port)

        else:
            # Run both servers in separate threads
            http_thread = threading.Thread(
                target=run_http_server,
                args=(args.http_host, args.http_port),
                daemon=True
            )
            smpp_thread = threading.Thread(
                target=run_smpp_server,
                args=(args.smpp_host, args.smpp_port),
                daemon=True
            )

            logger.info("Starting servers...")
            http_thread.start()
            smpp_thread.start()

            logger.info("All servers started successfully!")

            # Keep main thread alive
            while True:
                import time
                time.sleep(1)

    except KeyboardInterrupt:
        logger.info("Servers stopped by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
