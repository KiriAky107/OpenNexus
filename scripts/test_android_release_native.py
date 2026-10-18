from pathlib import Path
import struct,tempfile,unittest
import android_release_native as subject


def library(*,machine=183,code=b'owned-code',metadata=b'debug-original',entry=0,program_flags=5):
    names=b'\0.text\0.dynamic\0.dynsym\0.debug_info\0.shstrtab\0'
    code=code.ljust(32,b'\0');dynamic=b'original-dynamic'.ljust(32,b'\0');symbols=b'original-exports'.ljust(32,b'\0')
    offset=256;payload=bytearray(offset);payload.extend(code+dynamic+symbols+metadata+names)
    string_offset=offset+96+len(metadata);section_offset=len(payload)
    rows=[(0,)*10]
    for name,address,file_offset,size,kind,flags in [('.text',4096,offset,32,1,6),('.dynamic',8192,offset+32,32,6,3),
        ('.dynsym',12288,offset+64,32,11,2),('.debug_info',0,offset+96,len(metadata),1,0),('.shstrtab',0,string_offset,len(names),3,0)]:
        rows.append((names.index(name.encode()),kind,flags,address,file_offset,size,0,0,1,0))
    for row in rows:payload.extend(struct.pack('<IIQQQQIIQQ',*row))
    payload[:64]=struct.pack('<16sHHIQQQIHHHHHH',b'\x7fELF\x02\x01\x01'+b'\0'*9,3,machine,1,entry,64,section_offset,0,64,56,1,64,len(rows),5)
    payload[64:120]=struct.pack('<IIQQQQQQ',1,program_flags,0,0,0,offset+96,offset+96,4096)
    return bytes(payload)


class ReleaseNativeBinding(unittest.TestCase):
    def test_debug_metadata_can_change_without_changing_actual_code_data_or_exports(self):
        with tempfile.TemporaryDirectory() as folder:
            a=Path(folder)/'original.so';b=Path(folder)/'packaged.so';a.write_bytes(library());b.write_bytes(library(metadata=b''))
            value=subject.same_runtime(a,b,'arm64-v8a')
            self.assertTrue(value['exact_runtime_header_and_loader_segments_and_allocated_code_data_exports'])
            self.assertFalse(value['actual_native_execution_verified']);self.assertEqual(value['packaged_nonallocated_debug_or_symbol_bytes'],0)

    def test_code_or_dynamic_exports_header_or_loader_cannot_be_explained_as_stripping(self):
        with tempfile.TemporaryDirectory() as folder:
            a=Path(folder)/'original.so';b=Path(folder)/'packaged.so';a.write_bytes(library())
            variants=[library(code=b'changed-code'),library(entry=4096),library(program_flags=7)]
            for text in (b'original-dynamic',b'original-exports'):
                raw=bytearray(library());at=raw.index(text);raw[at]^=1;variants.append(raw)
            for raw in variants:
                b.write_bytes(raw)
                with self.subTest(hash=hash(bytes(raw))),self.assertRaises(RuntimeError):subject.same_runtime(a,b,'arm64-v8a')

    def test_wrong_abi_or_truncated_or_foreign_section_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'input.so'
            for raw in (library(machine=62),library()[:60],library()[:-1],b'x'*512):
                p.write_bytes(raw)
                with self.assertRaises(RuntimeError):subject.elf(p,'arm64-v8a')
            p.write_bytes(library(machine=62));self.assertEqual(subject.elf(p,'x86_64')['abi'],'x86_64')
            with self.assertRaises(RuntimeError):subject.elf(p,'armeabi-v7a')
            raw=bytearray(library(machine=62));header=struct.unpack('<16sHHIQQQIHHHHHH',raw[:64]);section=header[6]
            # Two allocated sections with the same name cannot hide a mutation.
            struct.pack_into('<I',raw,section+2*64,struct.unpack_from('<I',raw,section+64)[0]);p.write_bytes(raw)
            with self.assertRaisesRegex(RuntimeError,'DUPLICATE_SECTION'):subject.elf(p,'x86_64')

if __name__=='__main__':unittest.main()
