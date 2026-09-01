$ErrorActionPreference = "Stop"

$PROJECT_ID = "project-bbabb241-b260-4197-8dd"
$REGION = "asia-south1"
$ZONE = "asia-south1-a"
$INSTANCE_NAME = "kite-dashboard-vm"
$SCHEDULE_NAME = "market-hours"

Write-Host "Creating Static IP..."
$ip_exists = gcloud compute addresses list --filter="name=kite-dashboard-ip" --format="value(name)" --project=$PROJECT_ID
if (-not $ip_exists) {
    gcloud compute addresses create kite-dashboard-ip --region=$REGION --project=$PROJECT_ID
}
$STATIC_IP = gcloud compute addresses describe kite-dashboard-ip --region=$REGION --project=$PROJECT_ID --format="value(address)"
Write-Host "Static IP is $STATIC_IP"

Write-Host "Creating Firewall Rules..."
$fw_http = gcloud compute firewall-rules list --filter="name=allow-http-80" --format="value(name)" --project=$PROJECT_ID
if (-not $fw_http) {
    gcloud compute firewall-rules create allow-http-80 --allow tcp:80 --source-ranges 0.0.0.0/0 --project=$PROJECT_ID
}
$fw_https = gcloud compute firewall-rules list --filter="name=allow-https" --format="value(name)" --project=$PROJECT_ID
if (-not $fw_https) {
    gcloud compute firewall-rules create allow-https --allow tcp:443 --source-ranges 0.0.0.0/0 --description "Allow HTTPS" --project=$PROJECT_ID
}

Write-Host "Creating VM Instance..."
$vm_exists = gcloud compute instances list --filter="name=$INSTANCE_NAME" --format="value(name)" --project=$PROJECT_ID
if (-not $vm_exists) {
    gcloud compute instances create $INSTANCE_NAME `
        --project=$PROJECT_ID `
        --zone=$ZONE `
        --machine-type=e2-micro `
        --address=$STATIC_IP `
        "--tags=http-server,https-server" `
        --image-family=ubuntu-2204-lts `
        --image-project=ubuntu-os-cloud `
        --boot-disk-size=10GB `
        --boot-disk-type=pd-standard `
        --scopes=https://www.googleapis.com/auth/cloud-platform
    
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: VM creation failed. Aborting."
        exit 1
    }

    Write-Host "Waiting for VM SSH to become available..."
    Start-Sleep -Seconds 30
} else {
    Write-Host "VM already exists, ensuring it is started..."
    gcloud compute instances start $INSTANCE_NAME --zone=$ZONE --project=$PROJECT_ID
    Start-Sleep -Seconds 10
}

Write-Host "Setting up Instance Schedule..."
$sched_exists = gcloud compute resource-policies list --filter="name=$SCHEDULE_NAME" --format="value(name)" --project=$PROJECT_ID
if (-not $sched_exists) {
    gcloud compute resource-policies create instance-schedule $SCHEDULE_NAME `
        --region=$REGION `
        --project=$PROJECT_ID `
        --vm-start-schedule="45 8 * * *" `
        --vm-stop-schedule="45 15 * * *" `
        --timezone="Asia/Kolkata"
    
    try {
        gcloud compute instances add-resource-policies $INSTANCE_NAME `
            --zone=$ZONE `
            --resource-policies=$SCHEDULE_NAME `
            --project=$PROJECT_ID
    } catch {
        Write-Host "Schedule already attached or could not be attached."
    }
}

Write-Host "Uploading setup script and config files..."
gcloud compute scp vm_setup.sh $INSTANCE_NAME`: --zone=$ZONE --project=$PROJECT_ID

if (Test-Path "pl.db") {
    gcloud compute scp pl.db $INSTANCE_NAME`: --zone=$ZONE --project=$PROJECT_ID
}
if (Test-Path "token.json") {
    gcloud compute scp token.json $INSTANCE_NAME`: --zone=$ZONE --project=$PROJECT_ID
}
if (Test-Path "paytm_token.json") {
    gcloud compute scp paytm_token.json $INSTANCE_NAME`: --zone=$ZONE --project=$PROJECT_ID
}

Write-Host "Running VM Setup Script (via Git Clone)..."
gcloud compute ssh $INSTANCE_NAME --zone=$ZONE --project=$PROJECT_ID --command="bash vm_setup.sh"

Write-Host "Fetching .env from GCP Secret Manager..."
gcloud compute ssh $INSTANCE_NAME --zone=$ZONE --project=$PROJECT_ID --command="gcloud secrets versions access latest --secret='kite_trading_secret' --project=$PROJECT_ID > ~/kite_dashboard/.env"

Write-Host "Starting Dashboard Service..."
gcloud compute ssh $INSTANCE_NAME --zone=$ZONE --project=$PROJECT_ID --command="sudo systemctl restart kite-dashboard.service"

Write-Host ""
Write-Host "============================================="
Write-Host "DEPLOYMENT COMPLETE!"
Write-Host "Your dashboard: https://$STATIC_IP"
Write-Host "(Browser will show a security warning - click Advanced > Proceed)"
Write-Host ""
Write-Host "Kite redirect URL  : https://$STATIC_IP/callback"
Write-Host "Paytm redirect URL : https://$STATIC_IP/paytm_callback"
Write-Host "Update these in your API consoles AND in GCP Secret Manager (.env)"
Write-Host "============================================="