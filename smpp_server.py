"""
ABYSS SMS - SMPP Gateway Server

Receives SMS from external SMPP clients (like Kannel, SMS gateways)
and routes them to users based on destination phone numbers.

Supported Ports: 2775 (standard SMPP)
Allowed IPs: 167.71.243.250, 143.198.18.203
"""

import os
import sys
import asyncio
import logging
import socket
import threading
from datetime import datetime
from typing import Dict, Optional, Tuple
import json

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('smpp_server.log')
    ]
)
logger = logging.getLogger('smpp_server')

# Import database models
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import create_app, db
from app.models.user import User
from app.models.sms import SMSNumber, SMDRange, SMSCDR
from app.models.activity import ActivityLog


class SMPPServerConfig:
    """SMPP Server Configuration"""
    # Server settings
    HOST = os.environ.get('SMPP_HOST', '0.0.0.0')
    PORT = int(os.environ.get('SMPP_PORT', 2775))

    # Allowed IPs for SMPP connections
    ALLOWED_IPS = os.environ.get('SMPP_ALLOWED_IPS', '167.71.243.250,143.198.18.203').split(',')
    ALLOWED_IPS = [ip.strip() for ip in ALLOWED_IPS if ip.strip()]

    # Bind settings
    SYSTEM_ID = os.environ.get('SMPP_SYSTEM_ID', 'abyss_sms')
    PASSWORD = os.environ.get('SMPP_PASSWORD', '')
    SYSTEM_TYPE = os.environ.get('SMPP_SYSTEM_TYPE', 'SMSGW')
    VERSION = 0x34  # SMPP v3.4

    # Timing
    CONNECT_TIMEOUT = 30
    IDLE_TIMEOUT = 60

    # Message limits
    MAX_MESSAGE_LENGTH = 160
    MAX_SEGMENTS = 10

    # Logging
    LOG_MESSAGES = os.environ.get('SMPP_LOG_MESSAGES', 'true').lower() == 'true'


class SMPPCommand:
    """SMPP Command IDs"""
    BIND_TRANSMITTER = 0x00000001
    BIND_TRANSMITTER_RESP = 0x80000001
    BIND_RECEIVER = 0x00000002
    BIND_RECEIVER_RESP = 0x80000002
    BIND_TRANSCEIVER = 0x00000009
    BIND_TRANSCEIVER_RESP = 0x80000009
    UNBIND = 0x00000006
    UNBIND_RESP = 0x80000006
    SUBMIT_SM = 0x00000004
    SUBMIT_SM_RESP = 0x80000004
    DELIVER_SM = 0x00000005
    DELIVER_SM_RESP = 0x80000005
    ENQUIRE_LINK = 0x00000015
    ENQUIRE_LINK_RESP = 0x80000015
    GENERIC_NACK = 0x80000000


class SMPPStatus:
    """SMPP Command Statuses"""
    ESME_ROK = 0x00000000  # No error
    ESME_RBINDFAIL = 0x0000000D  # Bind failed
    ESME_RINVPASWD = 0x0000000E  # Invalid password
    ESME_RINVSYSID = 0x0000000F  # Invalid system id
    ESME_RSYSERR = 0x00000063  # System error


class SMPPPDU:
    """SMPP Protocol Data Unit"""

    def __init__(self, command_id: int = 0, command_status: int = 0, sequence: int = 0):
        self.command_id = command_id
        self.command_status = command_status
        self.sequence = sequence
        self.body = b''

    @property
    def command_length(self) -> int:
        return 16 + len(self.body)

    def to_bytes(self) -> bytes:
        """Serialize PDU to bytes"""
        result = bytearray()
        # Command length (4 bytes, big-endian)
        result.extend(self.command_length.to_bytes(4, 'big'))
        # Command ID (4 bytes)
        result.extend(self.command_id.to_bytes(4, 'big'))
        # Command status (4 bytes)
        result.extend(self.command_status.to_bytes(4, 'big'))
        # Sequence number (4 bytes)
        result.extend(self.sequence.to_bytes(4, 'big'))
        # Body
        result.extend(self.body)
        return bytes(result)

    @classmethod
    def from_bytes(cls, data: bytes) -> 'SMPPPDU':
        """Parse PDU from bytes"""
        if len(data) < 16:
            return None

        pdu = cls()
        pdu.command_id = int.from_bytes(data[0:4], 'big')
        pdu.command_status = int.from_bytes(data[4:8], 'big')
        pdu.sequence = int.from_bytes(data[8:12], 'big')
        pdu.body = data[16:]
        return pdu


