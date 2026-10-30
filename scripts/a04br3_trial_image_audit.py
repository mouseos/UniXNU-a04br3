"""Read-only audit of ce66352 images. Requires pyfatfs; does not access hardware."""
import hashlib, json, struct, sys, tomllib, zlib
from pathlib import Path
from pyfatfs.PyFatFS import PyFatFS


def digest(data): return hashlib.sha256(data).hexdigest()
def audit(root):
    root = Path(root)
    artifact = root/'port/xnu/out/memory-ce66352-boot'
    m = json.loads((artifact/'manifest.json').read_text())
    checks, fat = {}, {}
    for mode in ('off', 'on'):
        variant = m['variants'][mode]
        image = (artifact/f'{mode}-boot.img').read_bytes()
        checks[mode+'_boot'] = digest(image) == variant['boot_sha256']
        ksize, rsize = struct.unpack_from('<I', image, 8)[0], struct.unpack_from('<I', image, 16)[0]
        page = 2048
        offset = page + (ksize + page - 1)//page*page
        payload = root/f'port/xnu/out/memory-pair-720-ce66352/memory-ce66352-720-{mode}-payload.img'
        checks[mode+'_payload'] = digest(image[offset+512:offset+rsize]) == digest(payload.read_bytes()) == variant['payload_sha256']
        stream = zlib.decompressobj(31)
        decompressed = stream.decompress(image[page+512:page+ksize])
        fd = Path(m['fd_path']).read_bytes()
        checks[mode+'_fd'] = digest(fd) == m['fd_sha256'] and decompressed[192:] == fd
        dtb = (root/'unixnu/uefi-a04br3/Resources/DTBs/a04br3.dtb').read_bytes()
        checks[mode+'_dtb'] = digest(dtb) == m['dtb_sha256'] and stream.unused_data.endswith(dtb) and not any(stream.unused_data[:-len(dtb)])
        fs = PyFatFS(str(payload), read_only=True)
        try:
            base = '/EFI/UniXNU/Payloads/'
            members = {name: digest(fs.readbytes(base+name)) for name in ('kernel.macho','kernel.macho.adt','kernel.macho.gpuaddon','kernel.macho.trustcache','scratch-data.img')}
            args = tomllib.loads(fs.readtext('/EFI/UniXNU/unixnu.toml'))['entry'][0]['args']
        finally: fs.close()
        metadata = json.loads(Path(variant['payload_meta']).read_text())
        checks[mode+'_kernel'] = members['kernel.macho'] == variant['kernel_sha256'] == metadata['kernel_sha256']
        checks[mode+'_adt'] = members['kernel.macho.adt'] == variant['adt_sha256'] == metadata['adt_sha256']
        checks[mode+'_args'] = args == variant['kernel_args'] == metadata['kernel_args']
        for member,key in (('kernel.macho.gpuaddon','gpu_addon_sha256'), ('kernel.macho.trustcache','trust_cache_sha256'), ('scratch-data.img','data_ramdisk_sha256')):
            checks[mode+'_'+key] = members[member] == metadata[key]
        fat[mode] = members
    checks['shared_kernel'] = fat['off']['kernel.macho'] == fat['on']['kernel.macho']
    checks['shared_data'] = fat['off']['scratch-data.img'] == fat['on']['scratch-data.img']
    checks['shared_addon'] = fat['off']['kernel.macho.gpuaddon'] == fat['on']['kernel.macho.gpuaddon']
    checks['shared_trust'] = fat['off']['kernel.macho.trustcache'] == fat['on']['kernel.macho.trustcache']
    checks['args_delta'] = m['variants']['on']['kernel_args'] == m['variants']['off']['kernel_args'] + ' mb=1'
    return {'checks': checks, 'fat': fat, 'manifest_sha256':digest((artifact/'manifest.json').read_bytes()), 'adt_equal':fat['off']['kernel.macho.adt']==fat['on']['kernel.macho.adt'], 'limitations':['ADTの乱数seedは異なる。完全な単一変数比較ではない。','各構成要素の一致はseedとaddonの完全な出自や実機動作を証明しない。']}

if __name__ == '__main__':
    result = audit(sys.argv[1]); print(json.dumps(result, ensure_ascii=False, indent=2))
    if not all(result['checks'].values()): raise SystemExit(1)
