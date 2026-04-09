import subprocess
import json
import psutil
import os
import datetime
import time
import string
import random
import torch
from transformers import GPT2LMHeadModel, GPT2TokenizerFast

from flask import Flask, render_template, redirect, url_for, request, flash, send_file, jsonify, session
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
import servicenow

# Cooldown for disk alerts to prevent spamming ServiceNow (1 hour)
LAST_DISK_ALERT_TIME = 0

# --- FORCE REAL SYSTEM OPERATIONS ---
if os.name != 'nt':
    os.environ['NAS_ENV'] = 'production'
else:
    os.environ['NAS_ENV'] = 'development'

app = Flask(__name__)
# Secret key is required for session management. 
# In production, this should be a random string.
app.secret_key = os.environ.get('SECRET_KEY', 'dev_key_very_secret')

# --- Iframe / Cross-Site Cookie Support ---
# To allow ServiceNow to embed this page, we must allow cookies to be sent in an iframe.
# Note: SameSite=None REQUIRES Secure=True (HTTPS). 
app.config.update(
    SESSION_COOKIE_SAMESITE='None',
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=True, 
)


@app.after_request
def add_header(response):
    # Allow ServiceNow to embed our site in an iframe
    response.headers['X-Frame-Options'] = 'ALLOW-FROM https://dev337329.service-now.com/'
    # Better modern way
    response.headers['Content-Security-Policy'] = "frame-ancestors 'self' https://dev337329.service-now.com/"
    return response

# --- NAS-GPT2 Model Loading ---

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# Support both Windows structure (../nas_model_250m) and the flattened Ubuntu structure (..)
if os.path.exists(os.path.join("..", "nas_model_250m", "config.json")):
    MODEL_PATH = "../nas_model_250m"
else:
    MODEL_PATH = ".."
    
TOKENIZER_PATH = "../tokenizer/nas_tokenizer_final" if os.path.exists("../tokenizer/nas_tokenizer_final") else "../tokenizer"

try:
    print(f"Loading NAS-GPT2 Model onto {device} from {MODEL_PATH}...")
    tokenizer = GPT2TokenizerFast.from_pretrained(TOKENIZER_PATH)
    model = GPT2LMHeadModel.from_pretrained(MODEL_PATH).to(device)
    model.eval()
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    print("Model loaded successfully.")
    MODEL_LOADED = True
except Exception as e:
    print(f"Failed to load model: {e}")
    MODEL_LOADED = False
app.secret_key = os.environ.get('SECRET_KEY', 'dev_key_very_secret')

# Flask-Login Setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# User Configuration
NAS_USER = os.environ.get('NAS_USER', 'dev')
NAS_PASSWORD = os.environ.get('NAS_PASSWORD', 'raspberry')
TAILSCALE_IP = None

def get_tailscale_ip():
    global TAILSCALE_IP
    if TAILSCALE_IP:
        return TAILSCALE_IP
    try:
        # Try to get tailscale IP
        # We can try executing `tailscale ip -4`
        # Check if tailscale is in path first? Or just try run.
        if os.environ.get('NAS_ENV') == 'production':
             try:
                 out = subprocess.check_output(['tailscale', 'ip', '-4'], text=True).strip()
                 if out:
                     TAILSCALE_IP = out
                 else:
                     TAILSCALE_IP = "Not Connected"
             except FileNotFoundError:
                 TAILSCALE_IP = "Tailscale not found"
             except subprocess.CalledProcessError:
                 TAILSCALE_IP = "Error fetching IP"
        else:
             TAILSCALE_IP = "100.1.2.3 (Mock)"
    except Exception:
        TAILSCALE_IP = "Unknown"
        
    return TAILSCALE_IP

from functools import wraps

class User(UserMixin):
    def __init__(self, id, is_admin=False):
        self.id = str(id)
        self.is_admin = is_admin

@login_manager.user_loader
def load_user(user_id):
    # Retrieve the admin status from the session, defaulting to False if its a normal user
    is_admin = session.get('is_admin', False)
    return User(user_id, is_admin=is_admin)

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not getattr(current_user, 'is_admin', False):
            flash("Administrator privileges required to access this page.", "error")
            return redirect(url_for('files'))
        return f(*args, **kwargs)
    return decorated_function

def get_samba_status():
    """
    Parses `smbstatus` output (JSON preferred, fallback to text) to get shares and connections.
    """
    # Default structure
    status = {'shares': [], 'sessions': []}

    # 1. Try JSON output (Available in newer Samba versions)
    # sudo smbstatus -j
    try:
        # Check if we can run it
        if run_sudo_command(['type', 'smbstatus'])[0]:
             # We use Popen/check_output directly here to get stdout
             # Note: run_sudo_command returns (success, msg), so we need raw output
             if os.environ.get('NAS_ENV') != 'production':
                 # DEV: Return mock if in dev
                 return {
                    'shares': [{'service': 'NAS', 'pid': '1234', 'machine': '192.168.1.50', 'connected_at': 'Mon Feb 5 10:00:00 2026'}],
                    'sessions': [{'uid': 'real_user', 'gid': 'users', 'pid': '1234', 'machine': '192.168.1.50', 'protocol': 'SMB3_11'}]
                }
             
             # Attempt JSON
             try:
                 out = subprocess.check_output(['sudo', 'smbstatus', '-j'], text=True, stderr=subprocess.DEVNULL)
                 data = json.loads(out)
                 
                 # Transform JSON to our format
                 # JSON structure usually: {"sessions": {...}, "tcons": {...}}
                 # tcons are shares
                 
                 for pid, session in data.get('sessions', {}).items():
                     status['sessions'].append({
                         'uid': session.get('username'),
                         'gid': session.get('group'),
                         'pid': pid,
                         'machine': session.get('remote_machine'),
                         'protocol': session.get('protocol_version')
                     })
                     
                 for pid, share in data.get('tcons', {}).items():
                      status['shares'].append({
                          'service': share.get('service'),
                          'pid': pid,
                          'machine': share.get('machine'),
                          'connected_at': share.get('connected_at')
                      })
                 return status
                 
             except (subprocess.CalledProcessError, json.JSONDecodeError):
                 # Fallback to Text Parsing
                 pass
                 
             # Fallback Text Parsing (smbstatus -v)
             out = subprocess.check_output(['sudo', 'smbstatus'], text=True)
             
             # Parse Sessions
             # Skip headers, find "PID     Username" line
             lines = out.splitlines()
             section = None
             
             for line in lines:
                 line = line.strip()
                 if not line or line.startswith('-'): continue
                 
                 if line.startswith('PID') and 'Username' in line:
                     section = 'sessions'
                     continue
                 elif line.startswith('Service') and 'pid' in line:
                     section = 'shares'
                     continue
                     
                 if section == 'sessions':
                     # PID Username Group Machine Protocol ...
                     parts = line.split()
                     if len(parts) >= 4:
                         status['sessions'].append({
                             'uid': parts[1],
                             'gid': parts[2],
                             'pid': parts[0],
                             # Machine might be IP or Hostname (ipv4:...)
                             'machine': parts[3], 
                             'protocol': parts[4] if len(parts) > 4 else '?'
                         })
                         
                 elif section == 'shares':
                     # Service pid Machine Connected at
                     parts = line.split()
                     if len(parts) >= 3:
                         status['shares'].append({
                             'service': parts[0],
                             'pid': parts[1],
                             'machine': parts[2],
                             'connected_at': ' '.join(parts[3:])
                         })
                         
    except Exception as e:
        print(f"Error parsing smbstatus: {e}")
        # Return empty if failed real check
        return status

    return status

