#!/bin/bash
# Get the directory of the script
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

# Activate venv if it exists
if [ -d "venv" ]; then
    echo "Activating virtual environment..."
    source venv/bin/activate
fi

# Print Tailscale IP for convenience
echo "Checking Tailscale IP..."
TS_IP=$(ip addr show tailscale0 | grep "inet " | awk '{print $2}' | cut -d/ -f1)
if [ -n "$TS_IP" ]; then
    echo "Tailscale IP found: $TS_IP"
    echo "You should be able to access the app at: http://$TS_IP:8080"
else
    echo "Tailscale interface (tailscale0) not found or no IP assigned."
    echo "Access via LAN IP or localhost."
fi

echo "Starting NAS UI..."
# Run python directly as app.py already configures 0.0.0.0:8080
python3 app.py
