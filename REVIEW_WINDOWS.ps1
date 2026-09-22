# Run from the existing Joyful Rides repository directory with Flask stopped.
# Creates a review copy only. Does not push, deploy, or modify original files.
$destination = Join-Path (Split-Path (Get-Location) -Parent) ("joyfull-rides-review-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
New-Item -ItemType Directory -Path $destination -ErrorAction Stop | Out-Null
Get-ChildItem -Force | Where-Object { $_.Name -notin @('.git', '.venv', '__pycache__', '.pytest_cache') } | Copy-Item -Destination $destination -Recurse -Force -ErrorAction Stop
Write-Host "Review copy created: $destination"
Write-Host "Extract the update ZIP into that folder, then follow README_UPGRADE.md."
