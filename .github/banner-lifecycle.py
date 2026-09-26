import hashlib,json,os,subprocess,time
from pathlib import Path
assert os.name=='nt' and os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted'
root=Path(os.environ['RUNNER_TEMP'])/'banner-lifecycle'
rows=[{'file': 'banner-baseline.exe', 'sha256': 'ba6a276bd799a871d98491cb663c7a9785b1fe38fa4d6c73797c5afd811c8197', 'bytes': 43529}, {'file': 'banner-pinned.exe', 'sha256': 'c6182f80d33dc305be228fcb8bfcfc5f53e4ac5e2623b31ed141290a2a1779f3', 'bytes': 50597}]
result={'status':'running','runs':[]}
for row in rows:
 file=root/row['file']
 assert hashlib.sha256(file.read_bytes()).hexdigest()==row['sha256']
 for attempt in range(50):
  started=time.monotonic()
  process=subprocess.run([str(file)],timeout=30)
  outcome={'file':file.name,'attempt':attempt+1,'exitCode':process.returncode,'seconds':round(time.monotonic()-started,2)}
  result['runs'].append(outcome)
  (root/'result.json').write_text(json.dumps(result,indent=2))
  print(json.dumps(outcome),flush=True)
result['baselineFailures']=sum(r['exitCode']!=0 for r in result['runs'] if r['file']=='banner-baseline.exe')
result['fixedFailures']=sum(r['exitCode']!=0 for r in result['runs'] if r['file']=='banner-pinned.exe')
result['status']='passed' if result['fixedFailures']==0 else 'failed'
(root/'result.json').write_text(json.dumps(result,indent=2))
print(json.dumps({k:v for k,v in result.items() if k!='runs'}),flush=True)
assert result['status']=='passed'
