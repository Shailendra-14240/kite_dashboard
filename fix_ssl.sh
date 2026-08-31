#!/bin/bash
set -e

echo "Generating self-signed certificate..."
sudo openssl req -x509 -nodes -days 365 -newkey rsa:2048 -keyout /etc/ssl/private/nginx-selfsigned.key -out /etc/ssl/certs/nginx-selfsigned.crt -subj "/C=IN/ST=State/L=City/O=KiteDashboard/CN=35.234.223.204" 2>/dev/null

echo "Updating Nginx configuration for HTTPS..."
sudo bash -c 'cat > /etc/nginx/sites-available/kite_dashboard <<EOF
server {
    listen 80;
    listen 443 ssl;
    server_name _;

    ssl_certificate /etc/ssl/certs/nginx-selfsigned.crt;
    ssl_certificate_key /etc/ssl/private/nginx-selfsigned.key;

    location / {
        auth_basic "Kite Dashboard Login Required";
        auth_basic_user_file /etc/nginx/.htpasswd;

        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
}
EOF'

echo "Restarting Nginx..."
sudo systemctl restart nginx

echo "SSL Setup complete on the VM."
