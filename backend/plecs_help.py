"""Manual, copyable recovery steps for PLECS connection tools."""
from pathlib import PureWindowsPath
from uuid import UUID


RPC_PROBE = """$request = '<methodCall><methodName>system.listMethods</methodName><params/></methodCall>'
$reply = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:1080/RPC2' -Method Post -ContentType 'text/xml' -Body $request -TimeoutSec 5 -ErrorAction Stop
$methods = ([xml]$reply.Content).SelectNodes('//array/data/value') | ForEach-Object { $_.InnerText }
if (('plecs.load' -in $methods) -and ('plecs.get' -in $methods) -and ('plecs.simulate' -in $methods)) {
    'PLECS RPC ready'
} else {
    throw 'The service on port 1080 did not return the expected PLECS RPC methods.'
}"""


def companion_command(owner):
    UUID(owner)  # Validate without changing the exact owner key stored by the bridge.
    return f"& '.\\.venv\\Scripts\\python.exe' -m backend.plecs_companion --server $server --owner '{owner}'"


def connection_outcome(name, output, pending, executable=''):
    """Keep only unresolved recovery steps from actual tool calls in this turn."""
    if name not in {'check_plecs_connection','connect_plecs','open_plecs_model'}:
        return output
    if output.get('status') in {'ready','opened'} and not output.get('error'):
        pending.pop('connection', None)
        if name == 'open_plecs_model':
            pending.pop('model', None)
        return output
    if not output.get('error') and output.get('status') not in {'unavailable','uncertain','unconfigured','failed'}:
        return output
    kind = {'bridge_unpaired':'pair', 'bridge_offline':'bridge'}.get(output.get('error_code'))
    if not kind:
        kind = 'launch' if name == 'connect_plecs' and not output.get('bridge_job_id') else 'probe'
    if name == 'open_plecs_model':
        # Restoring the connection alone does not prove that the requested model opened.
        pending['model'] = ('probe','')
    key = 'model' if name == 'open_plecs_model' and kind == 'probe' else 'connection'
    if name == 'check_plecs_connection' and kind == 'probe' and not output.get('bridge_job_id') and pending.get(key, (None,))[0] == 'launch':
        kind, executable = pending[key]
    pending[key] = (kind, executable)
    return {**output, 'manual_recovery':kind}


def manual_help(kind, language, *, executable='', owner=''):
    english = language == 'en'
    if kind in {'pair','bridge'}:
        note = ('Open PowerShell in your local Buck Lab project folder. Use the running app URL from Connection settings and enter the pairing code when prompted.'
                if english else '在本机 Buck Lab 项目文件夹打开 PowerShell。按提示输入连接设置中的后端应用地址，再输入配对码。')
        if kind == 'pair':
            note = ('First generate a pairing code in Connection settings. ' if english else '先在当前会话的连接设置里生成本机连接器配对码。') + note
        note += (' For a private ModelScope Studio, enter your ModelScope SDK access token at the ModelScope SDK token for … prompt after checking the hostname. Keep PowerShell open once you see PLECS companion connected.'
                 if english else ' 私有魔搭创空间若提示 ModelScope SDK token for …，请核对域名，在终端输入可访问该空间的魔搭 SDK 访问令牌并按回车。看到 PLECS companion connected. 后保持 PowerShell 打开。')
        code = "$server = Read-Host 'Buck Lab backend URL'\n" + companion_command(owner)
    else:
        note = ('Run this in PowerShell on the PLECS computer. In PLECS Preferences, enable RPC on port 1080.'
                if english else '在安装 PLECS 的电脑上运行以下 PowerShell 命令。请在 PLECS 的 Preferences 中启用 RPC，端口设为 1080。')
        code = RPC_PROBE
        if kind == 'launch':
            path = PureWindowsPath(executable)
            # Use a literal local path only for an unambiguous PLECS executable.
            # Smart quotes are also delimiters in PowerShell. Prompt for unusual paths.
            safe = (path.is_absolute() and path.name.lower() == 'plecs.exe'
                    and all(c.isprintable() and c not in "‘’‚‛`" for c in executable))
            value = "'" + executable.replace("'", "''") + "'" if safe else """(Read-Host 'Full path to PLECS.exe').Trim().Trim('"')"""
            code = ("if (-not (Get-Process -Name PLECS -ErrorAction SilentlyContinue)) {\n"
                    f"    $exe = {value}\n"
                    "    if ((Split-Path -Leaf $exe) -ine 'PLECS.exe' -or -not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw 'Check the path to PLECS.exe.' }\n"
                    "    Start-Process -FilePath $exe -ErrorAction Stop\n"
                    "}\n[void](Read-Host 'Press Enter once PLECS is ready and RPC is enabled')\n" + code)
    return f"{note}\n\n```powershell\n{code}\n```"
