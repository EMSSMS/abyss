# 🔧 إصلاح المشكلة - شرح واضح

## ❌ المشكلة

في الصورة، الإعدادات عندك:
```
Host: 93.177.64.145
Port: 5977      ← ❌ غلط! دا HTTP port
```

## ✅ الحل

غيّر الإعدادات كده:
```
Host: 93.177.64.145
Port: 2775      ← ✅ SMPP port
```

---

## 📋 شرح المشاكل

### 1. Port غلط
```
Port 5977 = HTTP Server (Flask)
Port 2775 = SMPP Server (SMS Gateway)
```

### 2. ERR_EMPTY_RESPONSE
```
السيرفر HTTP (Flask) مش شغال على 5977
```

---

## 🔄 إزاي تشغل السيرفر

### على السيرفر، شغّل:

```bash
cd /path/to/abyss_sms

# شغّل SMPP Server
python run_smpp.py --port 2775
```

### أو شغّل الاتنين معاً:

```bash
python run_all.py
```

### شوف اللogs:

```bash
tail -f smpp_server.log
```

**المتوقع في اللogs:**
```
INFO:smpp_server:ABYSS SMS - SMPP Gateway Server
INFO:smpp_server:SMPP Server listening on 0.0.0.0:2775
```

---

## 📱 إعدادات الـ Host عندك (غالباً Ozeki أو similar)

### قبل (غلط):
```
Name: croco
Host: 93.177.64.145
Port: 5977        ❌
System ID: croco
Password: Croco123
```

### بعد (صح):
```
Name: croco
Host: 93.177.64.145
Port: 2775        ✅
System ID: croco
Password: Croco123
```

---

## 🔍 لو لسه في مشكلة

### 1. تأكد إن SMPP Server شغال:

```bash
netstat -tlnp | grep 2775
```

**النتيجة المتوقعة:**
```
tcp        0      0 0.0.0.0:2775            0.0.0.0:*               LISTEN      python
```

### 2. تأكد من الـ Firewall:

```bash
# على السيرفر
sudo ufw allow 2775/tcp

# أو
sudo iptables -A INPUT -p tcp --dport 2775 -j ACCEPT
```

### 3. شوف اللogs:

```bash
tail -f smpp_server.log
```

### 4. لو لسه Connection Failed:

```
Last error: Connection failed at 2026-04-14 00:12:19
```

معناه إن الاتصال مش واصل أصلاً. تأكد:
- [ ] SMPP Server شغال؟
- [ ] البورت 2775؟
- [ ] الـ IP صحيح؟
- [ ] الـ Firewall مفتوح؟

---

## 🚀 تشغيل سريع

```bash
# 1. على السيرفر
cd /home/user/abyss_sms

# 2. شغّل SMPP
python run_smpp.py &

# 3. شوف اللogs
tail -f smpp_server.log

# 4. متوقع تشوف:
# SMPP Server listening on 0.0.0.0:2775
```

### 5. على الـ Dashboard عندك:
```
Host: 93.177.64.145
Port: 2775
System ID: croco
Password: Croco123
```

---

## ✅ النتيجة المطلوبة

بعد ما تظبط الإعدادات:

```
✅ Connection successful!
✅ Last connected: 2026-04-14 ...
```

وفي اللogs هتلاقي:
```
INFO:smpp_server:Bind request: type=transceiver, system_id=croco, from=93.177.64.145
INFO:smpp_server:User croco authenticated successfully
```
