<#
.SYNOPSIS
  Polaris icin port secer. Cikti tek satir: "polaris <port>" (zaten calisiyor) veya "free <port>".
  Istenen port doluysa (baska program) panelin de taradigi yedek portlari dener.
#>
param([int]$Port = 8765)
$candidates = @($Port, 8765, 8766, 8767, 47615, 47616) | Select-Object -Unique
foreach ($p in $candidates) {
  $c = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $c) { Write-Output "free $p"; exit 0 }
  try {
    $r = Invoke-RestMethod -TimeoutSec 2 -Uri "http://127.0.0.1:$p/health"
    if ($r.service -eq 'polaris' -or ($r.ok -and $r.llm_model)) { Write-Output "polaris $p"; exit 0 }
  } catch {}
}
Write-Output "busy $Port"
exit 1
