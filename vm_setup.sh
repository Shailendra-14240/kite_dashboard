#!/bin/bash
set -e

echo "Starting VM Setup via Git Clone..."

# Update system packages
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip nginx apache2-utils git

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

# Create Nginx Basic Auth file
if [ ! -f /etc/nginx/.htpasswd ]; then
    sudo htpasswd -cb /etc/nginx/.htpasswd kite admin123
fi

# Configure Nginx as a reverse proxy for Flask
sudo bash -c 'cat > /etc/nginx/sites-available/kite_dashboard <<EOF
server {
    listen 80;
    server_name _;

    location / {
        auth_basic "Kite Dashboard Login Required";
        auth_basic_user_file /etc/nginx/.htpasswd;

        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
}
EOF'

# Enable Nginx site
sudo rm -f /etc/nginx/sites-enabled/default
sudo ln -sf /etc/nginx/sites-available/kite_dashboard /etc/nginx/sites-enabled/ || true
sudo systemctl restart nginx

# Setup Systemd service for Flask App
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

[Install]
WantedBy=multi-user.target
EOF"

# Enable the service (it will be started by the powershell script later)
sudo systemctl daemon-reload
sudo systemctl enable kite-dashboard.service

echo "VM Setup Script completed successfully!"