def get_system_stats():
    """
    Gather system metrics: CPU, RAM, Uptime, Temp, Network.
    """
    stats = {}
    
    # CPU usage
    stats['cpu_percent'] = psutil.cpu_percent(interval=0.1)
    
    # RAM usage
    mem = psutil.virtual_memory()
    stats['ram_total'] = f"{mem.total / (1024**3):.1f} GB"
    stats['ram_used'] = f"{mem.used / (1024**3):.1f} GB"
    stats['ram_percent'] = mem.percent
    
    # Uptime
    try:
        boot_time = psutil.boot_time()
        uptime_seconds = time.time() - boot_time
        uptime_string = str(datetime.timedelta(seconds=int(uptime_seconds)))
        stats['uptime'] = uptime_string
    except Exception:
        stats['uptime'] = "Unknown"
        
    # Temperature (Pi specific with fallback)
    try:
        temp_c = None
        # Try Pi specific file first
        if os.path.exists('/sys/class/thermal/thermal_zone0/temp'):
             with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                 temp_c = int(f.read()) / 1000.0
        else:
             # Try psutil sensors
             temps = psutil.sensors_temperatures()
             if temps:
                 # prioritize 'coretemp', 'cpu_thermal', etc.
                 for name, entries in temps.items():
                     if name in ['coretemp', 'cpu_thermal', 'k10temp']:
                         for entry in entries:
                             temp_c = entry.current
                             break
                     if temp_c is not None: break
                 # Fallback to first available if no specific match
                 if temp_c is None:
                     for name, entries in temps.items():
                         if entries:
                             temp_c = entries[0].current
                             break

        if temp_c is not None:
            stats['temp_c'] = f"{temp_c:.1f}"
            stats['temp_warning'] = temp_c > 75
        else:
             raise ValueError("No temp sensor found")

    except Exception:
        stats['temp_c'] = "N/A"
        stats['temp_warning'] = False
        
    # Network I/O
    try:
        net = psutil.net_io_counters()
        # Convert to readable format (MB)
        stats['net_sent'] = f"{net.bytes_sent / (1024**2):.1f} MB"
        stats['net_recv'] = f"{net.bytes_recv / (1024**2):.1f} MB"
    except Exception:
        stats['net_sent'] = "0 MB"
        stats['net_recv'] = "0 MB"
        
    return stats


def get_system_users():
    """
    Returns a list of users with UID >= 1000 (excluding nobody).
    """
    users = []
    try:
        # In a real scenario, parsing /etc/passwd is standard
        with open('/etc/passwd', 'r') as f:
            for line in f:
                parts = line.strip().split(':')
                if len(parts) > 2:
                    uid = int(parts[2])
                    user = parts[0]
                    # UID 1000+ are usually human users (on Debian/Pi)
                    # Exclude 'nobody' (usually 65534)
                    if 1000 <= uid < 65534:
                        users.append({'username': user, 'uid': uid})
    except Exception as e:
        # Fallback for dev env if /etc/passwd is not readable/standard
        print(f"Error reading users: {e}")
        users.append({'username': 'dev', 'uid': 1000})
        
    return users