def unpack_cstring(data: bytes, offset: int = 0) -> Tuple[str, int]:
    """Unpack a C-style null-terminated string"""
    end = data.find(b'\x00', offset)
    if end == -1:
        return '', len(data)
    return data[offset:end].decode('latin-1'), end + 1


def pack_cstring(s: str) -> bytes:
    """Pack a string as C-style null-terminated"""
    return s.encode('latin-1') + b'\x00'


class SMPPSession:
    """Represents an SMPP session with a client"""

    def __init__(self, client_socket: socket.socket, address: Tuple[str, int], config: SMPPServerConfig):
        self.socket = client_socket
        self.address = address
        self.config = config
        self.connected = False
        self.authenticated = False
        self.system_id = ''
        self.sequence = 0
        self.last_activity = datetime.utcnow()
        self.app = None

    def send_pdu(self, pdu: SMPPPDU):
        """Send a PDU to the client"""
        try:
            data = pdu.to_bytes()
            self.socket.sendall(data)
            logger.debug(f"Sent PDU: command_id={pdu.command_id:#x}, seq={pdu.sequence}")
        except Exception as e:
            logger.error(f"Failed to send PDU: {e}")
            raise

    def receive_pdu(self) -> Optional[SMPPPDU]:
        """Receive a PDU from the client"""
        try:
            # Read header (16 bytes)
            header = b''
            while len(header) < 16:
                chunk = self.socket.recv(16 - len(header))
                if not chunk:
                    return None
                header += chunk

            # Get command length
            cmd_length = int.from_bytes(header[0:4], 'big')

            # Read rest of PDU
            body_length = cmd_length - 16
            body = b''
            while len(body) < body_length:
                chunk = self.socket.recv(body_length - len(body))
                if not chunk:
                    return None
                body += chunk

            pdu = SMPPPDU.from_bytes(header + body)
            self.last_activity = datetime.utcnow()
            return pdu
        except Exception as e:
            logger.error(f"Failed to receive PDU: {e}")
            return None

    def create_response(self, command_id: int, status: int, sequence: int, body: bytes = b'') -> SMPPPDU:
        """Create a response PDU"""
        resp = SMPPPDU(command_id, status, sequence)
        resp.body = body
        return resp

    def handle_bind(self, pdu: SMPPPDU, bind_type: str) -> bool:
        """Handle bind request"""
        offset = 0

        # Parse bind request body
        system_id, offset = unpack_cstring(pdu.body, offset)
        password, offset = unpack_cstring(pdu.body, offset)
        system_type, offset = unpack_cstring(pdu.body, offset)
        interface_version = pdu.body[offset] if offset < len(pdu.body) else 0x34
        offset += 1
        addr_ton = pdu.body[offset] if offset < len(pdu.body) else 0
        offset += 1
        addr_npi = pdu.body[offset] if offset < len(pdu.body) else 0
        offset += 1
        address_range, _ = unpack_cstring(pdu.body, offset)

        logger.info(f"Bind request: type={bind_type}, system_id={system_id}, from={self.address[0]}")

        # Check if IP is allowed
        if self.address[0] not in self.config.ALLOWED_IPS:
            logger.warning(f"Connection rejected: IP {self.address[0]} not in allowed list")
            resp = self.create_response(
                pdu.command_id + 0x80000000,
                SMPPStatus.ESME_RBINDFAIL,
                pdu.sequence,
                pack_cstring('')
            )
            self.send_pdu(resp)
            return False

        # Validate credentials - check against database
        valid, user = self.validate_credentials(system_id, password)

        if not valid:
            logger.warning(f"Authentication failed for system_id={system_id}")
            resp = self.create_response(
                pdu.command_id + 0x80000000,
                SMPPStatus.ESME_RINVPASWD,
                pdu.sequence,
                pack_cstring('')
            )
            self.send_pdu(resp)
            return False

        self.authenticated = True
        self.system_id = system_id
        self.user = user

        logger.info(f"User {system_id} authenticated successfully")

        # Send success response
        resp_body = pack_cstring(self.config.SYSTEM_ID)
        resp_body += pack_cstring('')  # sc_interface_version
        resp = self.create_response(
            pdu.command_id + 0x80000000,
            SMPPStatus.ESME_ROK,
            pdu.sequence,
            resp_body
        )
        self.send_pdu(resp)
        return True

    def validate_credentials(self, system_id: str, password: str) -> Tuple[bool, Optional[User]]:
        """Validate SMPP credentials against database"""
        try:
            if self.app is None:
                self.app = create_app('development')

            with self.app.app_context():
                # Find user by system_id (username or api_token)
                user = User.query.filter(
                    (User.username == system_id) | (User.api_token == system_id)
                ).first()

                if not user:
                    return False, None

                # Verify password
                if not user.check_password(password):
                    return False, None

                # Check if user is active
                if not user.is_active:
                    return False, None

                # Check if user has permission to send SMS
                if user.role and user.role.name in ('admin', 'agent', 'client'):
                    return True, user

                return False, None
        except Exception as e:
            logger.error(f"Database error during authentication: {e}")
            return False, None

    def handle_submit_sm(self, pdu: SMPPPDU) -> Tuple[int, str]:
        """Handle submit_sm request (receive SMS from client)"""
        offset = 0

        # Parse submit_sm body
        service_type, offset = unpack_cstring(pdu.body, offset)
        source_addr_ton = pdu.body[offset] if offset < len(pdu.body) else 0
        offset += 1
        source_addr_npi = pdu.body[offset] if offset < len(pdu.body) else 0
        offset += 1
        source_addr, offset = unpack_cstring(pdu.body, offset)
        dest_addr_ton = pdu.body[offset] if offset < len(pdu.body) else 0
        offset += 1
        dest_addr_npi = pdu.body[offset] if offset < len(pdu.body) else 0
        offset += 1
        destination_addr, offset = unpack_cstring(pdu.body, offset)
        esm_class = pdu.body[offset] if offset < len(pdu.body) else 0
        offset += 1
        protocol_id = pdu.body[offset] if offset < len(pdu.body) else 0
        offset += 1
        priority_flag = pdu.body[offset] if offset < len(pdu.body) else 0
        offset += 1
        schedule_delivery_time, offset = unpack_cstring(pdu.body, offset)
        validity_period, offset = unpack_cstring(pdu.body, offset)
        registered_delivery = pdu.body[offset] if offset < len(pdu.body) else 0
        offset += 1
        replace_if_present = pdu.body[offset] if offset < len(pdu.body) else 0
        offset += 1
        data_coding = pdu.body[offset] if offset < len(pdu.body) else 0
        offset += 1
        sm_default_msg_id = pdu.body[offset] if offset < len(pdu.body) else 0
        offset += 1
        sm_length = pdu.body[offset] if offset < len(pdu.body) else 0
        offset += 1
        short_message = pdu.body[offset:offset + sm_length]

        # Decode message
        try:
            if data_coding == 0x08:  # UCS-2
                message_text = short_message.decode('utf-16-be')
            elif data_coding == 0x00 or data_coding == 0x03:  # ASCII / IA5
                message_text = short_message.decode('ascii')
            else:  # Default to latin-1
                message_text = short_message.decode('latin-1')
        except:
            message_text = short_message.decode('latin-1', errors='replace')

        # Clean phone numbers
        source_addr = ''.join(c for c in source_addr if c.isdigit())
        destination_addr = ''.join(c for c in destination_addr if c.isdigit())

        logger.info(f"Received SMS: from={source_addr}, to={destination_addr}, msg={message_text[:50]}")

        if self.config.LOG_MESSAGES:
            self.log_sms(
                source_addr=source_addr,
                destination=destination_addr,
                message=message_text,
                cli=source_addr
            )

        # Return success with message ID
        message_id = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}{pdu.sequence}"
        resp_body = pack_cstring(message_id)
        return SMPPStatus.ESME_ROK, resp_body

    def log_sms(self, source_addr: str, destination: str, message: str, cli: str):
        """Log SMS to database and calculate payouts"""
        try:
            if self.app is None:
                self.app = create_app('development')

            with self.app.app_context():
                # Find the SMS number (source) in database
                sms_number = SMSNumber.query.filter_by(number=source_addr).first()

                if not sms_number:
                    logger.warning(f"SMS number {source_addr} not found in database")
                    return

                # Determine the owner of the SMS number
                owner = None
                if sms_number.agent_id:
                    owner = User.query.get(sms_number.agent_id)
                elif sms_number.client_id:
                    owner = User.query.get(sms_number.client_id)

                if not owner:
                    logger.warning(f"No owner found for SMS number {source_addr}")
                    return

                # Get the range info for rates
                sms_range = sms_number.sms_range if sms_number.range_id else None

                # Calculate payouts
                rate = sms_range.rate if sms_range else 0.005
                agent_payout = sms_number.agent_payout if sms_number.agent_payout else rate
                client_payout = sms_number.client_payout if sms_number.client_payout else rate * 0.5
                profit = rate - agent_payout

                # Create CDR record
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

                # Update user's SMS count
                owner.sms_count = (owner.sms_count or 0) + 1

                # Credit the agent's balance
                if sms_number.agent_id:
                    agent = User.query.get(sms_number.agent_id)
                    if agent:
                        agent.balance = (agent.balance or 0) + agent_payout
                        agent.total_earned = (agent.total_earned or 0) + agent_payout

                # Credit the client's balance
                if sms_number.client_id:
                    client = User.query.get(sms_number.client_id)
                    if client:
                        client.balance = (client.balance or 0) + client_payout
                        client.total_earned = (client.total_earned or 0) + client_payout

                db.session.commit()

                logger.info(f"SMS logged: CDR={cdr.id}, payout=${agent_payout:.4f}")

                # Log activity
                ActivityLog.log(
                    user_id=owner.id,
                    action='sms_received',
                    details=f"SMS from {source_addr} to {destination}",
                    ip_address=self.address[0]
                )

        except Exception as e:
            logger.error(f"Failed to log SMS: {e}")
            if self.app:
                with self.app.app_context():
                    db.session.rollback()

    def handle_enquire_link(self, pdu: SMPPPDU):
        """Handle enquire_link request"""
        resp = self.create_response(
            SMPPCommand.ENQUIRE_LINK_RESP,
            SMPPStatus.ESME_ROK,
            pdu.sequence
        )
        self.send_pdu(resp)

    def handle_unbind(self, pdu: SMPPPDU):
        """Handle unbind request"""
        resp = self.create_response(
            SMPPCommand.UNBIND_RESP,
            SMPPStatus.ESME_ROK,
            pdu.sequence
        )
        self.send_pdu(resp)
        self.connected = False
        logger.info(f"Client {self.system_id} unbound")

    def run(self):
        """Main session handler"""
        logger.info(f"New connection from {self.address[0]}:{self.address[1]}")

        try:
            while self.connected or not self.authenticated:
                pdu = self.receive_pdu()
                if pdu is None:
                    break

                if pdu.command_id == SMPPCommand.BIND_TRANSMITTER:
                    self.connected = True
                    if not self.handle_bind(pdu, 'transmitter'):
                        break

                elif pdu.command_id == SMPPCommand.BIND_RECEIVER:
                    self.connected = True
                    if not self.handle_bind(pdu, 'receiver'):
                        break

                elif pdu.command_id == SMPPCommand.BIND_TRANSCEIVER:
                    self.connected = True
                    if not self.handle_bind(pdu, 'transceiver'):
                        break

                elif pdu.command_id == SMPPCommand.SUBMIT_SM:
                    if not self.authenticated:
                        self.send_error(SMPPStatus.ESME_RSYSERR, pdu.sequence)
                        continue
                    status, body = self.handle_submit_sm(pdu)
                    resp = self.create_response(
                        SMPPCommand.SUBMIT_SM_RESP,
                        status,
                        pdu.sequence,
                        body
                    )
                    self.send_pdu(resp)

                elif pdu.command_id == SMPPCommand.ENQUIRE_LINK:
                    if self.authenticated:
                        self.handle_enquire_link(pdu)

                elif pdu.command_id == SMPPCommand.UNBIND:
                    self.handle_unbind(pdu)
                    break

                else:
                    # Unknown command - send NACK
                    logger.warning(f"Unknown command: {pdu.command_id:#x}")
                    if self.authenticated:
                        resp = self.create_response(
                            SMPPCommand.GENERIC_NACK,
                            SMPPStatus.ESME_ROK,
                            pdu.sequence
                        )
                        self.send_pdu(resp)

        except Exception as e:
            logger.error(f"Session error: {e}")
        finally:
            self.socket.close()
            logger.info(f"Connection closed: {self.address[0]}")


