#requires -Version 7.0
param(
    [string]$ConfigPath,
    [string]$PythonPath,
    [psobject]$Configuration
)
$ErrorActionPreference = 'Stop'
# Avoid Windows packaged-app redirection of LocalAppData: scheduled tasks run
# outside that package and must see the same physical files.
$installDir = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.easydrop'
$destination = Join-Path ([Environment]::GetFolderPath('Desktop')) '来自vivo'
New-Item -ItemType Directory -Path $installDir,$destination -Force | Out-Null

# Credentials stay in this user's profile, outside the repository.
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$acl = [Security.AccessControl.DirectorySecurity]::new()
$acl.SetAccessRuleProtection($true, $false)
foreach ($account in @($identity, 'NT AUTHORITY\SYSTEM')) {
    $rule = [Security.AccessControl.FileSystemAccessRule]::new($account, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
    $acl.AddAccessRule($rule)
}
[IO.FileSystemAclExtensions]::SetAccessControl([IO.DirectoryInfo]::new($installDir), $acl)

if ($Configuration) {
    $config = $Configuration
} elseif ($ConfigPath) {
    $config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
} else {
    $serverUrl = Read-Host '【必须】PWA 完整地址，例如 https://drop.example.com/drop'
    $secureToken = Read-Host '【必须】电脑接收令牌 PC_PULL_TOKEN（不是 iPad 令牌）' -AsSecureString
    $credential = [PSCredential]::new('EasyDrop', $secureToken)
    $config = [PSCustomObject]@{server_url=$serverUrl.TrimEnd('/'); token=$credential.GetNetworkCredential().Password}
}
$config | Add-Member -NotePropertyName destination -NotePropertyValue $destination -Force
$config | Add-Member -NotePropertyName poll_seconds -NotePropertyValue 5 -Force
$installedConfig = Join-Path $installDir 'receiver.json'
$config | ConvertTo-Json | Set-Content -LiteralPath $installedConfig -Encoding utf8
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'receiver.py') -Destination (Join-Path $installDir 'receiver.py') -Force

if (-not $PythonPath) {
    $PythonPath = (& py -3 -c 'import sys; print(sys.executable)').Trim()
    if ($LASTEXITCODE -ne 0) { throw '请安装 Python 3.11 或更新版本，然后重新运行。' }
}
$pythonw = Join-Path (Split-Path -Parent $PythonPath) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) { throw "找不到 pythonw.exe：$pythonw" }

$taskName = 'EasyDrop-PC-Receiver'
$arguments = '"{0}" --config "{1}"' -f (Join-Path $installDir 'receiver.py'), $installedConfig
$action = New-ScheduledTaskAction -Execute $pythonw -Argument $arguments
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $taskName
}
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Milliseconds 800
if ((Get-ScheduledTask -TaskName $taskName).State -ne 'Running') {
    $result = (Get-ScheduledTaskInfo -TaskName $taskName).LastTaskResult
    throw "接收程序启动失败（代码 $result）。请检查 $installDir 中的配置和 receiver.log。"
}
Write-Output "已启动电脑接收。保存位置：$destination"
Write-Output "登录 Windows 后自动运行。日志：$(Join-Path $installDir 'receiver.log')"