def run_sudo_command(cmd_list):
    """
    Helper to run sudo commands.
    Uses -n (non-interactive) so sudo never blocks waiting for a password.
    If sudo requires a password and NOPASSWD is not set, it fails immediately
    with a clear error instead of hanging the web server.
    Returns (success, message).
    """
    try:
        if os.environ.get('NAS_ENV') != 'production':
            print(f"SIMULATED COMMAND: {' '.join(cmd_list)}")
            return True, "Simulated success"

        # Build the final command: inject -n after 'sudo' for non-interactive mode
        if cmd_list and cmd_list[0] == 'sudo':
            final_cmd = ['sudo', '-n'] + cmd_list[1:]
        else:
            final_cmd = cmd_list

        result = subprocess.run(
            final_cmd,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,  # No TTY, never prompt
            timeout=15
        )

        if result.returncode == 0:
            return True, result.stdout.strip() or "Success"
        else:
            err = (result.stderr or result.stdout or "Unknown error").strip()
            # Detect 'sudo: a password is required' and give actionable advice
            if 'password is required' in err or 'password required' in err:
                err = (
                    f"sudo NOPASSWD not configured for this command. "
                    f"Add to /etc/sudoers via visudo:\n"
                    f"  vboxuser ALL=(ALL) NOPASSWD: ALL"
                )
            return False, err

    except subprocess.TimeoutExpired:
        return False, "Command timed out (15s). Check if the process is hanging."
    except FileNotFoundError:
        return False, "sudo not found on this system."
    except Exception as e:
        return False, str(e)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        if getattr(current_user, 'is_admin', False):
            return redirect(url_for('index'))
        return redirect(url_for('files'))
        
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        # 1. Admin Check
        if username == NAS_USER and password == NAS_PASSWORD:
            user = User(username, is_admin=True)
            login_user(user)
            session['is_admin'] = True
            return redirect(url_for('index'))
            
        # 2. Normal System User Check
        # We use `sshpass` to verify the password against localhost.
        # This is the most reliable way to perform a non-interactive password check
        # without complex PAM configuration.
        is_valid_user = False
        if os.environ.get('NAS_ENV') == 'production':
            try:
                # Command: sshpass -p 'pass' ssh -o ... user@localhost id
                cmd = [
                    'sshpass', '-p', password, 
                    'ssh', '-o', 'StrictHostKeyChecking=no', 
                    '-o', 'PreferredAuthentications=password',
                    f'{username}@localhost', 'id'
                ]
                process = subprocess.run(cmd, capture_output=True, text=True)
                
                if process.returncode == 0:
                    is_valid_user = True
                else:
                    error_msg = process.stderr.lower()
                    if 'command not found' in error_msg or 'not found' in error_msg:
                        flash("System Error: 'sshpass' is not installed on the server.")
                        print("CRITICAL: sshpass not found. Run 'sudo apt-get install sshpass'")
                    else:
                        print(f"Login failed for {username}: {process.stderr}")
            except Exception as e:
                print(f"Authentication error: {e}")
        else:
            # Dev mock: any user with password 'raspberry' logs in as standard user
            if password == 'raspberry':
                is_valid_user = True

        if is_valid_user:
            user = User(username, is_admin=False)
            login_user(user)
            session['is_admin'] = False
            return redirect(url_for('files'))
            
        flash('Invalid username or password')
            
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for('files'))
        
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if not username or not password:
            flash("Username and password required.")
            return redirect(url_for('signup'))
            
        # 1. Add Linux User
        # Important: vboxuser must have NOPASSWD for useradd and chpasswd
        success, msg = run_sudo_command(['sudo', 'useradd', '-m', '-s', '/bin/bash', username])
        if not success:
            flash(f"Registration failed: {msg}")
            print(f"Useradd failed: {msg}")
            return redirect(url_for('signup'))

        # 2. Set Linux Password
        if os.environ.get('NAS_ENV') == 'production':
            try:
                process = subprocess.Popen(
                    ['sudo', 'chpasswd'],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                stdout, stderr = process.communicate(input=f"{username}:{password}\n")
                if process.returncode != 0:
                    flash(f"Failed to set password: {stderr}")
                    print(f"Chpasswd error: {stderr}")
                    return redirect(url_for('signup'))
            except Exception as e:
                flash(f"Internal error setting password: {e}")
                return redirect(url_for('signup'))
        else:
            print(f"SIMULATED CHPASSWD for {username}")

        flash("Registration successful. You may now initialize your session.")
        return redirect(url_for('login'))
        
    return render_template('signup.html')

@app.route('/users')
@admin_required
def users():
    users = get_system_users()
    nas_admin = os.environ.get('NAS_USER', 'dev')
    return render_template('users.html', users=users, nas_admin=nas_admin)

@app.route('/users/add', methods=['POST'])
@admin_required
def add_user():
    username = request.form.get('username')
    password = request.form.get('password')
    
    if not username or not password:
        flash("Username and password required.")
        return redirect(url_for('users'))
        
    # 1. Add Linux User
    # sudo useradd -m -s /bin/bash <username>
    success, msg = run_sudo_command(['sudo', 'useradd', '-m', '-s', '/bin/bash', username])
    if not success:
        flash(f"Failed to create system user: {msg}")
        return redirect(url_for('users'))

    # 2. Set Linux Password
    # echo "<username>:<password>" | sudo chpasswd
    ps = subprocess.Popen(['echo', f'{username}:{password}'], stdout=subprocess.PIPE)
    try:
        # In production we'd pipeline this properly
        # For simplicity/safety in this snippet, effectively doing:
        # subprocess.check_output(['sudo', 'chpasswd'], stdin=ps.stdout)
        if os.environ.get('NAS_ENV') == 'production':
             subprocess.check_output(['sudo', 'chpasswd'], stdin=ps.stdout)
        else:
             print(f"SIMULATED CHPASSWD for {username}")
    except Exception as e:
        flash(f"Failed to set password: {e}")
        return redirect(url_for('users'))

    # 3. Add Samba User
    # (echo "<password>"; echo "<password>") | sudo smbpasswd -a -s <username>
    # Similar complexity to mock safely. 
    if os.environ.get('NAS_ENV') != 'production':
        print(f"SIMULATED SMBPASSWD for {username}")
    else:
        # Implementation left for real system
        pass

    flash(f"User {username} added successfully.")
    return redirect(url_for('users'))

@app.route('/users/delete', methods=['POST'])
@admin_required
def delete_user():
    username = request.form.get('username')

    if not username:
        flash("No username provided.")
        return redirect(url_for('users'))

    # Protect system-critical accounts
    NAS_ADMIN = os.environ.get('NAS_USER', 'dev')
    protected = {NAS_ADMIN, 'root', 'nobody'}
    if username in protected:
        flash(f"Cannot delete protected system account '{username}'.")
        return redirect(url_for('users'))

    # 1. Remove Samba user first (non-fatal — user may not exist in Samba)
    # smbpasswd -x does not need stdin piping, just a direct sudo call
    run_sudo_command(['sudo', 'smbpasswd', '-x', username])

    # 2. Delete Linux user and their home directory
    success, msg = run_sudo_command(['sudo', 'userdel', '-r', username])
    if success:
        flash(f"User '{username}' deleted successfully.")
    else:
        flash(f"Error deleting user '{username}': {msg}")

    return redirect(url_for('users'))

# --- Storage Management ---

@app.route('/storage')
@admin_required
def storage():
    disks = get_all_disks_lsblk()
    return render_template('storage.html', disks=disks)

def get_all_disks_lsblk():
    """
    Uses lsblk -J to get all block devices, mounted or not.
    """
    disks = []
    try:
        # Always try to run the real command first, regardless of ENV
        # Using subprocess directly to capture stdout
        # lsblk is generally safe and available
        try:
             # -J for JSON, -o for columns
             out = subprocess.check_output(['lsblk', '-J', '-o', 'NAME,SIZE,FSTYPE,MOUNTPOINT,TYPE,MODEL,UUID'], text=True)
             data = json.loads(out)
             for device in data.get('blockdevices', []):
                 if device.get('type') in ['loop', 'rom']:
                     continue
                 disks.append(device)
             return disks
        except Exception:
             # Try with sudo if normal execution failed
             try:
                 out = subprocess.check_output(['sudo', 'lsblk', '-J', '-o', 'NAME,SIZE,FSTYPE,MOUNTPOINT,TYPE,MODEL,UUID'], text=True)
                 data = json.loads(out)
                 for device in data.get('blockdevices', []):
                     if device.get('type') in ['loop', 'rom']:
                         continue
                     disks.append(device)
                 return disks
             except Exception:
                 pass
        
        # No mock fallback to avoid "preset names" confusion.
        return []

    except Exception as e:
        print(f"Error lsblk: {e}")
        
    return disks

@app.route('/storage/mount', methods=['POST'])
@admin_required
def mount_disk():
    device = request.form.get('device')
    mountpoint = request.form.get('mountpoint')
    
    if not device or not mountpoint:
        flash("Device and Mountpoint required")
        return redirect(url_for('storage'))
        
    # Security: Ensure mountpoint is legitimate
    if not mountpoint.startswith('/mnt') and not mountpoint.startswith('/srv'):
         flash("Mount point must be in /mnt or /srv")
         return redirect(url_for('storage'))

    # Helper to create dir if missing
    run_sudo_command(['sudo', 'mkdir', '-p', mountpoint])
    
    success, msg = run_sudo_command(['sudo', 'mount', f"/dev/{device}", mountpoint])
    if success:
        flash(f"Mounted {device} to {mountpoint}")
    else:
        flash(f"Error mounting: {msg}")
        
    return redirect(url_for('storage'))

@app.route('/storage/unmount', methods=['POST'])
@admin_required
def unmount_disk():
    device = request.form.get('device') # e.g. sda1
    
    if not device:
        return redirect(url_for('storage'))
        
    success, msg = run_sudo_command(['sudo', 'umount', f"/dev/{device}"])
    if success:
         flash(f"Unmounted {device}")
    else:
         flash(f"Error unmounting: {msg}")
         
    return redirect(url_for('storage'))

@app.route('/storage/format', methods=['POST'])
@admin_required
def format_disk():
    device = request.form.get('device')
    fs_type = request.form.get('fstype', 'ext4')
    label = request.form.get('label', 'NAS_DISK')
    
    if not device:
        flash("Device required")
        return redirect(url_for('storage'))
        
    # SCARY: Format
    cmd = ['sudo', f'mkfs.{fs_type}']
    if fs_type == 'ext4':
        cmd.extend(['-F', '-L', label])
    elif fs_type == 'ntfs':
        cmd.extend(['-f', '-L', label]) # mkfs.ntfs might be different, assume default mkfs.ntfs exists
    elif fs_type == 'vfat':
        cmd.extend(['-I', '-n', label])
        
    cmd.append(f"/dev/{device}")
    
    success, msg = run_sudo_command(cmd)
    if success:
        flash(f"Formatted {device} as {fs_type}")
    else:
        flash(f"Error formatting: {msg}")
        
    return redirect(url_for('storage'))

# --- Share Management Helpers ---

SMB_CONF_PATH = '/etc/samba/smb.conf'
# For dev environment, override path if strictly testing locally without /etc/samba
if os.environ.get('NAS_ENV') != 'production':
    SMB_CONF_PATH = 'smb.conf' 

def get_samba_global():
    config_dict = {}
    import configparser
    # strict=False avoids duplicate section errors (common in manual edits)
    # But samba conf allows duplicate keys sometimes, which configparser hates.
    # We will assume a relatively clean file or just use simple parsing.
    cp = configparser.ConfigParser(strict=False)
    try:
        cp.read(SMB_CONF_PATH)
        if 'global' in cp:
            for k, v in cp['global'].items():
                config_dict[k] = v
    except Exception:
        pass
    return config_dict

def update_samba_global_conf(new_config):
    """
    Updates specific keys in [global] section.
    Respects existing file structure (comments).
    """
    try:
        with open(SMB_CONF_PATH, 'r') as f:
            lines = f.readlines()
            
        new_lines = []
        in_global = False
        
        # Keys we care about updating
        keys_to_update = ['workgroup', 'netbios name', 'server min protocol', 'server max protocol', 'map to guest']
        updated_keys = set()
        
        for line in lines:
            stripped = line.strip()
            
            if stripped.startswith('[global]'):
                in_global = True
                new_lines.append(line)
                continue
            elif stripped.startswith('[') and stripped.endswith(']'):
                in_global = False
            
            if in_global and '=' in line and not line.startswith(';'):
                key = line.split('=')[0].strip()
                if key in new_config and key in keys_to_update:
                    # Update line
                    new_val = new_config[key]
                    new_lines.append(f"   {key} = {new_val}\n")
                    updated_keys.add(key)
                    continue
            
            new_lines.append(line)
            
        # If any keys were missing in existing global section, append them?
        # A simple append logic is tricky if [global] isn't at the end.
        # For MVP, assume keys exist or user must add them manually first? No, we should robustly add them.
        # But for now, let's rely on line replacement if present.
        
        with open(SMB_CONF_PATH, 'w') as f:
            f.writelines(new_lines)
            
        return True, "Updated"
    except Exception as e:
        return False, str(e)

def get_shares():

    """
    Parses smb.conf to list shares.
    Returns list of dicts: {'name': 'sharename', 'path': '/path', 'valid_users': 'user1,user2'}
    """
    shares = []
    # Using configparser specifically with strict=False to be lenient
    import configparser
    config = configparser.ConfigParser(strict=False)
    
    try:
        config.read(SMB_CONF_PATH)
        for section in config.sections():
            if section in ['global', 'printers', 'print$']:
                continue
            
            shares.append({
                'name': section,
                'path': config.get(section, 'path', fallback='?'),
                'valid_users': config.get(section, 'valid users', fallback='Everyone'),
                'read_only': config.get(section, 'read only', fallback='yes'),
                'guest_ok': config.get(section, 'guest ok', fallback='no')
            })

    except Exception as e:
        print(f"Error parse smb.conf: {e}")
        
    return shares

def add_share_to_conf(name, path, valid_users, read_only='no', guest_ok='no'):
    """
    Appends a new share section to the end of smb.conf.
    """
    try:
        # Simple append to avoid re-serializing the whole file (preserves comments)
        new_section = f"\n[{name}]\n   path = {path}\n   valid users = {valid_users}\n   read only = {read_only}\n   guest ok = {guest_ok}\n   create mask = 0664\n   directory mask = 0775\n"
        
        with open(SMB_CONF_PATH, 'a') as f:
            f.write(new_section)

            
        return True, "Share added"
    except Exception as e:
        return False, str(e)

def delete_share_from_conf(share_name):
    """
    Removes a share section by reading lines and skipping the target section.
    Preserves comments outside the target section.
    """
    try:
        with open(SMB_CONF_PATH, 'r') as f:
            lines = f.readlines()
            
        new_lines = []
        skip = False
        
        for line in lines:
            stripped = line.strip()
            # Detect section start
            if stripped.startswith('[') and stripped.endswith(']'):
                current_section = stripped[1:-1]
                if current_section == share_name:
                    skip = True
                else:
                    skip = False
            
            if not skip:
                new_lines.append(line)
        
        with open(SMB_CONF_PATH, 'w') as f:
            f.writelines(new_lines)
            
        return True, "Share deleted"
    except Exception as e:
        return False, str(e)

@app.route('/shares')
@admin_required
def shares():
    shares = get_shares()
    return render_template('shares.html', shares=shares)

@app.route('/shares/add', methods=['POST'])
@admin_required
def add_share():
    name = request.form.get('name')
    path = request.form.get('path')
    users = request.form.get('valid_users') # comma separated
    read_only = 'yes' if request.form.get('read_only') else 'no'
    guest_ok = 'yes' if request.form.get('guest_ok') else 'no'
    
    if not name or not path or not users:
        flash("All fields required")
        return redirect(url_for('shares'))
    
    # Validation: don't overwrite global or existing system shares
    if name.lower() in ['global', 'printers', 'print$']:
        flash("Invalid share name.")
        return redirect(url_for('shares'))

    success, msg = add_share_to_conf(name, path, users, read_only, guest_ok)

    if success:
        # Reload samba
        run_sudo_command(['sudo', 'systemctl', 'reload', 'smbd'])
        flash(f"Share '{name}' created.")
    else:
        flash(f"Error creating share: {msg}")
        
    return redirect(url_for('shares'))

@app.route('/shares/delete', methods=['POST'])
@admin_required
def delete_share():
    name = request.form.get('name')
    if not name:
        return redirect(url_for('shares'))
        
    success, msg = delete_share_from_conf(name)
    if success:
         run_sudo_command(['sudo', 'systemctl', 'reload', 'smbd'])
         flash(f"Share '{name}' deleted.")
    else:
         flash(f"Error deleting share: {msg}")
         
    return redirect(url_for('shares'))



    return redirect(url_for('shares'))


@app.route('/settings')
@admin_required
def settings():
    conf = get_samba_global()
    return render_template('settings.html', config=conf)

@app.route('/settings/update', methods=['POST'])
@admin_required
def update_samba_global():
    workgroup = request.form.get('workgroup', 'WORKGROUP')
    netbios = request.form.get('netbios_name', 'RASPBERRYPI')
    min_proto = request.form.get('min_protocol', 'SMB2')
    max_proto = request.form.get('max_protocol', 'SMB3')
    map_guest = request.form.get('map_to_guest') # value if checked, None if not
    
    # map to guest = Bad User (if checked), otherwise remove or set to 'Never'
    # Actually if unchecked, usually we just don't set it or set to 'Never'.
    
    new_conf = {
        'workgroup': workgroup,
        'netbios name': netbios,
        'server min protocol': min_proto,
        'server max protocol': max_proto,
        'map to guest': 'Bad User' if map_guest else 'Never' 
    }
    
    success, msg = update_samba_global_conf(new_conf)
    
    if success:
        run_sudo_command(['sudo', 'systemctl', 'reload', 'smbd'])
        flash("Global settings updated and Samba reloaded.")
    else:
        flash(f"Error updating settings: {msg}")
        
    return redirect(url_for('settings'))

# --- SMART Health Helpers ---


def get_disk_health():
    """
    Finds the primary storage device and runs smartctl in production.
    Falls back to real psutil data with placeholder SMART fields in dev.
    """
    # Try to find the /srv/nas device, then fall back to root partition
    device = None
    try:
        partitions = psutil.disk_partitions()
        for p in partitions:
            if p.mountpoint in ('/srv/nas', '/'):
                device = p.device
                if p.mountpoint == '/srv/nas':
                    break  # Prefer /srv/nas if found
    except Exception:
        pass

    raw_device = '/dev/sda'  # final fallback
    if device:
        # Strip partition number: /dev/sda1 -> /dev/sda, /dev/mmcblk0p1 -> /dev/mmcblk0
        import re
        raw_device = re.sub(r'p?\d+$', '', device)

    # In production: try to run real smartctl
    if os.environ.get('NAS_ENV') == 'production':
        try:
            out = subprocess.check_output(
                ['sudo', '-n', 'smartctl', '-H', '-i', '-A', '-j', raw_device],
                text=True, stderr=subprocess.DEVNULL, timeout=10
            )
            data = json.loads(out)
            # Ensure required keys exist with defaults
            data.setdefault('smart_status', {'passed': True})
            data.setdefault('temperature', {'current': 0})
            data.setdefault('model_name', raw_device)
            data.setdefault('serial_number', 'N/A')
            return data
        except subprocess.CalledProcessError as e:
            # smartctl exit code 4 means some attrs are informational — still parse
            try:
                data = json.loads(e.output if hasattr(e, 'output') and e.output else '{}')
                data.setdefault('smart_status', {'passed': False})
                data.setdefault('temperature', {'current': 0})
                data.setdefault('model_name', raw_device)
                data.setdefault('serial_number', 'N/A')
                return data
            except Exception:
                pass
        except FileNotFoundError:
            print("smartctl not found — install smartmontools: sudo apt install smartmontools")
        except Exception as e:
            print(f"Error checking SMART: {e}")

    # Dev fallback: use real disk usage stats, placeholder SMART fields
    try:
        usage = psutil.disk_usage('/')
        total_gb = round(usage.total / (1024**3), 1)
    except Exception:
        total_gb = 0

    return {
        "device": {"name": raw_device, "type": "sat"},
        "smart_status": {"passed": True},
        "temperature": {"current": 0},
        "model_name": f"{raw_device} (smartctl unavailable in dev)",
        "serial_number": "N/A — run in production for real data",
        "user_capacity": {"bytes": int(total_gb * 1024**3)},
        "json_format_version": [1, 0]
    }

@app.route('/health')
@admin_required
def health():
    smart_data = get_disk_health()
    
    # Service Status
    services = {
        'smbd': 'stopped',
        'nmbd': 'stopped',
        'ssh': 'stopped',
        'nas-ui': 'running' 
    }
    
    for s in ['smbd', 'nmbd', 'ssh']:
        success, _ = run_sudo_command(['systemctl', 'is-active', s])
        # systemctl is-active returns 0 (success) if active, non-zero if inactive
        # run_sudo_command returns (True, "Success") if check_output succeeds
        # Actually check_output raises error on non-zero exit unless checked carefully
        try:
             # run_sudo_command wraps check_output, so if it returns True, it didn't crash.
             # However, is-active prints 'active' or 'inactive' to stdout.
             if os.environ.get('NAS_ENV') != 'production':
                 services[s] = 'active' # Mock
             else:
                 out = subprocess.check_output(['systemctl', 'is-active', s], text=True).strip()
                 services[s] = out
        except:
             services[s] = 'inactive'

    # Logs (Mock/Real)
    logs = []
    if os.environ.get('NAS_ENV') != 'production':
        logs = [
            {'time': '2026-02-10 10:00:01', 'level': 'INFO', 'msg': 'Samba service restarted'},
            {'time': '2026-02-10 10:05:22', 'level': 'WARN', 'msg': 'Disk /dev/sdb usage > 90%'},
        ]
    else:
        # Read journalctl or syslog?
        # subprocess.check_output(['journalctl', '-n', '20', '--output=short-iso'])
        pass

    return render_template('health.html', smart=smart_data, services=services, logs=logs)

@app.route('/service/<action>/<name>', methods=['POST'])
@login_required
def service_action(action, name):
    if action not in ['start', 'stop', 'restart']:
        flash("Invalid action")
        return redirect(url_for('health'))
        
    if name not in ['smbd', 'nmbd', 'ssh']:
        flash("Invalid service")
        return redirect(url_for('health'))
        
    success, msg = run_sudo_command(['sudo', 'systemctl', action, name])
    if success:
        flash(f"Service {name} {action}ed.")
    else:
        flash(f"Error: {msg}")
        
    return redirect(url_for('health'))

@app.route('/system/power', methods=['POST'])
@login_required
def system_power():
    action = request.form.get('action')
    if action == 'reboot':
        run_sudo_command(['sudo', 'reboot'])
        flash("Rebooting...")
    elif action == 'shutdown':
        run_sudo_command(['sudo', 'shutdown', 'now'])
        flash("Shutting down...")
        
    return redirect(url_for('index'))


@app.route('/')
@login_required
def index():
    samba_status = get_samba_status()
    
    # Improved Disk Detection
    disks = []
    try:
        partitions = psutil.disk_partitions()
        for p in partitions:
            # Filter for physical disks (skip snaps, loop, overlay) usually visible
            if '/dev/' in p.device and not 'loop' in p.device:
                try:
                    usage = psutil.disk_usage(p.mountpoint)
                    disks.append({
                        'device': p.device,
                        'mountpoint': p.mountpoint,
                        'fstype': p.fstype,
                        'total': usage.total,
                        'used': usage.used,
                        'free': usage.free,
                        'percent': usage.percent,
                        # Pre-formatted strings for the UI (Backend-driven approach)
                        'total_str': f"{usage.total / (1024**3):.2f} GB",
                        'used_str': f"{usage.used / (1024**3):.2f} GB",
                        'free_str': f"{usage.free / (1024**3):.2f} GB",
                        'percent_int': int(usage.percent)
                    })
                    
                    # ServiceNow Disk Alert hook
                    if usage.percent > 90.0:
                        global LAST_DISK_ALERT_TIME
                        cur_time = time.time()
                        if cur_time - LAST_DISK_ALERT_TIME > 3600:
                            servicenow.create_incident(
                                short_description=f"CRITICAL: Disk {p.device} at {int(usage.percent)}% usage",
                                description=f"The volume mounted at {p.mountpoint} is almost full.\nTotal: {usage.total / (1024**3):.2f}GB\nUsed: {usage.used / (1024**3):.2f}GB",
                                urgency=1, impact=1
                            )
                            LAST_DISK_ALERT_TIME = cur_time
                except Exception:
                    continue
    except Exception as e:
        print(f"Error scanning disks: {e}")

    # Fallback if empty (e.g. containers)
    if not disks:
         usage = psutil.disk_usage('/')
         disks.append({
            'device': 'root', 
            'mountpoint': '/', 
            'fstype': 'ext4',
            'total': usage.total,
            'used': usage.used, 
            'free': usage.free, 
            'percent': usage.percent,
            'total_str': f"{usage.total / (1024**3):.2f} GB",
            'used_str': f"{usage.used / (1024**3):.2f} GB",
            'free_str': f"{usage.free / (1024**3):.2f} GB",
            'percent_int': int(usage.percent)
        })
        
    
    
    sys_stats = get_system_stats()
    ts_ip = get_tailscale_ip()
    return render_template('index.html', disks=disks, samba=samba_status, sys_stats=sys_stats, tailscale_ip=ts_ip)


# --- File Browser ---

NAS_ROOT = '/srv/nas'
# Use the internal sudo wrapper to safely create the root directory if missing
success, msg = run_sudo_command(['sudo', 'mkdir', '-p', NAS_ROOT])
if success:
    run_sudo_command(['sudo', 'chmod', '777', NAS_ROOT])
else:
    print(f"Warning: Could not create {NAS_ROOT} automatically: {msg}")

@app.route('/files')
@app.route('/files/<path:subpath>')
@login_required
def files(subpath=''):
    # Normalize subpath to safe relative path
    subpath = subpath.strip('/')
    
    # Safety Check: Prevent directory traversal
    abs_path = os.path.join(NAS_ROOT, subpath)
    if not os.path.exists(abs_path):
        return render_template('files.html', error="Path not found", subpath=subpath)
    
    if not os.path.abspath(abs_path).startswith(NAS_ROOT):
         return render_template('files.html', error="Access Denied", subpath=subpath)

    files_list = []
    try:
        # scandir is faster than listdir
        with os.scandir(abs_path) as it:
            for entry in it:
                try:
                    stats = entry.stat()
                    mtime = datetime.datetime.fromtimestamp(stats.st_mtime).strftime('%Y-%m-%d %H:%M')
                    size = stats.st_size
                    
                    files_list.append({
                        'name': entry.name,
                        'is_dir': entry.is_dir(),
                        'size': size,
                        'mtime': mtime,
                        'path': os.path.join(subpath, entry.name)
                    })
                except OSError:
                    continue
    except OSError as e:
        return render_template('files.html', error=str(e), subpath=subpath)

    # Sort: Folders first, then files
    files_list.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
    
    # Parent directory Logic
    parent = None
    if subpath:
        # os.path.dirname("foo") returns "" (empty string)
        # We want to preserve this empty string to signal "Root" to the template
        parent = os.path.dirname(subpath)

    return render_template('files.html', files=files_list, current_path=subpath, parent=parent)

@app.route('/files/upload', methods=['POST'])
@login_required
def upload_file():
    if 'file' not in request.files:
        flash('No file part')
        return redirect(request.referrer)
        
    file = request.files['file']
    path = request.form.get('path', '')
    
    if file.filename == '':
        flash('No selected file')
        return redirect(request.referrer)
        
    if file:
        # Secure filename? Ideally yes, but internal trusted user...
        # Let's keep original name for usability but check path traversal
        filename = file.filename
        
        # Verify target path
        abs_path = os.path.join(NAS_ROOT, path)
        if not os.path.abspath(abs_path).startswith(NAS_ROOT):
             flash("Access Denied")
             return redirect(request.referrer)
             
        file.save(os.path.join(abs_path, filename))
        flash(f"Uploaded {filename}")
        
        # ServiceNow Audit Log hook
        servicenow.log_audit_event(
             username=current_user.id,
             action="File Upload",
             filename=filename,
             details=f"Target Directory: {abs_path}"
        )
        
    return redirect(request.referrer)

@app.route('/files/delete', methods=['POST'])
@admin_required
def delete_file():
    target_path = request.form.get('path')
    if not target_path:
        flash("No path provided to delete.")
        return redirect(request.referrer)
        
    abs_path = os.path.join(NAS_ROOT, target_path)
    if not os.path.abspath(abs_path).startswith(NAS_ROOT):
         flash("Access Denied")
         return redirect(request.referrer)
         
    if not os.path.exists(abs_path):
        flash("File or folder does not exist.")
        return redirect(request.referrer)
        
    # Using sudo to bypass any internal permission boundary since Admin approved it
    cmd = ['sudo', 'rm', '-rf', abs_path]
    success, msg = run_sudo_command(cmd)
    
    if success:
        flash(f"Deleted: {target_path}")
        # ServiceNow Audit Log hook
        servicenow.log_audit_event(
             username=current_user.id,
             action="File/Folder Deleted",
             filename=os.path.basename(target_path),
             details=f"Target Directory: {os.path.dirname(abs_path)}"
        )
    else:
        flash(f"Error deleting: {msg}")
        
    return redirect(request.referrer)

@app.route('/files/download')
@login_required
def download_file():
    path = request.args.get('path')
    if not path:
        return "No path provided", 400
        
    abs_path = os.path.join(NAS_ROOT, path)
    if not os.path.abspath(abs_path).startswith(NAS_ROOT):
         return "Access Denied", 403
         
    if not os.path.exists(abs_path):
        return "File not found", 404
        
    return send_file(abs_path, as_attachment=True)

@app.route('/files/play')
@login_required
def play_video():
    path = request.args.get('path')
    if not path:
        return "No path provided", 400
        
    filename = os.path.basename(path)
    parent = os.path.dirname(path)
    ext = filename.split('.')[-1].lower() if '.' in filename else 'mp4'
    if ext == 'mov': ext = 'mp4'
    
    return render_template('player.html', filename=filename, path=path, parent=parent, ext=ext)

@app.route('/files/stream')
@login_required
def stream_file():
    path = request.args.get('path')
    if not path:
        return "No path provided", 400
        
    abs_path = os.path.join(NAS_ROOT, path)
    if not os.path.abspath(abs_path).startswith(NAS_ROOT):
         return "Access Denied", 403
         
    if not os.path.exists(abs_path):
        return "File not found", 404
        
    # conditional=True enables streaming byte requests (206 Partial Content)
    return send_file(abs_path, conditional=True)

# --- Backup ---

BACKUP_HISTORY = []

@app.route('/backup')
@admin_required
def backup():
    return render_template('backup.html', history=reversed(BACKUP_HISTORY))

@app.route('/backup/run', methods=['POST'])
@admin_required
def run_backup():
    source = request.form.get('source')
    dest = request.form.get('dest')
    dry_run = request.form.get('dry_run')
    
    if not source or not dest:
        flash("Source and Destination required")
        return redirect(url_for('backup'))
        
    cmd = ['sudo', 'rsync', '-av']
    if dry_run:
        cmd.append('--dry-run')
        
    cmd.extend([source, dest])
    
    success, msg = run_sudo_command(cmd)
    
    # Simple history logging
    status = "Success" if success else "Failed"
    details = f"{'DRY RUN ' if dry_run else ''}Source: {source} -> Dest: {dest}. {msg[:50]}..."
    
    BACKUP_HISTORY.append({
        'time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'status': status,
        'details': details
    })
    
    if success:
        flash(f"Backup {'simulated' if dry_run else 'completed'}: {msg[:100]}...")
    else:
        flash(f"Backup failed: {msg}")
        
    return redirect(url_for('backup'))

# --- API & Automation ---

@app.route('/api/status')
@admin_required
def api_status():
    return {
        'system': get_system_stats(),
        'samba': get_samba_status(),
        'health': get_disk_health()
    }

@app.route('/api/shares')
@admin_required
def api_shares():
    return {'shares': get_shares()}

@app.route('/api/users')
@admin_required
def api_users():
    return {'users': get_system_users()}

# --- NAS Assistant Routes ---
@app.route('/assistant')
@admin_required
def assistant():
    return render_template('assistant.html', model_loaded=MODEL_LOADED)

@app.route('/api/assistant/generate', methods=['POST'])
@admin_required
def generate_command():
    if not MODEL_LOADED:
        return jsonify({"error": "NAS-GPT2 Model failed to load on server start."}), 500

    data = request.json
    if not data or 'tag' not in data or 'task' not in data:
        return jsonify({"error": "Please provide 'tag' and 'task' in the JSON body."}), 400

    tag = str(data['tag']).strip().upper()
    task = str(data['task']).strip()

    # CRUISE CONTROL FIX: The model was trained on "### Instruction:\n"
    # Injecting the [TAG] caused out-of-distribution hallucination.
    prompt = f"### Instruction:\n{task}\n\n### Response:\n"
    inputs = tokenizer(prompt, return_tensors="pt").to(device)

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=150,
            do_sample=True,
            temperature=0.2,
            top_p=0.85,
            repetition_penalty=1.1,
            pad_token_id=tokenizer.eos_token_id,
            eos_token_id=tokenizer.eos_token_id,
        )
    
    response_text = tokenizer.decode(outputs[0], skip_special_tokens=True)
    
    response_split = f"### Response:\n"
    if response_split in response_text:
        final_answer = response_text.split(response_split)[1].strip()
    else:
        final_answer = response_text.strip()
    
    return jsonify({
        "tag": tag,
        "task": task,
        "response": final_answer
    })

