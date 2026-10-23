"""Real packet/parser/files/once recorder with controlled device output only."""
from copy import deepcopy
import binascii
import json
import shlex
from pathlib import Path
import struct
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zlib

import android_sync_image_host as host
import android_sync_image_pixels as pixels


def chunk(kind,body):return struct.pack('>I',len(body))+kind+body+struct.pack('>I',binascii.crc32(kind+body)&0xffffffff)


def png(values, width=8, height=8, mode=0, channels=4):
    rows=[];prior=bytes(width*channels)
    for y in range(height):
        raw=bytes(v for p in values[y*width:(y+1)*width] for v in p[:channels]);filtered=bytearray()
        for x,value in enumerate(raw):
            a=raw[x-channels] if x>=channels else 0;b=prior[x];c=prior[x-channels] if x>=channels else 0
            p=a+b-c;distances=(abs(p-a),abs(p-b),abs(p-c))
            predictor=(0,a,b,(a+b)//2,(a,b,c)[distances.index(min(distances))])[mode]
            filtered.append((value-predictor)&255)
        rows.append(bytes([mode])+filtered);prior=raw
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,6 if channels==4 else 2,0,0,0))+chunk(b'IDAT',zlib.compress(b''.join(rows)))+chunk(b'IEND',b'')


def ready():
    return dict(nonce='12345678-1234-1234-1234-123456789abc',phase=host.PHASE,package=host.APP,uid=10216,window=4,
        display=0,screen=[0,0,1000,1600],canvas=[0,100,1000,1500],image=[100,300,420,540],elapsed_ms=1000,stable_ms=640,
        title_verified=True,exact_image_alt=host.ASSET,visible_unobscured=True,no_keyboard=True)


def packet(image=None, *, frames=None):
    image=image or ready()['image']
    if frames is None:
        _,values=pixels.source_samples(image);frames=[png([(*p,255) for p in patch]) for patch in values]
    return pixels.MAGIC+struct.pack('>I',4)+b''.join(struct.pack('>I',len(raw))+raw for raw in frames)


class Process:
    def __init__(self):self.returncode=None;self.terminate=Mock(side_effect=self.terminated);self.kill=self.terminate
    def poll(self):return self.returncode
    def terminated(self):self.returncode=-15
    def communicate(self,timeout):
        if self.returncode is None:self.returncode=0
        return b'OPENNEXUS_RELEASE_SYNC_IMAGE_PHASE_OK release-sync-image gui_guard_only=1 decoded_image_verified=0\n',b''


class ImagePixelChecks(unittest.TestCase):
    def test_actual_original_decode_filters_RGB_RGBA_and_scaled_sample_mapping(self):
        self.assertEqual(pixels.decode_png(pixels.IMAGE_BYTES,2,2),[(226,64,98,255),(34,167,134,255),(55,103,211,255),(247,198,72,255)])
        for image in ([100,300,420,540],[10,20,74,84],[20,30,91,130]):
            frames,value=pixels.validate_packet(packet(image),image)
            self.assertEqual(len(frames),4);self.assertTrue(value['four_original_source_pixel_centers_verified'])
            self.assertEqual([v['source_crop'] for v in value['observations']],host.validate_ready({**ready(),'image':image,'canvas':[0,0,1000,1500]},1100))
        values=[(i,i*2,i*3,255) for i in range(64)]
        for mode in range(5):
            for channels in (3,4):self.assertEqual(pixels.decode_png(png(values,mode=mode,channels=channels),8,8),values)

    def test_blank_wrong_color_order_transparency_or_single_bad_pixel_refused(self):
        frames,_=pixels.validate_packet(packet(),ready()['image'])
        variants=[list(reversed(frames)),[png([(255,255,255,255)]*64)]*4,[png([(0,0,0,255)]*64)]*4]
        original=pixels.decode_png(frames[0],8,8)
        for replacement in ((0,0,0,255),(*original[0][:3],0)):
            edited=original.copy();edited[0]=replacement;variants.append([png(edited),*frames[1:]])
        for value in variants:
            with self.assertRaisesRegex(RuntimeError,'PIXEL_MISMATCH'):pixels.validate_packet(packet(frames=value),ready()['image'])

    def test_packet_PNG_CRC_truncation_scope_inflate_and_dimension_bounds(self):
        good=packet();frames,_=pixels.validate_packet(good,ready()['image'])
        bad_frame=bytearray(frames[0]);bad_frame[-1]^=1
        bad_pngs=[bytes(bad_frame),png([(1,2,3,255)]*64,width=4,height=16),frames[0]+b'extra',
            frames[0][:33]+chunk(b'tEXt',b'no arbitrary metadata')+frames[0][33:],
            frames[0][:33]+chunk(b'IDAT',zlib.compress(b'0'*10000))+chunk(b'IEND',b'')]
        variants=[good[:-1],good+b'extra',b'bad'+good,good[:len(pixels.MAGIC)]+struct.pack('>I',5)+good[len(pixels.MAGIC)+4:]]
        variants += [packet(frames=[bad,*frames[1:]]) for bad in bad_pngs]
        for value in variants:
            with self.assertRaises((RuntimeError,zlib.error)):pixels.validate_packet(value,ready()['image'])


