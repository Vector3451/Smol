import requests
import urllib3
import getpass
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

sn_user = input("Enter SN User ID: ")
sn_pass = getpass.getpass("Enter SN Password: ")

url = "https://dev337329.service-now.com/api/now/table/incident"
headers = {"Content-Type": "application/json", "Accept": "application/json"}
payload = {
    "short_description": "NAS Test Incident",
    "description": "If you see this, the API works!",
    "urgency": "3",
    "impact": "3"
}

print("Sending request...")
try:
    response = requests.post(url, auth=(sn_user, sn_pass), headers=headers, json=payload, verify=False)
    print(f"Status: {response.status_code}")
    print(f"Body: {response.text}")
except Exception as e:
    print(f"Connection Error: {e}")