def handle_client(client_socket: socket.socket, address: Tuple[str, int], config: SMPPServerConfig):
    """Handle a client connection in a separate thread"""
    session = SMPPSession(client_socket, address, config)
    try:
        session.run()
    except Exception as e:
        logger.error(f"Client handler error: {e}")
    finally:
        try:
            client_socket.close()
        except:
            pass


def start_smpp_server(config: SMPPServerConfig = None):
    """Start the SMPP server"""
    if config is None:
        config = SMPPServerConfig()

    logger.info(f"=" * 60)
    logger.info("ABYSS SMS - SMPP Gateway Server")
    logger.info(f"=" * 60)
    logger.info(f"Host: {config.HOST}")
    logger.info(f"Port: {config.PORT}")
    logger.info(f"Allowed IPs: {', '.join(config.ALLOWED_IPS)}")
    logger.info(f"System ID: {config.SYSTEM_ID}")
    logger.info(f"=" * 60)

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        server_socket.bind((config.HOST, config.PORT))
        server_socket.listen(5)
        server_socket.settimeout(1)  # Allow periodic checks

        logger.info(f"SMPP Server listening on {config.HOST}:{config.PORT}")

        while True:
            try:
                client_socket, address = server_socket.accept()
                # Start a new thread for the client
                thread = threading.Thread(
                    target=handle_client,
                    args=(client_socket, address, config),
                    daemon=True
                )
                thread.start()
            except socket.timeout:
                continue
            except KeyboardInterrupt:
                logger.info("Shutting down SMPP server...")
                break
            except Exception as e:
                logger.error(f"Accept error: {e}")
                continue

    except Exception as e:
        logger.error(f"Server error: {e}")
    finally:
        server_socket.close()
        logger.info("SMPP Server stopped")


def start_smpp_server_async():
    """Start SMPP server with asyncio (for integration with Flask)"""
    import asyncio
    from functools import partial

    config = SMPPServerConfig()

    async def handle_client_async(client_reader, client_writer, config):
        address = client_writer.get_extra_info('peername')
        logger.info(f"New connection from {address[0]}:{address[1]}")

        session = SMPPSession(
            type('ClientSocket', (), {
                'socket': client_writer,
                'address': address,
                'recv': partial(client_reader.read, 4096),
                'send': client_writer.drain
            })(),
            address,
            config
        )

        try:
            await session.run_async()
        except Exception as e:
            logger.error(f"Session error: {e}")
        finally:
            client_writer.close()
            logger.info(f"Connection closed: {address[0]}")

    async def main():
        config = SMPPServerConfig()
        server = await asyncio.start_server(
            partial(handle_client_async, config=config),
            config.HOST,
            config.PORT
        )

        logger.info(f"SMPP Server listening on {config.HOST}:{config.PORT}")

        async with server:
            await server.serve_forever()

    return main()


if __name__ == '__main__':
    start_smpp_server()
