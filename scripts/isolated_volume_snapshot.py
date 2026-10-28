"""Offline backup/restore for OWNED ISOLATED ACCEPTANCE projects only.

Never operates on icp-workbench/icp-dify/icp-ragflow live volumes. Archives and
restored private configuration remain outside the submission allowlist.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.pull_platform_images import docker_cli


def validate_projects(projects):
    if not projects or any(not re.fullmatch(r'icp-clean-[a-z0-9-]+',p) for p in projects):
        raise ValueError('Only explicitly named icp-clean-* acceptance projects are allowed')


def docker(*args):
    result=subprocess.run([docker_cli(),*args],capture_output=True,text=True,encoding='utf-8',timeout=300)
    if result.returncode:raise RuntimeError('Docker snapshot operation failed; no secret output displayed')
    return result.stdout.strip()


def tree_hash(volume):
    command='find /volume -type f -exec sha256sum {} + | sort | sha256sum'
    value=docker('run','--rm','--pull','never','--network','none','--entrypoint','/bin/sh',
        '--mount','type=volume,src='+volume+',dst=/volume,readonly','postgres:16.6-alpine','-c',command)
    digest=value.split()[0]
    if not re.fullmatch('[a-f0-9]{64}',digest):raise RuntimeError('Invalid volume digest result')
    return digest


def backup(projects):
    validate_projects(projects)
    stamp=datetime.now().strftime('%Y%m%d-%H%M%S')
    directory=(ROOT/'.secrets/rebuild-backups'/stamp).resolve()
    if directory.exists():raise ValueError('Choose a new snapshot timestamp')
    directory.mkdir(parents=True)
    containers=[];volumes=[]
    for project in projects:
        ids=docker('ps','-aq','--filter','label=com.docker.compose.project='+project).splitlines()
        for cid in ids:
            item=json.loads(docker('inspect',cid))[0];labels=item['Config'].get('Labels',{})
            if labels.get('com.docker.compose.project')!=project:raise ValueError('Container ownership changed')
            containers.append({'id':cid,'name':item['Name'].lstrip('/'),'service':labels.get('com.docker.compose.service','')})
        names=docker('volume','ls','-q','--filter','label=com.docker.compose.project='+project).splitlines()
        for name in names:
            value=json.loads(docker('volume','inspect',name))[0]
            if value.get('Labels',{}).get('com.docker.compose.project')!=project:raise ValueError('Volume ownership changed')
            if not name.startswith(project+'_'):raise ValueError('Volume name is outside the isolated project namespace')
            if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]+',name):raise ValueError('Invalid volume name')
            volumes.append(name)
    if not containers or not volumes:raise ValueError('No isolated acceptance stack found')
    storage=lambda item:bool(re.search('postgres|mysql|redis|es01|minio|neo4j|weaviate',item['service']))
    for group in [[c for c in containers if not storage(c)],[c for c in containers if storage(c)]]:
        if group:docker('stop','--time','30',*[c['id'] for c in group])
    if any(json.loads(docker('inspect',c['id']))[0]['State']['Running'] for c in containers):raise RuntimeError('Stack not fully stopped')
    manifest={'date':datetime.now().isoformat(),'projects':projects,'containers':containers,'volumes':[],'stage':'backup_in_progress'}
    path=directory/'snapshot.json'
    for volume in volumes:
        archive=volume+'.tar.gz';before=tree_hash(volume)
        docker('run','--rm','--pull','never','--network','none','--entrypoint','/bin/sh',
            '--mount','type=volume,src='+volume+',dst=/volume,readonly',
            '--mount','type=bind,src='+directory.as_posix()+',dst=/backup',
            'postgres:16.6-alpine','-c','tar czf /backup/'+archive+' -C /volume .')
        file=directory/archive
        manifest['volumes'].append({'source_volume':volume,'archive':archive,'archive_sha256':hashlib.sha256(file.read_bytes()).hexdigest(),
            'file_tree_sha256':before,'bytes':file.stat().st_size})
        path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'backed_up_volume':volume,'bytes':file.stat().st_size}),flush=True)
    manifest['stage']='offline_backup_complete';path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Private offline snapshot completed. Original isolated volumes retained and stopped.')
    return path


def restore(snapshot):
    snapshot=snapshot.resolve();base=(ROOT/'.secrets/rebuild-backups').resolve()
    if not snapshot.is_relative_to(base):raise ValueError('Restore only an owned isolated snapshot')
    manifest=json.loads(snapshot.read_text(encoding='utf-8'));validate_projects(manifest['projects'])
    if manifest['stage']!='offline_backup_complete':raise ValueError('Incomplete backup cannot be restored')
    stamp=datetime.now().strftime('%Y%m%d-%H%M%S');restored=[]
    existing=set(docker('volume','ls','-q').splitlines())
    if len({v['source_volume'] for v in manifest['volumes']})!=len(manifest['volumes']):raise ValueError('Duplicate volume entries in snapshot')
    for item in manifest['volumes']:
        if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]+\.tar\.gz',item['archive']):raise ValueError('Unsafe archive filename')
        if not any(item['source_volume'].startswith(project+'_') for project in manifest['projects']):raise ValueError('Source volume outside isolated project')
        file=(snapshot.parent/item['archive']).resolve()
        if not file.is_relative_to(snapshot.parent):raise ValueError('Archive resolves outside snapshot directory')
        if hashlib.sha256(file.read_bytes()).hexdigest()!=item['archive_sha256']:raise ValueError('Backup digest differs')
        name='icp-restore-'+stamp+'-'+item['source_volume']
        if name in existing:raise ValueError('Restore volume already exists; nothing overwritten')
        docker('volume','create','--label','icp.restore-id='+stamp,name)
        docker('run','--rm','--pull','never','--network','none','--entrypoint','/bin/sh',
            '--mount','type=volume,src='+name+',dst=/volume',
            '--mount','type=bind,src='+snapshot.parent.as_posix()+',dst=/backup,readonly',
            'postgres:16.6-alpine','-c','tar xzf /backup/'+item['archive']+' -C /volume')
        same=tree_hash(name)==item['file_tree_sha256']
        restored.append({'source_volume':item['source_volume'],'restored_volume':name,'file_tree_matches':same})
        if not same:raise RuntimeError('Restored volume content digest differs')
        print(json.dumps({'restored_volume':name,'file_tree_matches':True}),flush=True)
    report={'date':datetime.now().isoformat(),'stage':'volume_bytes_restored_not_application_verified','volumes':restored,
        'passed':all(v['file_tree_matches'] for v in restored),'source_volumes_retained':True,
        'scope':'regular-file content hashes compared; tar preserves modes/links; application/workflow/sample verification must follow separately'}
    target=ROOT/'.secrets/rebuild-backups'/('restore-'+stamp+'.json');target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'outputs/isolated-volume-restoration.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return target


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--projects',nargs='+');parser.add_argument('--restore-from',type=Path)
    args=parser.parse_args()
    try:
        if args.restore_from:restore(args.restore_from)
        elif args.projects:backup(args.projects)
        else:parser.error('Choose isolated projects to back up, or an isolated snapshot to restore')
    except Exception as exc:print('Isolated snapshot stopped; error type='+type(exc).__name__);raise SystemExit(1)
