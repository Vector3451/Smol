import os
import requests
import json
import urllib3

# Suppress insecure request warnings if dev instances have self-signed certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configuration from Environment Variables (Read by systemd in production)
SN_INSTANCE = os.environ.get('SN_INSTANCE', 'dev337329.service-now.com')
SN_USER = os.environ.get('SN_USER', 'admin')  # Change this to your integration user
SN_PASS = os.environ.get('SN_PASS', 'dummy_password') # Dummy by default to prevent accidental spam

def _get_base_url():
    if not SN_INSTANCE.startswith('http'):
        return f"https://{SN_INSTANCE}"
    return SN_INSTANCE

def create_incident(short_description, description, urgency=1, impact=1):
    """
    Creates a high-priority incident in the ServiceNow Incident table.
    urgency/impact: 1 = High, 2 = Medium, 3 = Low
    """
    if SN_PASS == 'dummy_password':
        print(f"[SERVICENOW MOCK] High Priority Incident -> {short_description}")
        return True
        
    url = f"{_get_base_url()}/api/now/table/incident"
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    
    payload = {
        "short_description": short_description,
        "description": description,
        "urgency": str(urgency),
        "impact": str(impact),
        "category": "hardware" # Fits disk space issues nicely
    }
    
    try:
        response = requests.post(url, auth=(SN_USER, SN_PASS), headers=headers, json=payload, verify=False, timeout=10)
        response.raise_for_status()
        incident_number = response.json().get('result', {}).get('number', 'UNKNOWN')
        print(f"[SERVICENOW SUCCESS] Created Incident: {incident_number}")
        return True
    except requests.exceptions.RequestException as e:
        print(f"[SERVICENOW ERROR] Failed to create incident: {e}")
        return False

def log_audit_event(username, action, filename, details=""):
    """
    Creates an informational incident acting as an audit log.
    If you have a custom table in ServiceNow for logs, the URL can be changed here.
    """
    if SN_PASS == 'dummy_password':
         print(f"[SERVICENOW MOCK] Audit Event -> {username} did '{action}' on '{filename}'")
         return True
         
    url = f"{_get_base_url()}/api/now/table/incident"
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    
    description_text = f"NAS Audit Log Event\nTime: {os.popen('date').read().strip()}\nUser: {username}\nAction: {action}\nFile: {filename}\nDetails: {details}"
    
    payload = {
        "short_description": f"NAS Audit: {action} by {username}",
        "description": description_text,
        "urgency": "3", # Low urgency for audit logs
        "impact": "3",  # Low impact
        "category": "network"
    }
    
    try:
         response = requests.post(url, auth=(SN_USER, SN_PASS), headers=headers, json=payload, verify=False, timeout=10)
         if response.status_code == 201:
              print(f"[SERVICENOW SUCCESS] Audit log synced.")
              return True
    except Exception as e:
         print(f"[SERVICENOW ERROR] Audit log failed: {e}")
         return False
    return False
