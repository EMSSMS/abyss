# ABYSS SMS - SMPP Gateway Setup Guide

## Overview

The SMPP Gateway allows external SMS gateways (like Kannel, Clickatell, etc.) to connect to your ABYSS SMS platform and send/receive SMS messages.

**Problem Solved**: Your server was receiving HTTP 400 errors because external gateways were sending SMPP protocol traffic to an HTTP port.

## Architecture

```
┌─────────────────┐     SMPP (port 2775)     ┌─────────────────┐
│  SMS Gateway    │ ──────────────────────► │  SMPP Server     │
│  167.71.243.250 │                         │  (New Service)   │
│  143.198.18.203 │ ◄────────────────────── │                 │
└─────────────────┘     Bind Response      └────────┬────────┘
                                                    │
                                           ┌────────▼────────┐
                                           │  Database       │
                                           │  - Users        │
                                           │  - SMS Numbers  │
                                           │  - CDR Records  │
                                           │  - Payouts     │
                                           └─────────────────┘
```

## Setup Instructions

### 1. Install Dependencies

Make sure you have the required packages:

```bash
pip install flask flask-sqlalchemy flask-login flask-bcrypt python-dotenv
```

### 2. Configure Environment

Copy the example environment file:

```bash
cp .env.smpp .env
```

Edit `.env` with your settings:

```env
SMPP_HOST=0.0.0.0
SMPP_PORT=2775
SMPP_ALLOWED_IPS=167.71.243.250,143.198.18.203
SMPP_SYSTEM_ID=abyss_sms
DATABASE_URL=sqlite:///abyss_sms.db
SECRET_KEY=your-secret-key-here
```

### 3. Run the SMPP Server

Start the SMPP server:

```bash
# Basic usage
python run_smpp.py

# Custom port
python run_smpp.py --port 2775

# Debug mode
python run_smpp.py --debug
```

### 4. Verify Server is Running

Check the logs:

```bash
tail -f smpp_server.log
```

You should see:

```
INFO:smpp_server:ABYSS SMS - SMPP Gateway Server
INFO:smpp_server:Host: 0.0.0.0
INFO:smpp_server:Port: 2775
INFO:smpp_server:Allowed IPs: 167.71.243.250, 143.198.18.203
```

## Running with Systemd (Production)

Create a systemd service file:

```bash
sudo nano /etc/systemd/system/abyss-smpp.service
```

Add the following content:

```ini
[Unit]
Description=ABYSS SMS SMPP Gateway
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/path/to/your/app
ExecStart=/usr/bin/python3 run_smpp.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable abyss-smpp
sudo systemctl start abyss-smpp
```

Check status:

```bash
sudo systemctl status abyss-smpp
```

## SMPP Client Configuration

### Example: Kannel Configuration

```conf
group = smsc
smsc = smpp
smsc-id = abyss_sms
host = YOUR_SERVER_IP
port = 2775
receive-port = 2775
smsc-username = your_username
smsc-password = your_password
transceiver-mode = true
```

### Example: Ozeki NG Configuration

```
SMS Center Address: YOUR_SERVER_IP
SMS Center Port: 2775
Protocol: SMPP v3.4
Username: your_username
Password: your_password
```

## How It Works

### 1. Connection & Authentication

1. Client connects to SMPP port 2775
2. Server checks if IP is in allowed list
3. Server validates system_id and password against database
4. On success, binds as transmitter/receiver/transceiver

### 2. Message Handling

1. Client sends `SUBMIT_SM` PDU with message
2. Server parses source and destination numbers
3. Server looks up source number in database
4. Server credits the appropriate user (agent/client)

### 3. Payout Calculation

For each SMS received:

```
Rate (from SMS Range)    = $0.005
Agent Payout             = agent_payout from SMS Number
Client Payout            = client_payout from SMS Number
Profit                    = Rate - Agent Payout
```

Example:
- SMS Rate: $0.005
- Agent Payout: $0.003
- Client Payout: $0.001
- Platform Profit: $0.001

### 4. CDR Logging