@app.route('/api/assistant/execute', methods=['POST'])
@login_required
def execute_command():
    data = request.json
    if not data or 'command' not in data:
        return jsonify({"error": "No command provided"}), 400

    raw_command = data['command']

    import re

    # Parse the AI output into executable shell lines.
    # The model often outputs: "1. Description: sudo command arg"
    # Strategy: extract lines that look like shell commands.
    extracted_commands = []
    for line in raw_command.splitlines():
        line = line.strip()
        if not line:
            continue

        # Strip numbering: "1. ", "2) "
        line = re.sub(r'^\d+[.)\s]+', '', line).strip()

        # Strip descriptive prefix before a sudo/systemctl command:
        # e.g. "Restart service: sudo systemctl restart smbd" -> "sudo systemctl restart smbd"
        colon_match = re.match(r'^[^:]+:\s*(sudo\s.+|systemctl\s.+)', line)
        if colon_match:
            line = colon_match.group(1).strip()

        # Inject prerequisite for sshd config test
        if 'sshd' in line and '-t' in line:
            extracted_commands.append('sudo mkdir -p /run/sshd')

        # Accept any line that starts with a known shell token or looks like a command
        # Reject lines that are clearly prose (no lowercase command token at start)
        SHELL_PREFIXES = (
            'sudo ', 'systemctl ', 'ls ', 'cat ', 'chmod ', 'chown ',
            'mkdir ', 'smbstatus ', 'smbpasswd ', 'useradd ', 'userdel ',
            'mount ', 'umount ', 'df ', 'du ', 'rsync ', 'mdadm ',
            'exportfs ', 'showmount ', 'nfsstat ', 'journalctl ', 'tail ',
            'grep ', 'echo ', 'find ', 'cp ', 'mv ', 'rm ', 'tar ',
            'apt ', 'dpkg ', 'service ', 'ip ', 'ping ', 'ss ', 'netstat ',
        )
        first_word = line.split()[0].lower() if line.split() else ''
        if line.startswith(SHELL_PREFIXES) or (
            first_word and
            not any(c.isupper() for c in first_word) and
            re.match(r'^[a-z][a-z0-9_-]*$', first_word) and
            len(line.split()) >= 2
        ):
            extracted_commands.append(line)

    if not extracted_commands:
        # Nothing parseable — run the raw command as-is (user copy-pasted a real command)
        final_script = raw_command.strip()
    else:
        final_script = ' && '.join(extracted_commands)

    try:
        result = subprocess.run(
            final_script, shell=True,
            capture_output=True, text=True, timeout=120
        )

        stdout = result.stdout or ''
        stderr = result.stderr or ''
        output = stdout
        if stderr:
            output = (output + '\n' + stderr).strip() if output else stderr

        if not output.strip() and result.returncode == 0:
            output = '(Command completed with no output — exit code 0)'

        return jsonify({
            'status': 'success' if result.returncode == 0 else 'error',
            'output': output,
            'returncode': result.returncode
        })
    except subprocess.TimeoutExpired:
        return jsonify({'status': 'error', 'output': 'Command timed out after 120 seconds.'})
    except Exception as e:
        return jsonify({'status': 'error', 'output': str(e)})


if __name__ == '__main__':
    # Using ssl_context='adhoc' to enable HTTPS for SameSite=None cookies in iframes.
    app.run(host='0.0.0.0', port=8080, ssl_context='adhoc')
