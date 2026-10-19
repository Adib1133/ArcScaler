"""Small structurally valid PE fixtures; never loaded or executed."""
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[1]


def pe_bytes(dll=True, marker=b'fixture', imports=(), exports=()):
    data = bytearray(1024)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 60, 128)
    data[128:132] = b'PE\0\0'
    struct.pack_into('<HHIIIHH', data, 132, 0x8664, 1, 0, 0, 0, 240, 0x2022 if dll else 0x22)
    struct.pack_into('<H', data, 152, 0x20b)
    struct.pack_into('<I', data, 152 + 56, 8192)
    struct.pack_into('<I', data, 152 + 60, 512)
    data[392:400] = b'.text\0\0\0'
    struct.pack_into('<IIII', data, 400, 512, 4096, 512, 512)
    data[512:512+len(marker)] = marker
    if imports:
        struct.pack_into('<II',data,152+112+8,4096+64,20*(len(imports)+1))
        for i,name in enumerate(imports):
            pos=512+256+i*48
            struct.pack_into('<IIIII',data,512+64+i*20,0,0,0,4096+256+i*48,0)
            encoded=name.encode()+b'\0';data[pos:pos+len(encoded)]=encoded
    if exports:
        data.extend(bytes(4096))
        struct.pack_into('<IIII', data, 400, len(data)-512, 4096, len(data)-512, 512)
        rva = lambda pos: pos-512+4096
        struct.pack_into('<II', data, 152+112, rva(1024), 40)
        struct.pack_into('<IIIII', data, 1024+20, len(exports), len(exports), rva(1100), rva(1300), rva(1500))
        pos=1700
        for i,name in enumerate(sorted(exports)):
            struct.pack_into('<I', data, 1100+i*4, rva(512))
            struct.pack_into('<I', data, 1300+i*4, rva(pos))
            struct.pack_into('<H', data, 1500+i*2, i)
            encoded=name.encode()+b'\0';data[pos:pos+len(encoded)]=encoded;pos+=len(encoded)
    return bytes(data)


def runtime_bytes(name='libxess.dll', marker=b'native runtime'):
    from core.runtime_validation import REQUIRED_EXPORTS
    return pe_bytes(marker=marker, exports=REQUIRED_EXPORTS[name])


def package(root, tag='v0.9.4'):
    version = Path(root) / 'versions' / tag
    version.mkdir(parents=True, exist_ok=True)
    for name in ('OptiScaler.dll', 'libxess.dll', 'libxess_dx11.dll', 'libxess_fg.dll', 'libxell.dll', 'fakenvapi.dll'):
        (version / name).write_bytes(pe_bytes(marker=name.encode()))
    (version / 'OptiScaler.ini').write_bytes((ROOT / 'assets/versions/v0.9.4/OptiScaler.ini').read_bytes())
    (version / 'fakenvapi.ini').write_text('[fakenvapi]\nforce_reflex=0\n', encoding='utf-8')
    return version


def game(root, name='unique-test-game.exe', imports=('d3d12.dll',)):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    exe = root / name
    exe.write_bytes(pe_bytes(dll=False, imports=imports))
    return exe
