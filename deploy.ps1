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

Write-Host "Creating Firewall Rule..."
$fw_exists = gcloud compute firewall-rules list --filter="name=allow-http-80" --format="value(name)" --project=$PROJECT_ID
if (-not $fw_exists) {
    gcloud compute firewall-rules create allow-http-80 --allow tcp:80 --target-tags=http-server --project=$PROJECT_ID
}

Write-Host "Creating VM Instance..."
$vm_exists = gcloud compute instances list --filter="name=$INSTANCE_NAME" --format="value(name)" --project=$PROJECT_ID
if (-not $vm_exists) {
    gcloud compute instances create $INSTANCE_NAME `
        --project=$PROJECT_ID `
        --zone=$ZONE `
        --machine-type=e2-micro `
        --address=$STATIC_IP `
        --tags=http-server `
        --image-family=ubuntu-2204-lts `
        --image-project=ubuntu-os-cloud `
        --boot-disk-size=10GB `
        --boot-disk-type=pd-standard
    
    Write-Host "Waiting for VM SSH to become available..."
    Start-Sleep -Seconds 30
} else {
    Write-Host "VM already exists, ensuring it is started..."
    gcloud compute instances start $INSTANCE_NAME --zone=$ZONE --project=$PROJECT_ID
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

if (Test-Path ".env") {
    gcloud compute scp .env $INSTANCE_NAME`: --zone=$ZONE --project=$PROJECT_ID
}
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

Write-Host "Starting Dashboard Service..."
gcloud compute ssh $INSTANCE_NAME --zone=$ZONE --project=$PROJECT_ID --command="sudo systemctl restart kite-dashboard.service"

Write-Host ""
Write-Host "============================================="
Write-Host "DEPLOYMENT COMPLETE!"
Write-Host "Your dashboard will be available at: http://$STATIC_IP"
Write-Host "Remember to update KITE_REDIRECT_URL and PAYTM_REDIRECT_URL"
Write-Host "to point to this public IP in your API consoles and .env file!"
Write-Host "============================================="
