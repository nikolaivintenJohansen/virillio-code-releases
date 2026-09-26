import hashlib, json, os, subprocess, urllib.request
from pathlib import Path
import winreg
assert os.name == "nt" and os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
root = Path(os.environ["RUNNER_TEMP"]) / "registry-diagnostic"
root.mkdir()
f = root / "affected.exe"
urllib.request.urlretrieve("https://github.com/nikolaivintenJohansen/virillio-code-releases/releases/download/v0.1.2-windows-beta.2/Virillio-Code-Setup.exe", f)
with f.open("rb") as stream: assert hashlib.file_digest(stream,"sha256").hexdigest() == "655fad01a7dfdd28fcdc29ccc161fea87d7a8e65a390262612d28b385554ed09"
assert subprocess.run([str(f),"/S","/currentuser"],timeout=900).returncode == 0
result={}
for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
 for path in (r"Software\abe31ce7-a3ec-561f-b166-ddb0919b6461",r"Software\Microsoft\Windows\CurrentVersion\Uninstall\abe31ce7-a3ec-561f-b166-ddb0919b6461"):
  label=str(view)+":"+path
  try:
   with winreg.OpenKey(winreg.HKEY_CURRENT_USER,path,0,winreg.KEY_READ|view) as key:
    subkeys, values, _ = winreg.QueryInfoKey(key)
    result[label]={"subkeys":subkeys,"values":[winreg.EnumValue(key,i) for i in range(values)]}
  except FileNotFoundError: result[label]="absent"
print(json.dumps(result,indent=2),flush=True)
(root/"result.json").write_text(json.dumps(result,indent=2))
