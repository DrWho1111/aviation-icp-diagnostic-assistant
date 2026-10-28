"""Make a credential-free candidate ZIP and a NEW isolated deployment directory.

This preparation does not start containers and never labels the candidate a
final release. Models/images may be reused; application/platform data may not.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile
import yaml

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.package_release import DIRS,FILES,EXCLUDED
from scripts.configure_platforms import configure,env_values,replace_env
from scripts.release_security import private_values,scan


def isolated_compose(path,settings,offset):
    doc=yaml.safe_load(path.read_text(encoding='utf-8'))
    import re
    for service in doc.get('services',{}).values():
        service.pop('container_name',None)
        rewritten=[]
        for port in service.get('ports',[]):
            if isinstance(port,dict):
                published=int(port['published']);target=int(port['target'])
            else:
                def variable(match):
                    expression=match.group(1)
                    parsed=re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]*)(?::?-(.*))?',expression)
                    if not parsed:raise ValueError('Unsupported port variable syntax')
                    key=parsed[1]
                    value=settings.get(key)
                    if not value:
                        value=parsed[2]
                    if not value or not value.isdecimal():raise ValueError('Unresolved port variable')
                    return value
                literal=re.sub(r'\$\{([^}]+)\}',variable,str(port))
                parts=literal.split(':');published=int(parts[-2]);target=int(parts[-1].split('/')[0])
            rewritten.append({'host_ip':'127.0.0.1','published':str(published+offset),'target':target,'protocol':'tcp'})
        if rewritten:service['ports']=rewritten
    for category in ['volumes','networks']:
        for value in (doc.get(category) or {}).values():
            if isinstance(value,dict):
                if value.get('external'):raise ValueError('External platform storage/network cannot be reused')
                value.pop('name',None)
    path.write_text(yaml.safe_dump(doc,allow_unicode=True,sort_keys=False),encoding='utf-8')


def prepare(destination):
    destination=destination.resolve()
    if destination.exists() or destination==ROOT.resolve() or destination.is_relative_to(ROOT.resolve()):
        raise ValueError('Choose a new directory outside the live project')
    paths={ROOT/name for name in FILES if (ROOT/name).is_file()}
    for name in DIRS:
        paths.update(p for p in (ROOT/name).rglob('*') if p.is_file())
    paths=sorted(p for p in paths if not EXCLUDED.intersection(p.relative_to(ROOT).parts)
        and p.suffix not in {'.pyc','.log','.tsbuildinfo'} and p.name not in {'release-manifest.json','package-manifest.json'})
    suffix=hashlib.sha256(str(destination).encode('utf-8')).hexdigest()[:8]
    archive=ROOT/('outputs/rebuild-candidate-20261024-'+suffix+'.zip');manifest={}
    credentials=private_values(ROOT)
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as bundle:
        for path in paths:
            relative=path.relative_to(ROOT).as_posix()
            if not path.resolve().is_relative_to(ROOT.resolve()):raise ValueError('Candidate input outside workspace')
            content=path.read_bytes();scan(content,credentials,relative)
            manifest[relative]=hashlib.sha256(content).hexdigest();bundle.writestr(relative,content)
        bundle.writestr('REBUILD-MANIFEST.json',json.dumps({'stage':'candidate_not_final','sha256':manifest},ensure_ascii=False,indent=2))
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip():raise RuntimeError('Candidate archive failed integrity check')
        destination.mkdir(parents=True)
        for member in bundle.infolist():
            target=(destination/member.filename).resolve()
            if not target.is_relative_to(destination):raise ValueError('Unsafe archive path')
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(bundle.read(member))
    for name,digest in manifest.items():
        if hashlib.sha256((destination/name).read_bytes()).hexdigest()!=digest:raise RuntimeError('Extracted file differs')
    # Reuse only pinned source archives, not any live private configuration.
    for platform,version in [('dify','1.17.1'),('ragflow','v0.27.2')]:
        source=ROOT/f'.tools/platform-sources/{platform}-{version}.zip'
        target=destination/f'.tools/platform-sources/{source.name}';target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    report={'stage':'prepared_not_deployed','candidate_zip':archive.relative_to(ROOT).as_posix(),
        'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'destination':str(destination),'files_verified':len(manifest),
        'environment':'same-machine new directory/configuration/data volumes; not another computer',
        'reused':'pinned source archives, installed Python runtime, local images and model weights only',
        'live_credentials_copied':False,'live_data_volumes_reused':False}
    (ROOT/'outputs/isolated-rebuild.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


def configure_isolated(destination):
    """Run after copied prepare_platform_sources.py and configure.py, in the new root."""
    for platform in ['dify','ragflow']:
        configure(platform,root=destination)
        private=destination/'.secrets/platforms'/platform;path=private/'.env'
        marker=private/'.icp-isolated.json'
        if marker.exists():continue
        content=path.read_text(encoding='utf-8');settings=env_values(content)
        base=private/'icp.base.compose.yaml'
        if not base.exists():shutil.copy2(private/'icp.compose.yaml',base)
        isolated=private/'icp.compose.yaml'
        shutil.copy2(base,isolated);isolated_compose(isolated,settings,10000)
        if platform=='dify':
            changes={k:'http://127.0.0.1:18081' for k in ['CONSOLE_API_URL','CONSOLE_WEB_URL','SERVICE_API_URL','APP_API_URL','APP_WEB_URL','FILES_URL']}
            path.write_text(replace_env(content,changes),encoding='utf-8')
        marker.write_text(json.dumps({'offset':10000,'storage':'new project-scoped volumes','base_compose':'icp.base.compose.yaml'}),encoding='utf-8')
    app=destination/'deploy/clean.compose.yaml'
    if not app.exists():
        shutil.copy2(destination/'deploy/compose.yaml',app)
        settings=env_values((destination/'.env').read_text(encoding='utf-8'))
        isolated_compose(app,settings,10000)
        document=yaml.safe_load(app.read_text(encoding='utf-8'));document.pop('name',None)
        service=document['services']['app']
        # Build from the extracted candidate, not the already-running image.
        service['image']='icp-clean-candidate:20261024'
        service['environment'].update(DIFY_BASE_URL='http://host.docker.internal:18081/v1',
            RAGFLOW_BASE_URL='http://host.docker.internal:19380',DIFY_NATIVE_MAIN_KEYS_FILE='/run/secrets/native_main')
        service['secrets'].append('native_main')
        document['secrets']['native_main']={'file':'../.secrets/dify-native-main-production.json'}
        app.write_text(yaml.safe_dump(document,allow_unicode=True,sort_keys=False),encoding='utf-8')
    print('Isolated platform configurations generated; no services started.')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--destination',type=Path,required=True);parser.add_argument('--configure',action='store_true')
    args=parser.parse_args()
    try:
        if args.configure:configure_isolated(args.destination.resolve())
        else:prepare(args.destination)
    except Exception as exc:print('Isolated rebuild preparation stopped; error type='+type(exc).__name__);raise SystemExit(1)
