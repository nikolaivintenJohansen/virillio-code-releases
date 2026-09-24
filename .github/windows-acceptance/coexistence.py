"""Run real installer coexistence checks only on a disposable hosted runner.

No product source, user credentials, or application databases are uploaded.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import time
import urllib.parse
import urllib.request

assert os.name == "nt" and os.environ.get("GITHUB_ACTIONS") == "true"
assert os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
root = Path(os.environ["RUNNER_TEMP"]) / "virillio-acceptance"
evidence = root / "evidence"
evidence.mkdir(exist_ok=True)
candidate = root / "Virillio-Code-0.1.3-x64-Setup.exe"
expected = "21c3a280d573ba7bf18cc000fca95235624a6692edcf91b1e3b96f2670e16ede"
report = {"status": "running", "installerSHA256": expected, "host": "disposable-github-windows-2025", "checks": []}
env = {k: v for k, v in os.environ.items() if not any(part in k.upper() for part in ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "GITHUB", "ACTIONS"))}
env.update(OPENCODE_DISABLE_AUTOUPDATE="true", OPENCODE_DISABLE_MODELS_FETCH="true")

def digest(file):
    with file.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def save():
    (evidence / "result.json").write_text(json.dumps(report, indent=2) + "\n")

def check(name, condition, **details):
    report["checks"].append({"name": name, "passed": bool(condition), **details})
    save()
    print(name + ": " + ("PASS" if condition else "FAIL"), flush=True)
    if not condition:
        raise RuntimeError(name)

def ps(script, timeout=60):
    command = "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; " + script
    encoded = base64.b64encode(command.encode("utf-16le")).decode("ascii")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], env=env, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("PowerShell failed: " + result.stderr[-1500:])
    return result.stdout.strip()

def registrations():
    data = ps("$items=@('HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*','HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*','HKLM:\\Software\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*') | ForEach-Object { Get-ItemProperty $_ -ErrorAction SilentlyContinue } | Where-Object { $_.DisplayName -match 'Virillio|OpenCode|Freebuff' } | Select-Object PSChildName,DisplayName,DisplayVersion,DisplayIcon,InstallLocation,UninstallString,Publisher; ConvertTo-Json -InputObject @($items) -Depth 4")
    return json.loads(data)

def processes():
    return json.loads(ps("$items=Get-Process | Where-Object { $_.Path } | ForEach-Object { [pscustomobject]@{pid=$_.Id;path=$_.Path;start=$_.StartTime.ToUniversalTime().ToString('o');title=$_.MainWindowTitle;window=$_.MainWindowHandle.ToInt64()} }; ConvertTo-Json -InputObject @($items)"))

def within(file, directory):
    return str(file).lower().startswith(str(directory).lower().rstrip("\\/") + "\\")

def owned_processes(directory):
    return [p for p in processes() if within(p["path"], directory)]

def file_hashes(directory):
    return {str(p.relative_to(directory)): digest(p) for p in directory.rglob("*") if p.is_file()}

def snapshot_foreign(label):
    result = {name: {"files": file_hashes(item["root"]), "processes": owned_processes(item["root"])} for name, item in foreign.items()}
    result["registrations"] = [r for r in registrations() if "virillio" not in r["DisplayName"].lower()]
    result["sentinels"] = {str(p): digest(p) for p in sentinels}
    (evidence / (label + ".json")).write_text(json.dumps(result, indent=2) + "\n")
    return result

def compare_foreign(before, label):
    after = snapshot_foreign(label)
    for name in foreign:
        check(label + ": " + name + " files preserved", before[name]["files"] == after[name]["files"], files=len(after[name]["files"]))
        old = {(p["pid"], p["start"], p["path"]) for p in before[name]["processes"]}
        current = {(p["pid"], p["start"], p["path"]) for p in after[name]["processes"]}
        check(label + ": " + name + " processes preserved", old.issubset(current), before=len(old), after=len(current))
    check(label + ": foreign registrations preserved", before["registrations"] == after["registrations"])
    check(label + ": shared history sentinels preserved", before["sentinels"] == after["sentinels"])

def run_installer(file, args, name, timeout=900):
    print("Starting " + name, flush=True)
    started = time.monotonic()
    process = subprocess.Popen([str(file), *args], env=env)
    while process.poll() is None and time.monotonic() - started < timeout:
        time.sleep(2)
    if process.poll() is None:
        # Leave the timed-out process for runner teardown; do not hide a dialog by killing it.
        report["timeoutProcesses"] = processes()
        screenshot(name + "-timeout")
        raise RuntimeError(name + " timed out")
    check(name + " exit", process.returncode == 0, exitCode=process.returncode, seconds=round(time.monotonic() - started, 2))

def wait_for_window(directory, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        matching = owned_processes(directory)
        visible = [p for p in matching if p["window"] and p["title"]]
        if visible:
            return visible
        time.sleep(2)
    return []

def screenshot(name):
    path = str(evidence / (name + ".png")).replace("'", "''")
    ps("Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; $r=[System.Windows.Forms.SystemInformation]::VirtualScreen; $b=New-Object System.Drawing.Bitmap($r.Width,$r.Height); $g=[System.Drawing.Graphics]::FromImage($b); $g.CopyFromScreen($r.Left,$r.Top,0,0,$b.Size); $b.Save('" + path + "'); $g.Dispose(); $b.Dispose()")

def close_app(directory):
    for item in owned_processes(directory):
        if not item["window"]:
            continue
        # Revalidate exact path and start time before requesting normal window closure.
        path = item["path"].replace("'", "''")
        ps("$p=Get-Process -Id " + str(item["pid"]) + "; if($p.Path -ne '" + path + "' -or $p.StartTime.ToUniversalTime().ToString('o') -ne '" + item["start"] + "'){throw 'Process identity changed'}; $null=$p.CloseMainWindow()")
    deadline = time.monotonic() + 30
    while owned_processes(directory) and time.monotonic() < deadline:
        time.sleep(1)
    check("Owned Virillio application closed gracefully", not owned_processes(directory))

def verify_private_backend():
    profile = Path(os.environ["APPDATA"]) / "io.virillio.code.desktop/backend"
    state = profile / "state/opencode/server.json"
    deadline = time.monotonic() + 300
    while not state.exists() and time.monotonic() < deadline:
        time.sleep(2)
    check("Desktop starts a private backend", state.is_file())
    registration = json.loads(state.read_text())
    address = registration["url"]
    check("Private backend binds localhost", urllib.parse.urlsplit(address).hostname in ("localhost", "127.0.0.1", "::1"))
    secret = (state.parent / "password").read_text().strip()
    headers = {"Authorization": "Basic " + base64.b64encode(("opencode:" + secret).encode()).decode(), "Content-Type": "application/json"}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def request(endpoint, method="GET", body=None):
        req = urllib.request.Request(address + endpoint, method=method, headers=headers, data=None if body is None else json.dumps(body).encode())
        with opener.open(req, timeout=30) as response:
            body = response.read()
            return json.loads(body) if body else None
    check("Installed backend is healthy", request("/api/health")["healthy"] is True)
    project = root / "terminal-project"
    project.mkdir(exist_ok=True)
    query = "?" + urllib.parse.urlencode({"directory": str(project)})
    terminal = request("/api/pty" + query, "POST", {"command": "C:\\Windows\\System32\\cmd.exe", "args": ["/d", "/c", "git --version > terminal-proof.txt"], "cwd": str(project), "title": "Installer acceptance"})["data"]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        current = request("/api/pty/" + terminal["id"] + query)["data"]
        if current["status"] == "exited":
            break
        time.sleep(0.3)
    check("Installed terminal executes Git", current["status"] == "exited" and current["exitCode"] == 0 and (project / "terminal-proof.txt").read_text().startswith("git version"))
    request("/api/pty/" + terminal["id"] + query, "DELETE")
    check("Private database created", (profile / "data/opencode/opencode.db").is_file())
    report["privateBackend"] = {"pid": registration["pid"], "statePath": str(state), "healthy": True, "terminalPassed": True}
    save()

foreign = {}
sentinels = []
try:
    check("Exact candidate checksum", digest(candidate) == expected)
    check("Clean disposable host", not registrations())
    for name, url, expected_hash in [
        ("OpenCode", "https://github.com/anomalyco/opencode/releases/download/v1.18.21/opencode-desktop-win-x64.exe", "3bd1a81d8fcb377a6bda60a9abf8d412aca1c9c702218ddbbdf7c7b09deaa739"),
        ("Freebuff", "https://github.com/CodebuffAI/codebuff-community/releases/download/freebuff-desktop-v0.0.138/Freebuff-0.0.138-win-x64-baseline.exe", "a9cb382379d7a8cbfa3e13a81efd793244c8022b43e6ff0792f0e49d144e785b"),
    ]:
        file = root / (name + "-Setup.exe")
        urllib.request.urlretrieve(url, file)
        check(name + " official installer checksum", digest(file) == expected_hash)
        listing = subprocess.run(["C:\\Program Files\\7-Zip\\7z.exe", "l", str(file)], env=env, capture_output=True, text=True, timeout=30)
        (evidence / (name + "-installer-layout.txt")).write_text(listing.stdout, encoding="utf-8")
        if name == "Freebuff":
            # Both official 0.0.138 installers crash in their System.dll before
            # Virillio is executed on this runner. Exercise the unchanged real
            # application payload without claiming Freebuff installer acceptance.
            unpack = root / "freebuff-installer-payload"
            destination = Path(os.environ["LOCALAPPDATA"]) / "Programs/Freebuff-acceptance"
            for command in [
                ["C:\\Program Files\\7-Zip\\7z.exe", "x", "-y", "-o" + str(unpack), str(file), "$PLUGINSDIR\\app-64.7z"],
                ["C:\\Program Files\\7-Zip\\7z.exe", "x", "-y", "-o" + str(destination), str(unpack / "$PLUGINSDIR/app-64.7z")],
            ]:
                extracted = subprocess.run(command, env=env, capture_output=True, text=True, timeout=180)
                check("Extract official Freebuff payload", extracted.returncode == 0)
            executable = destination / "Freebuff.exe"
            report["freebuffSetup"] = "Unmodified official 0.0.138 x64-baseline application payload extracted; upstream NSIS installer crashes in System.dll on the runner."
        else:
            run_installer(file, ["/S", "/currentuser"], name + " installation")
            registration = next(r for r in registrations() if name.lower() in r["DisplayName"].lower())
            icon = registration["DisplayIcon"].strip('"').split(",")[0].strip('"')
            executable = Path(icon)
        check(name + " executable exists", executable.is_file(), path=str(executable))
        foreign[name] = {"root": executable.parent, "executable": executable}
        subprocess.Popen([str(executable)], env=env)
        windows = wait_for_window(executable.parent)
        check(name + " visible window", bool(windows), windows=windows)
        screenshot(name + "-running")
    # Use fresh data with no real credentials, accounts, or user sessions.
    for directory in [Path.home() / ".local/share/opencode", Path.home() / ".config/opencode", Path(os.environ["APPDATA"]) / "ai.opencode.desktop", Path(os.environ["LOCALAPPDATA"]) / "@opencode-aidesktop-updater"]:
        directory.mkdir(parents=True, exist_ok=True)
        sentinel = directory / "virillio-coexistence-preserve.txt"
        sentinel.write_text("Standalone application data must remain untouched.\n")
        sentinels.append(sentinel)
    inherited_database = Path.home() / ".local/share/opencode/shared-victim.db"
    with sqlite3.connect(inherited_database) as database:
        database.execute("CREATE TABLE preservation_test (value TEXT)")
        database.execute("INSERT INTO preservation_test VALUES ('must remain untouched')")
    sentinels.append(inherited_database)
    env.update(OPENCODE_DB=str(inherited_database), XDG_DATA_HOME=str(Path.home() / ".local/share"), XDG_CONFIG_HOME=str(Path.home() / ".config"), XDG_CACHE_HOME=str(Path.home() / ".cache"), XDG_STATE_HOME=str(Path.home() / ".local/state"))
    before = snapshot_foreign("before")
    check("Both foreign applications are running", all(before[name]["processes"] for name in foreign))
    # Default interactive one-click install covers the original erroneous running-app prompt.
    run_installer(candidate, [], "Virillio interactive installation")
    registration = next(r for r in registrations() if "virillio" in r["DisplayName"].lower())
    executable = Path(registration["DisplayIcon"].strip('"').split(",")[0].strip('"'))
    install_root = executable.parent
    check("Dedicated installation directory", install_root.name == "virillio-code", path=str(install_root))
    check("New installer identity", registration["PSChildName"].strip("{}").lower() == "a3f321ba-0e54-5dc6-9826-c36c4c1482bf")
    windows = wait_for_window(install_root, 300)
    check("Installer automatically launches visible Virillio", bool(windows), windows=windows)
    screenshot("Virillio-auto-launch")
    verify_private_backend()
    compare_foreign(before, "after-install")
    report["virillioInstallRoot"] = str(install_root)
    report["virillioExecutable"] = str(executable)
    # Running Virillio must cause silent refusal, not global process termination.
    running = subprocess.run([str(candidate), "/S"], env=env, timeout=600)
    check("Silent running-app refusal", running.returncode == 10, exitCode=running.returncode)
    compare_foreign(before, "after-running-refusal")
    close_app(install_root)
    run_installer(candidate, ["/S"], "Virillio silent reinstall")
    compare_foreign(before, "after-reinstall")
    shortcut_paths = [p for base in [Path.home() / "Desktop", Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs"] for p in base.rglob("*.lnk") if "virillio" in p.name.lower()]
    check("Installed shortcut exists", bool(shortcut_paths), shortcuts=[str(p) for p in shortcut_paths])
    shortcut = str(shortcut_paths[0]).replace("'", "''")
    ps("Start-Process -FilePath '" + shortcut + "'")
    check("Installed shortcut launches visible Virillio", bool(wait_for_window(install_root, 300)))
    verify_private_backend()
    screenshot("Virillio-shortcut-launch")
    compare_foreign(before, "after-shortcut-launch")
    close_app(install_root)
    unknown = install_root / "user-created-preserve.txt"
    unknown.write_text("Preserve unknown files.\n")
    uninstaller = next(install_root.glob("Uninstall*.exe"))
    run_installer(uninstaller, ["/S"], "Virillio uninstall")
    deadline = time.monotonic() + 300
    while executable.exists() and time.monotonic() < deadline:
        time.sleep(2)
    check("Owned executable removed", not executable.exists())
    check("Unknown installation file preserved", unknown.read_text() == "Preserve unknown files.\n")
    compare_foreign(before, "after-uninstall")
    report.update(status="passed", realInstallerExecuted=True, realOpenCodeAndFreebuffCoexistence=True, limitations=["Third-party applications remained at initial unauthenticated screens; no coding workload was submitted.", "Freebuff ran from its official extracted application payload because its own installer crashes in System.dll on this runner.", "Affected-release recovery, WSL, reboot and channel coexistence are not covered by this run.", "This runner is Windows Server 2025, not the owner's Windows 11 Lenovo."])
except Exception as error:
    report.update(status="failed", error=str(error))
    try:
        screenshot("failure")
        report["failureProcesses"] = processes()
        report["failureRegistrations"] = registrations()
    except Exception as capture_error:
        report["captureError"] = str(capture_error)
    try:
        report["applicationErrors"] = ps("Get-WinEvent -FilterHashtable @{LogName='Application';StartTime=(Get-Date).AddMinutes(-30);Id=1000,1001} -ErrorAction SilentlyContinue | Select-Object TimeCreated,ProviderName,Message | ConvertTo-Json -Depth 4")
    except Exception:
        pass
    raise
finally:
    save()
