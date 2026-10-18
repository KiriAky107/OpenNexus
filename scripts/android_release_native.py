"""Compare packaged release ELF code/data with its original compiled library.

Nonallocated metadata may differ; runtime headers and allocated sections may not.
This is not runtime evidence.
"""
import hashlib,struct
from pathlib import Path
from release_plan import require
from verify_android_package import MACHINES


def region(stream,offset,size,total):
    require(type(offset) is int and type(size) is int and 0<=offset<=total and 0<=size<=total-offset,
        'ANDROID_RELEASE_ELF_RANGE')
    stream.seek(offset);digest=hashlib.sha256();left=size
    while left:
        block=stream.read(min(left,1024*1024));require(bool(block),'ANDROID_RELEASE_ELF_TRUNCATED')
        digest.update(block);left-=len(block)
    return digest.hexdigest()


def elf(path,abi):
    path=Path(path);require(abi in MACHINES,'ANDROID_RELEASE_ELF_ABI_UNSUPPORTED')
    require(path.is_file() and not path.is_symlink(),'ANDROID_RELEASE_ELF_INPUT')
    size=path.stat().st_size;require(64<=size<=512*1024**2,'ANDROID_RELEASE_ELF_SIZE')
    with path.open('rb') as stream:
        header=struct.unpack('<16sHHIQQQIHHHHHH',stream.read(64))
        require(header[0][:7]==b'\x7fELF\x02\x01\x01' and header[1]==3 and header[2]==MACHINES[abi][0]
            and header[3]==1 and header[8]==64,'ANDROID_RELEASE_ELF_HEADER_OR_ABI')
        require(header[9]==56 and 0<header[10]<64 and header[5]<=size and header[9]*header[10]<=size-header[5],
            'ANDROID_RELEASE_ELF_PROGRAM_TABLE')
        stream.seek(header[5]);programs=[list(struct.unpack('<IIQQQQQQ',stream.read(56))) for _ in range(header[10])]
        require(header[11]==64 and 0<header[12]<1024 and 0<=header[13]<header[12]
            and header[6]<=size and header[11]*header[12]<=size-header[6],'ANDROID_RELEASE_ELF_SECTION_TABLE')
        stream.seek(header[6]);rows=[struct.unpack('<IIQQQQIIQQ',stream.read(64)) for _ in range(header[12])]
        strings=rows[header[13]]
        require(strings[1]==3 and 0<strings[5]<=1024*1024 and strings[4]<=size and strings[5]<=size-strings[4],
            'ANDROID_RELEASE_ELF_SECTION_NAMES')
        stream.seek(strings[4]);names=stream.read(strings[5]);allocated={};all_names=set();nonallocated_metadata=0
        for row in rows[1:]:
            require(row[0]<len(names),'ANDROID_RELEASE_ELF_SECTION_NAME')
            end=names.find(b'\0',row[0]);require(end>=row[0],'ANDROID_RELEASE_ELF_SECTION_NAME')
            try:name=names[row[0]:end].decode('ascii','strict')
            except UnicodeError:require(False,'ANDROID_RELEASE_ELF_SECTION_NAME')
            require(bool(name) and len(name)<=256 and name not in all_names,'ANDROID_RELEASE_ELF_DUPLICATE_SECTION')
            all_names.add(name)
            if row[2]&2:
                require(row[5]<=256*1024**2,'ANDROID_RELEASE_ELF_SECTION_SIZE')
                value='NOBITS' if row[1]==8 else region(stream,row[4],row[5],size)
                allocated[name]=dict(type=row[1],flags=row[2],address=row[3],size=row[5],alignment=row[8],sha256=value)
            elif name.startswith(('.debug','.zdebug')) or name in ('.symtab','.strtab'):
                nonallocated_metadata+=row[5]
        require('.text' in allocated and '.dynamic' in allocated and '.dynsym' in allocated and bool(programs),
            'ANDROID_RELEASE_ELF_RUNTIME_SECTIONS_MISSING')
    # e_shoff and section counts can change after stripping; runtime header cannot.
    runtime_header=[header[0].hex(),*header[1:6],header[7],header[8],header[9],header[10]]
    return dict(abi=abi,runtime_header=runtime_header,program_headers=programs,allocated_sections=allocated,
        nonallocated_debug_or_symbol_bytes=nonallocated_metadata)


def same_runtime(compiled,packaged,abi):
    before=elf(compiled,abi);after=elf(packaged,abi)
    require(before['runtime_header']==after['runtime_header'],'ANDROID_RELEASE_ELF_RUNTIME_HEADER_CHANGED')
    require(before['program_headers']==after['program_headers'],'ANDROID_RELEASE_ELF_LOADER_SEGMENTS_CHANGED')
    require(before['allocated_sections']==after['allocated_sections'],'ANDROID_RELEASE_ELF_CODE_DATA_EXPORTS_CHANGED')
    return dict(abi=abi,allocated_sections=before['allocated_sections'],program_headers=before['program_headers'],
        exact_runtime_header_and_loader_segments_and_allocated_code_data_exports=True,
        compiled_nonallocated_debug_or_symbol_bytes=before['nonallocated_debug_or_symbol_bytes'],
        packaged_nonallocated_debug_or_symbol_bytes=after['nonallocated_debug_or_symbol_bytes'],
        actual_native_execution_verified=False)
