# ABYSS SMS - Unified Server Guide
# دليل خادم ABYSS الموحد (HTTP + SMPP على منفذ واحد)

## Overview | نظرة عامة

هذا الخادم يجمع **HTTP** و **SMPP** على **منفذ واحد فقط**، مما يتيح:
- استقبال رسائل SMS من البوابات الخارجية عبر بروتوكول SMPP
- الوصول إلى لوحة التحكم عبر متصفح الويب
- لا حاجة لفتح منافذ متعددة

## Quick Start | البدء السريع

### 1. تثبيت التبعيات

```bash
pip install -r requirements.txt
```

### 2. إعداد ملف البيئة

```bash
# انسخ ملف البيئة
cp .env.smpp .env

# أو أنشئ ملف جديد
cat > .env << 'EOF'
# إعدادات الخادم الموحد
SMPP_HOST=0.0.0.0
SMPP_PORT=2775
SMPP_ALLOWED_IPS=167.71.243.250,143.198.18.203
SMPP_SYSTEM_ID=abyss_sms
SMPP_LOG_MESSAGES=true

# إعدادات قاعدة البيانات
DATABASE_URL=sqlite:///abyss_sms.db

# إعدادات Flask
SECRET_KEY=your-secret-key-here
FLASK_ENV=production

# إعدادات المسؤول
ADMIN_PASSWORD=YourSecurePassword
EOF
```

### 3. تشغيل الخادم

```bash
# التشغيل العادي
python run_unified.py

# مع التصحيح
python run_unified.py --debug

# مع منفذ مخصص
python run_unified.py --port 2775
```

## Installation | التثبيت

### Linux (Systemd)

```bash
# نسخ ملفات التطبيق
sudo cp -r . /opt/abyss-sms
cd /opt/abyss-sms

# نسخ ملف الخدمة
sudo cp abyss-unified.service /etc/systemd/system/

# إعادة تحميل systemd
sudo systemctl daemon-reload

# تفعيل وتشغيل
sudo systemctl enable abyss-unified
sudo systemctl start abyss-unified

# فحص الحالة
sudo systemctl status abyss-unified
```

### Firewall | الجدار الناري

```bash
# فتح المنفذ الواحد فقط
sudo ufw allow 2775/tcp

# أو لعنوان IP محدد
sudo ufw allow from 167.71.243.250 to any port 2775
sudo ufw allow from 143.198.18.203 to any port 2775
```

## Usage | الاستخدام

### بوابات SMS الخارجية (SMPP)

```conf
# مثال Kannel
group = smsc
smsc = smpp
smsc-id = abyss_sms
host = YOUR_SERVER_IP
port = 2775
smsc-username = your_username
smsc-password = your_password
transceiver-mode = true
```

### لوحة التحكم (HTTP)

افتح المتصفح وانتقل إلى:
```
http://YOUR_SERVER_IP:2775/
```

## How It Works | كيف يعمل

```
┌─────────────────────────────────────────────────────────────┐
│                    Unified Server                           │
│                     Port: 2775                               │
│                                                             │
│  ┌───────────────┐    ┌───────────────┐                     │
│  │    HTTP       │    │    SMPP       │                     │
│  │   Handler     │    │   Handler     │                     │
│  └───────┬───────┘    └───────┬───────┘                     │
│          │                    │                              │
│          │    ┌────────────────┘                              │
│          │    │                                               │
│          ▼    ▼                                               │
│  ┌─────────────────────────────────┐                         │
│  │      Protocol Detector          │                         │
│  │   (SMPP vs HTTP detection)      │                         │
│  └─────────────────────────────────┘                         │
│                    │                                         │
│                    ▼                                         │
│  ┌─────────────────────────────────┐                        │
│  │       Flask App Context          │                        │
│  │    (Database, Auth, etc.)        │                        │
│  └─────────────────────────────────┘                        │
└─────────────────────────────────────────────────────────────┘
```

## Protocol Detection | اكتشاف البروتوكول

الخادم يكتشف تلقائياً نوع الاتصال:

| الإشارة | البروتوكول |
|---------|-----------|
| يبدأ بـ `GET /` أو `POST /` | HTTP |
| 16 bytes header مع command_length و command_id صالح | SMPP |

## Configuration | الإعدادات

### Environment Variables | متغيرات البيئة

| المتغير | الافتراضي | الوصف |
|---------|----------|-------|
| `SMPP_PORT` | 2775 | المنفذ الاستماع |
| `SMPP_HOST` | 0.0.0.0 | عنوان الربط |
| `SMPP_ALLOWED_IPS` | * | عناوين IP المسموحة (مفصولة بفواصل) |
| `SMPP_SYSTEM_ID` | abyss_sms | معرف النظام |
| `SMPP_LOG_MESSAGES` | true | تسجيل الرسائل |

### Allowed IPs Example | مثال عناوين IP المسموحة

```env
SMPP_ALLOWED_IPS=167.71.243.250,143.198.18.203,192.168.1.100
```

## Troubleshooting | استكشاف الأخطاء

### Connection refused | الاتصال مرفوض

```bash
# تحقق من أن المنفذ مفتوح
sudo ufw status
sudo netstat -tlnp | grep 2775
```

### SMPP authentication failed | فشل المصادقة

1. تأكد من اسم المستخدم وكلمة المرور
2. تأكد من أن IP الخاص بالبوابة في القائمة المسموحة
3. تحقق من أن المستخدم نشط في قاعدة البيانات

###查看 Logs | عرض السجلات

```bash
# سجلات systemd
sudo journalctl -u abyss-unified -f

# أو سجل التطبيق
tail -f smpp_server.log
```

## Security | الأمان

### IP Whitelist | قائمة IP المسموحة

**مهم جداً**: حدد فقط عناوين IP الموثوقة في `SMPP_ALLOWED_IPS`.

### Firewall Rules | قواعد الجدار الناري

```bash
# السماح فقط لـ IPs محددة
sudo ufw delete allow 2775/tcp
sudo ufw allow from 167.71.243.250 to any port 2775
sudo ufw allow from 143.198.18.203 to any port 2775
```

## Docker Deployment | النشر بـ Docker

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 2775

CMD ["python", "run_unified.py", "--port", "2775"]
```

```yaml
# docker-compose.yml
version: '3.8'
services:
  abyss-sms:
    build: .
    ports:
      - "2775:2775"
    environment:
      - SMPP_ALLOWED_IPS=167.71.243.250
      - DATABASE_URL=sqlite:///abyss_sms.db
    volumes:
      - ./data:/app/data
```

## Migration | الانتقال

### من الخادم المنفصل إلى الموحد

```bash
# 1. أوقف الخوادم القديمة
sudo systemctl stop abyss-http
sudo systemctl stop abyss-smpp

# 2. احفظ قاعدة البيانات
cp abyss_sms.db abyss_sms.db.backup

# 3. ثبت الخادم الموحد
cp run_unified.py /opt/abyss-sms/

# 4. شغل الخادم الموحد
sudo systemctl restart abyss-unified
```

## Support | الدعم

للأخطاء أو المشاكل:
1. تحقق من السجلات: `sudo journalctl -u abyss-unified -n 100`
2. فعّل التصحيح: `python run_unified.py --debug`
3. تحقق من اتصال قاعدة البيانات
