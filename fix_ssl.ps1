$ErrorActionPreference = "Stop"

$PROJECT_ID = "project-bbabb241-b260-4197-8dd"
$ZONE = "asia-south1-a"
$INSTANCE_NAME = "kite-dashboard-vm"

Write-Host "1. Opening Port 443 in Firewall..."
$fw_exists = gcloud compute firewall-rules list --filter="name=allow-https-443" --format="value(name)" --project=$PROJECT_ID
if (-not $fw_exists) {
    gcloud compute firewall-rules create allow-https-443 --allow tcp:443 --target-tags=https-server --project=$PROJECT_ID
}

Write-Host "2. Adding HTTPS tag to VM..."
gcloud compute instances add-tags $INSTANCE_NAME --tags=http-server,https-server --zone=$ZONE --project=$PROJECT_ID

Write-Host "3. Uploading and running SSL configuration script..."
gcloud compute scp fix_ssl.sh $INSTANCE_NAME`: --zone=$ZONE --project=$PROJECT_ID
gcloud compute ssh $INSTANCE_NAME --zone=$ZONE --project=$PROJECT_ID --command="bash fix_ssl.sh"

Write-Host "Done! You can now use https://35.234.223.204 as your redirect URL."
