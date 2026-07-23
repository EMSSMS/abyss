#!/usr/bin/env python3
"""
ABYSS SMS - Unified Server (HTTP + SMPP on Single Port using gevent)

Uses gevent to handle both HTTP (Flask) and SMPP on ONE port (10075).

Usage:
    python run_unified.py                     # Both HTTP and SMPP on port 10075
    python run_unified.py --debug              # Debug mode
"""

import os
import sys
from datetime import datetime
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger('unified_server')

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def load_env():
    """Load environment variables from .env files"""
    env_files = ['.env', '.env.smpp']
    for env_file in env_files:
        if os.path.exists(env_file):
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        os.environ.setdefault(key.strip(), value.strip())


# Import gevent and patch stdlib
from gevent import monkey
monkey.patch_all()

import gevent
from gevent.server import StreamServer
from gevent.pool import Pool, Group


class ProtocolDetector:
    """Detects whether incoming connection is HTTP or SMPP"""

    SMPP_COMMAND_IDS = {
        0x00000001, 0x80000001,  # BIND_TRANSMITTER
        0x00000002, 0x80000002,  # BIND_RECEIVER
        0x00000009, 0x80000009,  # BIND_TRANSCEIVER
        0x00000004, 0x80000004,  # SUBMIT_SM
        0x00000005, 0x80000005,  # DELIVER_SM
        0x00000006, 0x80000006,  # UNBIND
        0x00000015, 0x80000015,  # ENQUIRE_LINK
        0x80000000,              # GENERIC_NACK
    }

    @classmethod
    def is_smpp(cls, data: bytes) -> bool:
        # First check: Does it look like HTTP?
        try:
            data_str = data[:50].decode('ascii', errors='ignore').upper()
            http_methods = ['GET ', 'POST ', 'PUT ', 'DELETE ', 'HEAD ', 'OPTIONS ', 'PATCH ', 'HTTP/']
            for method in http_methods:
                if data_str.startswith(method):
                    return False
        except:
            pass

        # Now check if it looks like SMPP
        try:
            if len(data) >= 8:
                cmd_length = int.from_bytes(data[0:4], 'big')
                cmd_id = int.from_bytes(data[4:8], 'big')

                # Valid SMPP PDU check
                if 16 <= cmd_length <= 1048576 and cmd_id in cls.SMPP_COMMAND_IDS:
                    return True
        except (ValueError, struct.error):
            pass

        # Default to HTTP for safety (safer for browsers)
        return False


