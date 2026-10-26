# SpotOps real-dataset deploy. Run from the folder that holds the downloaded files:
#   cd C:\Users\VIDHYALAKSHMI\spotops ; .\deploy_real.ps1
# NOTE: pkill and nohup are deliberately in SEPARATE ssh calls (one combined call kills itself).
$k    = "$HOME\spotops-aws-key-v2.pem"
$spot = "16.176.137.75"     # change this one if you launched a NEW real Spot VM
$fb   = "13.210.203.78"
$st   = "3.27.11.65"
$o    = @("-o","StrictHostKeyChecking=no","-o","ConnectTimeout=10")

foreach ($f in "train_real.py","checkpoint_utils_real.py","dashboard_real.py") {
    if (-not (Test-Path $f)) { Write-Host "MISSING $f in $(Get-Location). Move the downloaded files here first." -ForegroundColor Red; exit 1 }
}
$pw = Read-Host "Choose a dashboard password (letters/numbers only, no quotes)"
if ($pw -notmatch '^[A-Za-z0-9@#%+=_.-]{6,}$') { Write-Host "Use 6+ simple characters." -ForegroundColor Red; exit 1 }

foreach ($ip in @($spot,$fb)) {
    Write-Host "== training VM $ip" -ForegroundColor Cyan
    scp -i $k @o train_real.py checkpoint_utils_real.py $k "ubuntu@${ip}:/home/ubuntu/"
    ssh -i $k @o "ubuntu@$ip" "chmod 600 ~/spotops-aws-key-v2.pem; pip3 install --break-system-packages -q scikit-learn joblib boto3 numpy"
    ssh -i $k @o "ubuntu@$ip" "python3 -c 'import sklearn,joblib,boto3,numpy' && echo dependencies-OK"
}

Write-Host "== storage VM $st (dashboard)" -ForegroundColor Cyan
scp -i $k @o dashboard_real.py $k "ubuntu@${st}:/home/ubuntu/"
ssh -i $k @o "ubuntu@$st" "chmod 600 ~/spotops-aws-key-v2.pem"
ssh -i $k @o "ubuntu@$st" "printf '%s' '$pw' > ~/dash_pass.txt; chmod 600 ~/dash_pass.txt"
ssh -i $k @o "ubuntu@$st" "pkill -f '[d]ashboard_real.py'"
ssh -i $k @o "ubuntu@$st" "nohup python3 dashboard_real.py > dashboard_real.log 2>&1 &"
Start-Sleep 3
ssh -i $k @o "ubuntu@$st" "ss -tlnp | grep 8001; cat dashboard_real.log"

Write-Host "`nOpen http://${st}:8001   user: admin   password: what you just typed" -ForegroundColor Green
