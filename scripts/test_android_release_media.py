"""Synthetic boundary and real SQLite/ZIP checks, no device execution claims."""
from contextlib import closing
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from android_release_observation import APP, DATABASES, admit_attachment
from android_release_snapshot import fingerprint, inspect_stopped_snapshot, same_originals
from android_release_media import ASSET, FILES, IMAGE_BODY, IMAGE_BYTES, IMAGE_HASH, ReleaseMedia, original_media, validate_export


class MediaBoundaries(unittest.TestCase):
    def fixture(self, root):
        snapshot=root/'raw';snapshot.mkdir();(snapshot/'vault/.ainote').mkdir(parents=True)
        originals={FILES[0]:(IMAGE_BODY+'![]('+ASSET+')').encode(),FILES[1]:json.dumps({'nodes':[{'id':'own','type':'file','file':ASSET}],'edges':[]}).encode(),ASSET:IMAGE_BYTES}
        for name,raw in originals.items():
            target=snapshot/'vault'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        with closing(sqlite3.connect(snapshot/DATABASES[0])) as db:
            db.executescript('CREATE TABLE drafts(id,file_id,path,base_hash,generation,saved_generation,content,edit_session); CREATE TABLE save_intents(operation_id,draft_id,generation,file_id,path,expected,content,state,error); CREATE TABLE image_intents(operation_id,draft_id,path,generation,asset_path,hash,size,state,error,content_before,content_after);')
            for i,name in enumerate(FILES):
                body=originals[name].decode();sha=hashlib.sha256(originals[name]).hexdigest()
                db.execute('INSERT INTO drafts VALUES(?,?,?,?,?,?,?,?)',('draft'+str(i),'file'+str(i),name,sha,3,3,body,'before'))
                db.execute('INSERT INTO save_intents VALUES(?,?,?,?,?,?,?,?,?)',('save'+str(i),'draft'+str(i),3,'file'+str(i),name,'',body,'committed',None))
                db.execute('INSERT INTO image_intents VALUES(?,?,?,?,?,?,?,?,?,?,?)',('image'+str(i),'draft'+str(i),name,2,ASSET,IMAGE_HASH,len(IMAGE_BYTES),'committed',None,'original',body))
            db.commit()
        with closing(sqlite3.connect(snapshot/DATABASES[1])) as db:
            db.executescript('CREATE TABLE files(id,path,hash,revision,deleted); CREATE TABLE outbox(operation_id,file_id,revision,path,hash,operation,state); CREATE TABLE operations(operation_id,fingerprint,state,result);')
            for i,(name,raw) in enumerate(originals.items()):
                sha=hashlib.sha256(raw).hexdigest();operation='save'+str(i) if i<2 else 'image0'
                db.execute('INSERT INTO files VALUES(?,?,?,?,?)',('file'+str(i),name,sha,2,0))
                db.execute('INSERT INTO outbox VALUES(?,?,?,?,?,?,?)',(operation,'file'+str(i),2,name,sha,'write','pending'))
                db.execute('INSERT INTO operations VALUES(?,?,?,?)',(operation,'f'*64,'committed','original own receipt'))
            db.commit()
        record=dict(package=APP,files={},app_stopped_before_and_after=True,credential_store_copied=False,SQL_executed_on_device=False)
        for name in [*DATABASES,*('vault/'+n for n in originals)]:
            path=snapshot/name;record['files'][name]=dict(bytes=path.stat().st_size,sha256=fingerprint(path))
        for db in DATABASES:
            for suffix in ('-wal','-shm'):record['files'][db+suffix]=None
        (snapshot/'snapshot.json').write_text(json.dumps(record),'utf-8')
        return snapshot,originals

    def test_attachment_admission_is_hash_sharded_png_only(self):
        self.assertEqual(admit_attachment(ASSET),'vault/'+ASSET)
        for name in ('../'+ASSET,ASSET.replace('.png','.db'),ASSET.replace(IMAGE_HASH[:2]+'/', 'xx/',1),'mobile-sync-auth.sqlite3',ASSET.upper()):
            with self.subTest(name=name),self.assertRaises(RuntimeError):admit_attachment(name)

    def test_real_sqlite_projection_keeps_raw_and_native_media_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);snapshot,originals=self.fixture(root)
            before={p.relative_to(snapshot).as_posix():fingerprint(p) for p in snapshot.rglob('*') if p.is_file()}
            projection=inspect_stopped_snapshot(snapshot,root/'working',FILES,attachment=ASSET)
            self.assertEqual(original_media(projection,originals)['original_GUI_save_ids'],['save0','save1'])
            self.assertEqual(original_media(projection,originals)['original_image_operation_ids'],['image0','image1'])
            self.assertEqual(before,{p.relative_to(snapshot).as_posix():fingerprint(p) for p in snapshot.rglob('*') if p.is_file()})
            after=deepcopy(projection);after['owned_files'][FILES[0]]['drafts'][0]['edit_session']='cold'
            same_originals(projection,after)
            after['owned_files'][FILES[0]]['images'][0]['operation_id']='other'
            with self.assertRaisesRegex(RuntimeError,'ORIGINAL_ROWS_CHANGED'):same_originals(projection,after)

    def test_image_error_duplicate_save_or_changed_bytes_cannot_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);snapshot,originals=self.fixture(root)
            base=inspect_stopped_snapshot(snapshot,root/'working',FILES,attachment=ASSET)
            for mode in ('image-error','duplicate-save','wrong-image','wrong-reference','unsaved'):
                value=deepcopy(base);raw=dict(originals);item=value['owned_files'][FILES[0]]
                if mode=='image-error':item['images'][0]['error']='unknown'
                if mode=='duplicate-save':item['saves']*=2
                if mode=='wrong-image':raw[ASSET]=b'changed'
                if mode=='wrong-reference':raw[FILES[0]]=b'changed'
                if mode=='unsaved':item['drafts'][0]['saved_generation']=2
                with self.subTest(mode=mode),self.assertRaises(RuntimeError):original_media(value,raw)

    def export(self,extra=False):
        originals={FILES[0]:b'original md',FILES[1]:b'{"nodes":[],"edges":[]}',ASSET:IMAGE_BYTES}
        manifest=dict(format='opennexus-content-export',schema=1,scope='markdown-canvas-images',files=[dict(path='vault/'+n,sha256=hashlib.sha256(b).hexdigest(),size=len(b)) for n,b in sorted(originals.items())])
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as z:
            for n,b in originals.items():z.writestr('vault/'+n,b)
            z.writestr('OpenNexus-export.json',json.dumps(manifest))
            if extra:z.writestr('vault/.ainote/host.sqlite3',b'not allowed')
        content=buffer.getvalue();ticket='12345678-1234-1234-1234-123456789abc'
        sealed=dict(ticket=ticket,filename='OpenNexus-content-'+ticket+'.zip',sha256=hashlib.sha256(content).hexdigest(),size=len(content),file_count=3,content_bytes=sum(map(len,originals.values())))
        delivery=dict(ticket=ticket,delivery_id='87654321-1234-1234-1234-123456789abc',state='saved',verified_at=1,sha256=sealed['sha256'],size=len(content))
        return delivery,sealed,content,originals

    def test_actual_zip_and_sealed_delivery_are_bound_to_each_original(self):
        delivery,sealed,content,originals=self.export()
        self.assertEqual(validate_export(delivery,sealed,content,originals)['sha256'],sealed['sha256'])
        for mode in ('hash','unknown','ticket','timestamp','content'):
            d=dict(delivery);s=dict(sealed);b=content
            if mode=='hash':d['sha256']='f'*64
            if mode=='unknown':d['state']='choosing'
            if mode=='ticket':s['ticket']='other'
            if mode=='timestamp':d['verified_at']=True
            if mode=='content':b+=b'changed'
            with self.subTest(mode=mode),self.assertRaises(RuntimeError):validate_export(d,s,b,originals)

    def test_even_hash_bound_private_file_is_refused_in_content_zip(self):
        with self.assertRaisesRegex(RuntimeError,'EXPORT_CLOSED_SCOPE'):validate_export(*self.export(extra=True))

    def test_physical_or_local_execution_rejected_before_binding_or_output(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict('os.environ',{'GITHUB_ACTIONS':'false'}),patch('android_release_media.bind_pair') as bind:
            out=Path(folder)/'new'
            with self.assertRaises(RuntimeError):ReleaseMedia(Path(folder),Path(folder),Path(folder),out)
            self.assertFalse(out.exists());bind.assert_not_called()

    def test_unapproved_phase_or_private_export_path_refused_before_observer(self):
        subject=object.__new__(ReleaseMedia)
        with self.assertRaises(RuntimeError):subject.phase('markdown','foreign')
        for path in ('mobile-sync-auth.sqlite3','exports/../credentials.json','exports/a.zip'):
            with self.subTest(path=path),self.assertRaises(RuntimeError):subject.extra_original(path)


if __name__=='__main__':unittest.main()