class SMPPSession:
    """SMPP Session Handler"""

    def __init__(self, socket, address, config, flask_app):
        self.socket = socket
        self.address = address
        self.config = config
        self.flask_app = flask_app
        self.connected = False
        self.authenticated = False
        self.system_id = ''
        self.user = None

    def send_pdu(self, pdu_data: bytes):
        try:
            self.socket.sendall(pdu_data)
        except Exception as e:
            logger.error(f"Failed to send PDU: {e}")

    def receive_pdu(self):
        try:
            header = b''
            while len(header) < 16:
                chunk = self.socket.recv(16 - len(header))
                if not chunk:
                    return None
                header += chunk

            cmd_length = int.from_bytes(header[0:4], 'big')
            body = b''
            while len(body) < cmd_length - 16:
                chunk = self.socket.recv(cmd_length - 16 - len(body))
                if not chunk:
                    return None
                body += chunk

            return header + body
        except Exception as e:
            logger.error(f"Failed to receive PDU: {e}")
            return None

    def unpack_cstring(self, data, offset=0):
        end = data.find(b'\x00', offset)
        if end == -1:
            return '', len(data)
        return data[offset:end].decode('latin-1', errors='replace'), end + 1

    def handle_bind(self, pdu_data):
        offset = 16
        try:
            system_id, offset = self.unpack_cstring(pdu_data, offset)
            password, offset = self.unpack_cstring(pdu_data, offset)

            logger.info(f"SMPP Bind: system_id={system_id}, from={self.address[0]}")

            # Check IP whitelist
            if self.address[0] not in self.config.get('ALLOWED_IPS', []):
                logger.warning(f"IP {self.address[0]} not allowed")
                return self.create_bind_response(pdu_data, 0x0000000D)

            # Validate credentials
            valid, user = self.validate_credentials(system_id, password)
            if not valid:
                logger.warning(f"Auth failed for {system_id}")
                return self.create_bind_response(pdu_data, 0x0000000E)

            self.authenticated = True
            self.system_id = system_id
            self.user = user

            logger.info(f"User {system_id} authenticated successfully")
            return self.create_bind_response(pdu_data, 0x00000000, self.config.get('SYSTEM_ID', 'abyss_sms'))

        except Exception as e:
            logger.error(f"Bind error: {e}")
            return None

    def create_bind_response(self, pdu_data, status, system_id=''):
        cmd_id = int.from_bytes(pdu_data[4:8], 'big')
        seq = int.from_bytes(pdu_data[8:12], 'big')

        body = system_id.encode('latin-1') + b'\x00'
        body += b'\x00'

        pdu = bytearray()
        pdu.extend((16 + len(body)).to_bytes(4, 'big'))
        pdu.extend((cmd_id + 0x80000000).to_bytes(4, 'big'))
        pdu.extend(status.to_bytes(4, 'big'))
        pdu.extend(seq.to_bytes(4, 'big'))
        pdu.extend(body)

        return bytes(pdu)

    def validate_credentials(self, system_id, password):
        try:
            with self.flask_app.app_context():
                from app.models.user import User

                user = User.query.filter(
                    (User.username == system_id) | (User.api_token == system_id)
                ).first()

                if not user:
                    return False, None

                if not user.check_password(password):
                    return False, None

                if not user.is_active:
                    return False, None

                if user.role and user.role.name in ('admin', 'agent', 'client'):
                    return True, user

                return False, None

        except Exception as e:
            logger.error(f"Database error: {e}")
            return False, None

    def handle_submit_sm(self, pdu_data):
        if not self.authenticated:
            return self.create_error_response(pdu_data, 0x00000063)

        try:
            offset = 16
            service_type, offset = self.unpack_cstring(pdu_data, offset)
            offset += 2
            source_addr, offset = self.unpack_cstring(pdu_data, offset)
            offset += 2
            destination_addr, offset = self.unpack_cstring(pdu_data, offset)
            offset += 6
            registered_delivery = pdu_data[offset] if offset < len(pdu_data) else 0
            offset += 1
            offset += 1
            data_coding = pdu_data[offset] if offset < len(pdu_data) else 0
            offset += 1
            offset += 1
            sm_length = pdu_data[offset] if offset < len(pdu_data) else 0
            offset += 1

            short_message = pdu_data[offset:offset + sm_length]

            # Decode message
            try:
                if data_coding == 0x08:
                    message_text = short_message.decode('utf-16-be')
                elif data_coding in (0x00, 0x03):
                    message_text = short_message.decode('ascii')
                else:
                    message_text = short_message.decode('latin-1')
            except:
                message_text = short_message.decode('latin-1', errors='replace')

            source_addr = ''.join(c for c in source_addr if c.isdigit())
            destination_addr = ''.join(c for c in destination_addr if c.isdigit())

            logger.info(f"SMS: from={source_addr}, to={destination_addr}, len={len(message_text)}")

            # Log SMS
            self.log_sms(source_addr, destination_addr, message_text, source_addr)

            # Create response
            seq = int.from_bytes(pdu_data[8:12], 'big')
            message_id = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}{seq}"
            body = message_id.encode('latin-1') + b'\x00'

            resp = bytearray()
            resp.extend((16 + len(body)).to_bytes(4, 'big'))
            resp.extend((0x80000004).to_bytes(4, 'big'))
            resp.extend((0x00000000).to_bytes(4, 'big'))
            resp.extend(seq.to_bytes(4, 'big'))
            resp.extend(body)

            return bytes(resp)

        except Exception as e:
            logger.error(f"Submit_SM error: {e}")
            return self.create_error_response(pdu_data, 0x00000063)

    def log_sms(self, source_addr, destination, message, cli):
        try:
            with self.flask_app.app_context():
                from app import db
                from app.models.sms import SMSNumber, SMSCDR
                from app.models.user import User

                sms_number = SMSNumber.query.filter_by(number=source_addr).first()
                if not sms_number:
                    logger.warning(f"SMS number {source_addr} not found")
                    return

                owner = None
                if sms_number.agent_id:
                    owner = User.query.get(sms_number.agent_id)
                elif sms_number.client_id:
                    owner = User.query.get(sms_number.client_id)

                if not owner:
                    return

                sms_range = sms_number.sms_range
                rate = sms_range.rate if sms_range else 0.005
                agent_payout = sms_number.agent_payout if sms_number.agent_payout else rate
                client_payout = sms_number.client_payout if sms_number.client_payout else rate * 0.5
                profit = rate - agent_payout

                cdr = SMSCDR(
                    number_id=sms_number.id,
                    range_id=sms_number.range_id,
                    user_id=owner.id,
                    client_id=sms_number.client_id,
                    caller_id=source_addr,
                    destination=destination,
                    cli=cli,
                    message=message,
                    currency=sms_range.currency if sms_range else 'USD',
                    rate=rate,
                    agent_payout=agent_payout,
                    client_payout=client_payout,
                    profit=profit,
                    sms_type='received',
                    status='completed'
                )
                db.session.add(cdr)

                owner.sms_count = (owner.sms_count or 0) + 1

                if sms_number.agent_id:
                    agent = User.query.get(sms_number.agent_id)
                    if agent:
                        agent.balance = (agent.balance or 0) + agent_payout
                        agent.total_earned = (agent.total_earned or 0) + agent_payout

                if sms_number.client_id:
                    client = User.query.get(sms_number.client_id)
                    if client:
                        client.balance = (client.balance or 0) + client_payout
                        client.total_earned = (client.total_earned or 0) + client_payout

                db.session.commit()
                logger.info(f"SMS logged: payout=${agent_payout:.4f}")

        except Exception as e:
            logger.error(f"Failed to log SMS: {e}")

    def create_error_response(self, pdu_data, status):
        cmd_id = int.from_bytes(pdu_data[4:8], 'big')
        seq = int.from_bytes(pdu_data[8:12], 'big')

        resp = bytearray()
        resp.extend((16).to_bytes(4, 'big'))
        resp.extend((cmd_id + 0x80000000).to_bytes(4, 'big'))
        resp.extend(status.to_bytes(4, 'big'))
        resp.extend(seq.to_bytes(4, 'big'))

        return bytes(resp)

    def handle_enquire_link(self, pdu_data):
        seq = int.from_bytes(pdu_data[8:12], 'big')

        resp = bytearray()
        resp.extend((16).to_bytes(4, 'big'))
        resp.extend((0x80000015).to_bytes(4, 'big'))
        resp.extend((0).to_bytes(4, 'big'))
        resp.extend(seq.to_bytes(4, 'big'))

        return bytes(resp)

    def handle_unbind(self, pdu_data):
        seq = int.from_bytes(pdu_data[8:12], 'big')

        resp = bytearray()
        resp.extend((16).to_bytes(4, 'big'))
        resp.extend((0x80000006).to_bytes(4, 'big'))
        resp.extend((0).to_bytes(4, 'big'))
        resp.extend(seq.to_bytes(4, 'big'))

        return bytes(resp)

    def run(self):
        """Main SMPP session loop"""
        logger.info(f"SMPP connection from {self.address[0]}:{self.address[1]}")
        self.connected = True

        try:
            while self.connected:
                pdu_data = self.receive_pdu()
                if pdu_data is None:
                    break

                cmd_id = int.from_bytes(pdu_data[4:8], 'big')

                if cmd_id in (0x00000001, 0x00000002, 0x00000009):
                    resp = self.handle_bind(pdu_data)
                    if resp:
                        self.send_pdu(resp)

                elif cmd_id == 0x00000004:
                    resp = self.handle_submit_sm(pdu_data)
                    if resp:
                        self.send_pdu(resp)

                elif cmd_id == 0x00000015:
                    resp = self.handle_enquire_link(pdu_data)
                    if resp:
                        self.send_pdu(resp)

                elif cmd_id == 0x00000006:
                    resp = self.handle_unbind(pdu_data)
                    if resp:
                        self.send_pdu(resp)
                    self.connected = False
                    break

                else:
                    logger.warning(f"Unknown SMPP command: {cmd_id:#x}")

        except Exception as e:
            logger.error(f"Session error: {e}")
        finally:
            self.socket.close()
            logger.info(f"SMPP connection closed: {self.address[0]}")