All SMS messages are logged to `sms_cdr` table with:
- Source and destination numbers
- Message content
- Payout amounts
- User assignments

## Managing Users for SMPP

### Create SMPP User

1. Login to admin panel
2. Go to Users → Add User
3. Set role (agent or client)
4. Assign SMS numbers

### User Authentication

SMPP users authenticate using:
- **Username** (from users table)
- **Password** (from users table)

### Assign Numbers to User

```python
from app import create_app, db
from app.models.user import User
from app.models.sms import SMSNumber

app = create_app()
with app.app_context():
    # Find user
    user = User.query.filter_by(username='agent1').first()

    # Find available numbers
    number = SMSNumber.query.filter_by(agent_id=None).first()

    # Assign to agent
    number.agent_id = user.id
    number.agent_payout = 0.003  # $0.003 per SMS
    db.session.commit()
```

## Troubleshooting

### Connection Refused

```bash
# Check if port is open
netstat -tlnp | grep 2775

# Check firewall
sudo ufw allow 2775/tcp
```

### Authentication Failed

1. Check username/password in database
2. Verify user is active (`is_active = True`)
3. Check user role (must be admin/agent/client)

### Messages Not Being Logged

1. Verify SMS numbers exist in `sms_numbers` table
2. Check database permissions
3. Enable debug logging

### High CPU Usage

- Reduce `ALLOWED_IPS` to only necessary IPs
- Enable connection timeouts
- Monitor with `htop`

## Monitoring

### View Active Connections

```bash
# Check SMPP log
tail -f smpp_server.log

# Count connections
grep "authenticated successfully" smpp_server.log | wc -l
```

### View Message Statistics

```sql
-- Total SMS today
SELECT COUNT(*) FROM sms_cdr WHERE DATE(created_at) = CURDATE();

-- Total SMS by user
SELECT u.username, COUNT(*) as total
FROM sms_cdr c
JOIN users u ON c.user_id = u.id
GROUP BY u.username;
```

## Security

### IP Whitelist

Only IPs in `SMPP_ALLOWED_IPS` can connect:

```env
SMPP_ALLOWED_IPS=167.71.243.250,143.198.18.203
```

### Firewall Rules

```bash
# Allow only SMPP clients
sudo ufw deny 2775/tcp
sudo ufw allow from 167.71.243.250 to any port 2775
sudo ufw allow from 143.198.18.203 to any port 2775
```

### TLS/SSL

For production, consider:
- Running behind VPN
- Using stunnel for encryption
- Implementing SMPP over TLS

## Running Both HTTP and SMPP

Start both services:

```bash
# Terminal 1: HTTP API
python run.py

# Terminal 2: SMPP Gateway
python run_smpp.py
```

Or create a combined runner:

```python
import threading
from run import create_app
from smpp_server import start_smpp_server, SMPPServerConfig

def run_http():
    app = create_app()
    app.run(host='0.0.0.0', port=5977)

def run_smpp():
    start_smpp_server(SMPPServerConfig())

if __name__ == '__main__':
    http_thread = threading.Thread(target=run_http)
    smpp_thread = threading.Thread(target=run_smpp)

    http_thread.start()
    smpp_thread.start()

    http_thread.join()
    smpp_thread.join()
```

## API Reference

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SMPP_HOST` | 0.0.0.0 | Bind address |
| `SMPP_PORT` | 2775 | Listening port |
| `SMPP_ALLOWED_IPS` | * | Comma-separated allowed IPs |
| `SMPP_SYSTEM_ID` | abyss_sms | System identifier |
| `SMPP_LOG_MESSAGES` | true | Log all SMS |

### Supported SMPP Commands

- `BIND_TRANSMITTER` - Send SMS
- `BIND_RECEIVER` - Receive SMS
- `BIND_TRANSCEIVER` - Both directions
- `SUBMIT_SM` - Submit SMS
- `ENQUIRE_LINK` - Keepalive
- `UNBIND` - Close connection

## Support

For issues:
1. Check `smpp_server.log` for errors
2. Enable debug mode: `python run_smpp.py --debug`
3. Verify database connection
4. Check firewall rules