class ImageHostChecks(unittest.TestCase):
    def test_closed_duplicate_foreign_stale_boolean_or_rect_ready_rejected(self):
        raw=json.dumps(ready(),separators=(',',':')).encode();self.assertEqual(host.closed_json(raw),ready())
        for bad in (b'{"x":1,"x":1}',raw+b' ',b'[]'):
            with self.assertRaises((RuntimeError,ValueError)):host.validate_ready(host.closed_json(bad),1100)
        for key,value in (('package',host.APP+'.dev'),('phase','release-sync-read-canvas'),('display',1),('uid',True),
            ('visible_unobscured',False),('nonce','bad'),('image',[0,0,1000,1600]),('screen',[-1,0,1000,1600]),
            ('stable_ms',599),('elapsed_ms',1101),('extra','no')):
            modified={**ready(),key:value}
            with self.assertRaises(RuntimeError):host.validate_ready(modified,1100)
        with self.assertRaises(RuntimeError):host.validate_ready(ready(),3001)

    def subject(self, folder, *, failure=None, partial=False):
        root=Path(folder);tools=root/'private-tools';(tools/'driver').mkdir(parents=True)
        (tools/'driver/driver.apk').write_bytes(b'original independently signed test driver fixture')
        output=root/'evidence';output.mkdir();commands=[];reads=0;value=ready()
        subject=SimpleNamespace(output=output,tools=tools,args=SimpleNamespace(serial='emulator-5562'),adb=Path('not-launched-adb'),
            activity=host.APP+'/.MainActivity',identity={'boot_id':'11111111-1111-1111-1111-111111111111'},phases=[],stop=Mock())
        subject.observer=SimpleNamespace(observe_identity=Mock(return_value=subject.identity))
        apk='/data/app/~~AAECAwQFBgcICQoLDA0ODw==/'+host.DRIVER+'-Dw4NDAsKCQgHBgUEAwIBAA==/base.apk'
        def shell(*args,**kw):
            nonlocal reads
            stdout=b''
            if args==('pidof',host.APP):stdout=b'1234\n'
            elif args==('pm','path',host.DRIVER):stdout=('package:'+apk+'\n').encode()
            elif args==('sha256sum',apk):stdout=(host.digest(tools/'driver/driver.apk')+'  '+apk+'\n').encode()
            elif args[:2]==('sh','-c'):
                self.assertIn(shlex.join(('readlink','-f',apk)),args[2]);self.assertIn('test ! -L /data/app',args[2]);stdout=(apk+'\n').encode()
            elif args==('cat','/proc/uptime'):stdout=b'1.100 0\n'
            elif args[:3]==('run-as',host.DRIVER,'sh'):
                reads+=1;stdout=b'{"nonce":' if partial and reads==1 else json.dumps(value,separators=(',',':')).encode()
            elif args[:3]==('run-as',host.DRIVER,'cat'):stdout=b'finish\n'
            else:raise AssertionError(args)
            return SimpleNamespace(returncode=0,stdout=stdout,stderr=b'')
        def device(*args,**kw):
            commands.append((args,kw))
            if host.HELPER in str(args):
                if failure=='timeout':raise subprocess.TimeoutExpired('original',20,output=b'partial-private-binary')
                if failure=='pixels':return SimpleNamespace(returncode=0,stdout=packet(frames=[png([(0,0,0,255)]*64)]*4),stderr=b'')
                if failure=='helper':return SimpleNamespace(returncode=1,stdout=b'',stderr=b'OWNED_IMAGE_CROP_FAILED stage=capture no_fallback=1\n')
                return SimpleNamespace(returncode=0,stdout=packet(),stderr=b'')
            self.assertEqual(kw['input'],b'finish\n');return SimpleNamespace(returncode=0,stdout=b'',stderr=b'')
        subject.shell=shell;subject.device=device;subject.original_command=Mock(return_value=SimpleNamespace(returncode=0,stdout=b'',stderr=b''))
        return subject,commands

    def execute(self,subject,process):
        with patch.object(host,'require_hosted_emulator'),patch.object(host.subprocess,'Popen',return_value=process), \
            patch.object(host.ReleaseAuthBackground,'app_is_resumed',return_value=True),patch.object(host.time,'sleep'):
            return host.run_image(subject,'canvas-image')

    def test_original_once_command_real_recorder_partial_ready_and_approved_four_ROI_only(self):
        with tempfile.TemporaryDirectory() as folder:
            subject,commands=self.subject(folder,partial=True);result=self.execute(subject,Process())
            self.assertTrue(result['actual_four_source_ROI_pixels_verified']);self.assertEqual(len(commands),2)
            request=json.loads(commands[0][1]['input']);self.assertEqual(set(request),{'nonce','boot_id','ready_sha256','pid'})
            self.assertNotIn('sourceCrop',str(commands));self.assertEqual(len(list(subject.output.glob('*.png'))),4)
            for path in subject.output.glob('*.png'):self.assertEqual(len(pixels.decode_png(path.read_bytes(),8,8)),64)
            self.assertEqual(json.loads((subject.output/'canvas-image-compositor-result.json').read_bytes())['returncode'],0)
            with self.assertRaises(FileExistsError):self.execute(subject,Process())
            self.assertEqual(len(commands),2)

    def test_unknown_failed_or_wrong_pixels_stop_same_driver_without_finish_retry_or_binary_leak(self):
        for mode in ('timeout','helper','pixels'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                subject,commands=self.subject(folder,failure=mode);process=Process()
                with self.assertRaises((RuntimeError,subprocess.TimeoutExpired)):self.execute(subject,process)
                self.assertEqual(len(commands),1);process.terminate.assert_called_once();self.assertFalse(list(subject.output.glob('*.png')))
                self.assertFalse(subject.phases);self.assertFalse(list(subject.output.glob('*finish-intent*')))
                self.assertFalse(any(b'partial-private-binary' in p.read_bytes() for p in subject.output.iterdir()))
                with self.assertRaises(FileExistsError):self.execute(subject,Process())
                self.assertEqual(len(commands),1)

    def test_original_capture_recorder_refuses_duplicate_before_sending(self):
        with tempfile.TemporaryDirectory() as folder:
            subject,commands=self.subject(folder)
            command=('shell','-T','closed helper fixture')
            # Static original signal path exercises the real exclusive recorder.
            host.original_stdin(subject,'one',command,b'finish\n',timeout=1)
            with self.assertRaises(FileExistsError):host.original_stdin(subject,'one',command,b'finish\n',timeout=1)
            self.assertEqual(len(commands),1)

    def test_owner_change_does_not_capture_or_finish_even_with_valid_old_ready(self):
        for stage in ('before','after'):
            with self.subTest(stage=stage),tempfile.TemporaryDirectory() as folder:
                subject,commands=self.subject(folder);process=Process()
                observations=[subject.identity,{'boot_id':'22222222-2222-2222-2222-222222222222'}] if stage=='after' else [{'boot_id':'changed'}]
                subject.observer.observe_identity.side_effect=observations
                with self.assertRaisesRegex(RuntimeError,'OWNER_CHANGED'):self.execute(subject,process)
                self.assertEqual(len(commands),1 if stage=='after' else 0)
                self.assertFalse(list(subject.output.glob('*.png')));self.assertFalse(list(subject.output.glob('*finish-intent*')))
                process.terminate.assert_called_once()

    def test_exact_API36_randomized_driver_path_only_not_foreign_split_alias_or_bad_Base64(self):
        apk='/data/app/~~AAECAwQFBgcICQoLDA0ODw==/'+host.DRIVER+'-Dw4NDAsKCQgHBgUEAwIBAA==/base.apk'
        self.assertEqual(host.installed_driver_path(('package:'+apk+'\n').encode()),apk)
        values=[apk.replace(host.DRIVER,host.APP),apk.replace('/base.apk','/split.apk'),apk.replace('/data/app/','/data/local/tmp/'),
            apk.replace('/base.apk','/../base.apk'),apk.replace('~~',''),apk.replace('AAECAwQFBgcICQoLDA0ODw==','AAECAwQFBgcICQoLDA0ODx=='),
            apk.replace('==','='),apk.replace('/base.apk','/dir/base.apk')]
        for value in values:
            with self.assertRaises(RuntimeError):host.installed_driver_path(('package:'+value+'\n').encode())
        for value in (('package:'+apk+'\n')*2,'package:'+apk+'\n\n','package:'+apk+'\nsecret','package:'+apk+'\v'):
            with self.assertRaises(RuntimeError):host.installed_driver_path(value.encode())

    def test_original_helper_path_read_alias_or_hash_failure_retained_before_any_GUI_launch(self):
        for mode in ('foreign','alias','bad-hash','command-failed','missing-pid'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                subject,commands=self.subject(folder);original=subject.shell
                def shell(*args,**kw):
                    result=original(*args,**kw)
                    if mode=='foreign' and args[:2]==('pm','path'):result.stdout=result.stdout.replace(host.DRIVER.encode(),host.APP.encode())
                    if mode=='alias' and args[:2]==('sh','-c'):result.stdout=b'/data/app/alias/base.apk\n'
                    if mode=='bad-hash' and args[:1]==('sha256sum',):result.stdout=result.stdout.replace(result.stdout[:64],b'0'*64)
                    if mode=='command-failed' and args[:2]==('pm','path'):result.returncode=1;result.stderr=b'owned path unavailable\n'
                    if mode=='missing-pid' and args[:1]==('pidof',):result.returncode=1;result.stdout=b''
                    return result
                subject.shell=shell
                with patch.object(host,'require_hosted_emulator'),patch.object(host.subprocess,'Popen') as instrument:
                    with self.assertRaises(RuntimeError):host.run_image(subject,'canvas-image')
                    instrument.assert_not_called()
                self.assertFalse(commands);self.assertFalse(list(subject.output.glob('*compositor*')))
                if mode=='missing-pid':
                    self.assertTrue((subject.output/'canvas-image-image-process-result.json').exists())
                    self.assertFalse((subject.output/'canvas-image-helper-binding-intent.json').exists());continue
                self.assertTrue((subject.output/'canvas-image-driver-pm-path.stdout').exists())
                failure=json.loads((subject.output/'canvas-image-helper-binding-failure.json').read_bytes())
                self.assertFalse(failure['image_instrumentation_or_capture_dispatched'])
                self.assertFalse(failure['automatic_retry'])


if __name__=='__main__':unittest.main()