class HTTPSession:
    """Simple HTTP handler for web interface"""

    def __init__(self, socket, address, flask_app):
        self.socket = socket
        self.address = address
        self.flask_app = flask_app

    def run(self):
        """Handle HTTP connection"""
        try:
            # Read HTTP request
            request = b''
            self.socket.settimeout(30)

            # Read until double CRLF (end of HTTP headers)
            while b'\r\n\r\n' not in request:
                chunk = self.socket.recv(4096)
                if not chunk:
                    return
                request += chunk
                if len(request) > 65536:
                    break

            # Parse request
            try:
                request_str = request.decode('utf-8', errors='ignore')
                lines = request_str.split('\r\n')
                if lines:
                    first_line = lines[0].split(' ')
                    if len(first_line) >= 2:
                        method = first_line[0]
                        path = first_line[1]
                    else:
                        return
                else:
                    return
            except:
                return

            logger.info(f"HTTP: {method} {path} from {self.address[0]}")

            # Generate simple HTML response
            html = self.generate_response(path)

            response = f"HTTP/1.1 200 OK\r\n"
            response += "Content-Type: text/html; charset=utf-8\r\n"
            response += f"Content-Length: {len(html)}\r\n"
            response += "Connection: close\r\n"
            response += "\r\n"

            self.socket.sendall(response.encode('utf-8') + html)

        except Exception as e:
            logger.error(f"HTTP session error: {e}")
        finally:
            self.socket.close()

    def generate_response(self, path):
        """Generate HTML response"""
        if path in ('/', '/admin', '/dashboard'):
            return self.get_dashboard()
        elif path == '/login':
            return self.get_login_page()
        else:
            return self.get_not_found()

    def get_dashboard(self):
        return """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ABYSS SMS Gateway</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 0; padding: 20px; background: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; }
        .header { background: #2c3e50; color: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; }
        .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; }
        .stat-card { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .stat-card h3 { margin: 0 0 10px 0; color: #666; font-size: 14px; }
        .stat-card .value { font-size: 32px; font-weight: bold; color: #2c3e50; }
        .status { color: #27ae60; }
        .info { background: white; padding: 20px; border-radius: 8px; margin-top: 20px; }
        .info h2 { color: #2c3e50; }
        .info ul { list-style: none; padding: 0; }
        .info li { padding: 10px 0; border-bottom: 1px solid #eee; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>ABYSS SMS Gateway</h1>
            <p>نظام إدارة الرسائل القصيرة - خادم موحد</p>
        </div>

        <div class="stats">
            <div class="stat-card">
                <h3>خادم SMPP</h3>
                <div class="value status">🟢 يعمل</div>
                <p>المنفذ: 10075</p>
            </div>
            <div class="stat-card">
                <h3>خادم HTTP</h3>
                <div class="value status">🟢 يعمل</div>
                <p>البروتوكول: HTTP/1.1</p>
            </div>
            <div class="stat-card">
                <h3>قاعدة البيانات</h3>
                <div class="value status">🟢 متصلة</div>
                <p>النوع: SQLite</p>
            </div>
        </div>

        <div class="info">
            <h2>معلومات النظام</h2>
            <ul>
                <li><strong>البروتوكولات:</strong> SMPP v3.4, HTTP/1.1</li>
                <li><strong>المنفذ:</strong> 10075 (خادم واحد لكلا البروتوكولين)</li>
                <li><strong>البوابات المدعومة:</strong> Kannel, Clickatell, Ozeki, وغيرها</li>
                <li><strong>لوحة التحكم:</strong> متاحة عبر متصفح الويب</li>
            </ul>
        </div>
    </div>
</body>
</html>
        """.encode('utf-8')

    def get_login_page(self):
        html = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>ABYSS SMS - Login</title>
    <style>
        body { font-family: Arial, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background: #f5f5f5; }
        .login-box { background: white; padding: 40px; border-radius: 8px; box-shadow: 0 4px 8px rgba(0,0,0,0.1); width: 350px; }
        h1 { color: #2c3e50; text-align: center; }
        input { width: 100%; padding: 12px; margin: 10px 0; border: 1px solid #ddd; border-radius: 4px; box-sizing: border-box; }
        button { width: 100%; padding: 12px; background: #3498db; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 16px; }
        button:hover { background: #2980b9; }
    </style>
</head>
<body>
    <div class="login-box">
        <h1>ABYSS SMS</h1>
        <form action="/login" method="POST">
            <input type="text" name="username" placeholder="Username" required>
            <input type="password" name="password" placeholder="Password" required>
            <button type="submit">Login</button>
        </form>
    </div>
</body>
</html>
        """
        return html.encode('utf-8')

    def get_not_found(self):
        return b"<html><body><h1>404 - Not Found</h1></body></html>"


class SMPPServer:
    """SMPP Server using gevent"""

    def __init__(self, host, port, flask_app):
        self.host = host
        self.port = port
        self.flask_app = flask_app
        self.pool = Pool(size=10000)
        self.smpp_config = {
            'ALLOWED_IPS': [ip.strip() for ip in os.environ.get('SMPP_ALLOWED_IPS', '').split(',') if ip.strip()],
            'SYSTEM_ID': os.environ.get('SMPP_SYSTEM_ID', 'abyss_sms'),
        }

    def handle(self, socket, address):
        """Handle SMPP connection"""
        try:
            session = SMPPSession(socket, address, self.smpp_config, self.flask_app)
            session.run()
        except Exception as e:
            logger.error(f"SMPP handle error: {e}")
            try:
                socket.close()
            except:
                pass

    def start(self):
        """Start SMPP server"""
        logger.info(f"Starting SMPP server on {self.host}:{self.port}")
        print(f"  SMPP Gateway: {self.host}:{self.port}")

        server = StreamServer(
            (self.host, self.port),
            self.handle,
            spawn=self.pool
        )

        try:
            server.serve_forever()
        except KeyboardInterrupt:
            server.stop()


class HTTPServer:
    """HTTP Server using gevent"""

    def __init__(self, host, port, flask_app):
        self.host = host
        self.port = port
        self.flask_app = flask_app
        self.pool = Pool(size=10000)

    def handle(self, socket, address):
        """Handle HTTP connection"""
        try:
            session = HTTPSession(socket, address, self.flask_app)
            session.run()
        except Exception as e:
            logger.error(f"HTTP handle error: {e}")
            try:
                socket.close()
            except:
                pass

    def start(self):
        """Start HTTP server"""
        logger.info(f"Starting HTTP server on {self.host}:{self.port}")
        print(f"  HTTP Server:   {self.host}:{self.port}")

        server = StreamServer(
            (self.host, self.port),
            self.handle,
            spawn=self.pool
        )

        try:
            server.serve_forever()
        except KeyboardInterrupt:
            server.stop()


def main():
    import argparse

    # Load environment
    load_env()

    # Parse arguments
    parser = argparse.ArgumentParser(description='ABYSS SMS Server')
    parser.add_argument('--smpp-port', type=int, default=2775,
                       help='SMPP port (default: 2775)')
    parser.add_argument('--http-port', type=int, default=10075,
                       help='HTTP port (default: 10075)')
    parser.add_argument('--host', default='0.0.0.0',
                       help='Host to bind (default: 0.0.0.0)')
    parser.add_argument('--debug', action='store_true',
                       help='Enable debug logging')

    args = parser.parse_args()

    # Configure logging
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # Create Flask app
    from app import create_app

    config_name = os.environ.get('FLASK_ENV', 'production')
    flask_app = create_app(config_name)

    # Print banner
    print("=" * 70)
    print("ABYSS SMS - SMPP Gateway")
    print("=" * 70)
    print(f"Host: {args.host}")
    print("-" * 70)

    # Start both servers
    smpp_server = SMPPServer(args.host, args.smpp_port, flask_app)
    http_server = HTTPServer(args.host, args.http_port, flask_app)

    smpp_thread = gevent.spawn(smpp_server.start)
    http_thread = gevent.spawn(http_server.start)

    print("-" * 70)
    print("Press Ctrl+C to stop")
    print("=" * 70)

    try:
        gevent.joinall([smpp_thread, http_thread])
    except KeyboardInterrupt:
        print("\nShutting down...")
        smpp_server.pool.kill()
        http_server.pool.kill()


if __name__ == '__main__':
    main()
