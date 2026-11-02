import os
import requests
import urllib3
import getpass

# Instance can be overridden for other dev instances: SN_INSTANCE=<host> python test_sn.py
sn_instance = os.environ.get('SN_INSTANCE', 'dev337329.service-now.com')
sn_user = input("Enter SN User ID: ")
sn_pass = getpass.getpass("Enter SN Password: ")

# SECURITY: TLS is verified by default; set SN_TLS_VERIFY=0 only for dev
# instances with self-signed certificates.
sn_verify_tls = os.environ.get('SN_TLS_VERIFY', '1').strip().lower() in ('1', 'true', 'yes')
if not sn_verify_tls:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

url = f"https://{sn_instance}/api/now/table/incident"
headers = {"Content-Type": "application/json", "Accept": "application/json"}
payload = {
    "short_description": "NAS Test Incident",
    "description": "If you see this, the API works!",
    "urgency": "3",
    "impact": "3"
}

print("Sending request...")
try:
    response = requests.post(url, auth=(sn_user, sn_pass), headers=headers, json=payload, verify=sn_verify_tls)
    print(f"Status: {response.status_code}")
    print(f"Body: {response.text}")
except Exception as e:
    print(f"Connection Error: {e}")
