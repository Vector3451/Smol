import socket
import subprocess
import os

def check_port(ip, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2)
    try:
        s.connect((ip, port))
        return True
    except:
        return False

print("--- NAS DIAGNOSTIC ---")
# 1. Check if app is listening on 8080
if check_port('127.0.0.1', 8080):
    print("[SUCCESS] NAS is listening on port 8080 locally.")
else:
    print("[FAILURE] NAS is NOT listening on port 8080. The service probably crashed.")

# 2. Check for pyOpenSSL
try:
    import OpenSSL
    print("[SUCCESS] pyOpenSSL is installed.")
except ImportError:
    print("[FAILURE] pyOpenSSL is MISSING. Run: sudo apt-get install python3-openssl")

# 3. Check for app errors
print("\n--- Recent Service Logs ---")
try:
    logs = subprocess.check_output(['sudo', 'journalctl', '-u', 'nas-ui', '-n', '20'], text=True)
    print(logs)
except:
    print("Could not retrieve logs. Try: sudo journalctl -u nas-ui -n 20")
