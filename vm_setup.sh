#!/bin/bash
set -e

echo "Starting VM Setup via Git Clone..."

# Update system packages
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip nginx apache2-utils git openssl

# Clone or pull the repository
if [ -d "$HOME/kite_dashboard" ]; then
    echo "Directory exists, pulling latest changes..."
    cd $HOME/kite_dashboard
    git pull
else
    echo "Cloning repository..."
    cd $HOME
    git clone https://github.com/Shailendra-14240/kite_dashboard.git
    cd kite_dashboard
fi

# Move config files from home directory if they exist
echo "Moving local config files..."
mv $HOME/.env . 2>/dev/null || true
mv $HOME/pl.db . 2>/dev/null || true
mv $HOME/token.json . 2>/dev/null || true
mv $HOME/paytm_token.json . 2>/dev/null || true

# Create virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install wheel
pip install -r requirements.txt

# --- SSL: Generate self-signed certificate (valid 10 years) ---
echo "Setting up self-signed SSL certificate..."
CERT=/etc/ssl/certs/kite-selfsigned.crt
KEY=/etc/ssl/private/kite-selfsigned.key
if [ ! -f "$CERT" ] || [ ! -f "$KEY" ]; then
    EXTERNAL_IP=$(curl -sf "http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip" -H "Metadata-Flavor: Google" || echo "localhost")
    sudo openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
        -keyout "$KEY" \
        -out "$CERT" \
        -subj "/CN=$EXTERNAL_IP"
    echo "SSL certificate generated for $EXTERNAL_IP"
else
    echo "SSL certificate already exists, skipping..."
fi

# --- Configure Nginx: HTTP -> HTTPS redirect + HTTPS reverse proxy ---
echo "Configuring Nginx..."

# Remove all old/duplicate configs to avoid conflicts
sudo rm -f /etc/nginx/sites-enabled/default
sudo rm -f /etc/nginx/sites-enabled/kite_dashboard
sudo rm -f /etc/nginx/sites-enabled/kite-dashboard
sudo rm -f /etc/nginx/sites-available/kite_dashboard
sudo rm -f /etc/nginx/sites-available/kite-dashboard

# Create fresh Nginx config with HTTPS
sudo bash -c 'cat > /etc/nginx/sites-available/kite-dashboard <<EOF
# Redirect all HTTP traffic to HTTPS
server {
    listen 80;
    server_name _;
    return 301 https://\$host\$request_uri;
}

# HTTPS server
server {
    listen 443 ssl;
    server_name _;

    ssl_certificate /etc/ssl/certs/kite-selfsigned.crt;
    ssl_certificate_key /etc/ssl/private/kite-selfsigned.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 120;
        proxy_connect_timeout 120;
    }
}
EOF'

# Enable the site
sudo ln -sf /etc/nginx/sites-available/kite-dashboard /etc/nginx/sites-enabled/kite-dashboard

# Test and restart Nginx
sudo nginx -t
sudo systemctl restart nginx
echo "Nginx configured with HTTPS successfully."

# --- Setup Systemd service for Flask App ---
echo "Setting up systemd service..."
sudo bash -c "cat > /etc/systemd/system/kite-dashboard.service <<EOF
[Unit]
Description=Kite Dashboard Flask App
After=network.target

[Service]
User=$USER
WorkingDirectory=/home/$USER/kite_dashboard
Environment=PATH=/home/$USER/kite_dashboard/.venv/bin
ExecStart=/home/$USER/kite_dashboard/.venv/bin/python app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF"

# Enable the service (it will be started by the powershell script later)
sudo systemctl daemon-reload
sudo systemctl enable kite-dashboard.service

echo "============================================="
echo "VM Setup Script completed successfully!"
echo "App will be available at: https://$(curl -sf http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip -H 'Metadata-Flavor: Google' || echo '<YOUR_IP>')"
echo "Make sure GCP firewall allows ports 80 and 443."
echo "============================================="
