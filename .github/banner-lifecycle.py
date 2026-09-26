import hashlib,json,os,subprocess,time
from pathlib import Path
assert os.name=='nt' and os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted'
root=Path(os.environ['RUNNER_TEMP'])/'banner-lifecycle'
rows=[{'file': 'banner-actual.exe', 'sha256': 'c646c456047694ea22b53c0fea8c52514747e83161441832e64182fbdb026443', 'bytes': 50662}]
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
result['fixedFailures']=sum(r['exitCode']!=0 for r in result['runs'] if r['file']=='banner-actual.exe')
result['status']='passed' if result['fixedFailures']==0 else 'failed'
(root/'result.json').write_text(json.dumps(result,indent=2))
print(json.dumps({k:v for k,v in result.items() if k!='runs'}),flush=True)
assert result['status']=='passed'
