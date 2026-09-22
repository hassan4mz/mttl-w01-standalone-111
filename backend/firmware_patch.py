#!/usr/bin/env python3
"""Deterministic MTTL-W01 local builder from user-obtained 1.0.105 or LG U+ 1.0.66 (stdlib only)."""
from pathlib import Path
import hashlib,ssl,struct,urllib.error,urllib.request
URLS=('https://dev.toi.ommeq.com/mef/firmware1.0.115/MTTL-W01_V1.0.105.fwr','https://dev.toi.ommeq.com/mef/firmware1.0.105/MTTL-W01_V1.0.105.fwr')
SRC105_NAME='MTTL-W01_V1.0.105.fwr'
SRC66_NAME='comMTTL-W01_1.0.66_LGU.fwr'
SRC_SHA='f78aee903d392f2b8470c845d9e76c33d91fdcf1da57e6c7e714cbedc9edaefa'
APP105_SHA='7ba0ba4b4bbf041b84d54c10a9105c6c74ac59a86a6bc2cc3da5a72bce4815c9'
APP66_SHA='8ed36a772158f1438800db8d0e5e9c1353a5b219d35f7cc7289ba335631f290c'
APP110_SHA='a72d17347be654fe3f5b655fc8a6e5caec6ce0d37ef21805a77130672119c654'
FWR66_SHA='d780b578af69d52f3a05191a8e7d91a20e05085a912722327481cd5663682c04'
FWR110_SHA='318f44567cd90613a3a485a66bb398c80bc60c891699772d438c9cfd6cb33059'
FWR111_SHA='7766d8c309ea77361a31b2af0c710ee24d05d076a48ecc0c8ee9b627bb288dfb'
KEY105=bytes.fromhex('3f5a27e8d8fb85f97c8397d3ad879f97')
KEYLGU=bytes.fromhex('3f5a27e8d8fb85f9bd4d51196c4f5159')
P105_66=[(101976, '313035', '363600'), (132484, '6c6f672e746f692e6f6d6d65712e636f6d000000', '6864736c6f672e6c6775706c75732e636f2e6b72'), (132688, '6c6f672e746f692e6f6d6d65712e636f6d000000', '6864736c6f672e6c6775706c75732e636f2e6b72'), (316460, '3c00', '0807'), (316640, '3c00', '0807'), (318700, '64', '6d'), (318702, '76', '66'), (318704, '746f692e6f', '6f6e656d32'), (318710, '6d65712e636f6d0000000000', '2e75706c75732e636f2e6b72'), (318752, '6465762e74', '62726b322e'), (318758, '692e6f6d', '6e656d32'), (318763, '65712e636f6d000000000000', '2e75706c75732e636f2e6b72')]
P66_110=[(13198, '01f0fdfb', '00bf00bf'), (13224, '01f0f0fb', '00bf00bf'), (13334, '0ff2843001f0b7fb', '00bf00bf00bf00bf'), (13464, '0ff2383001f076fb', '00bf00bf00bf00bf'), (13476, '0ff2a436', '00bf00bf'), (13483, 'd0', 'e0'), (13509, 'd0', 'e0'), (13541, 'd5', 'e0'), (13554, '0ff29830', '00bf00bf'), (14236, '0d5b5350494620496e665d537069634e564d43616c53746f72653d3d3e204269744d6f64653d256420437075436c6b3d25640d0a000000000d5b5350494620496e665d537069634e564d43616c53746f7265284269744d6f64652025642c20435055436c6b202564293a2043616c6962726174696f6e2053746f7265643a2042617564526174653d3078257820526444756d6d7943796c653d307825782044656c61794c696e653d307825780d0a00000d5b53504946204572725d537069634e564d43616c53746f726520457272284f', '0021cdf84c13cdf850134ff0ff30704700b515f0b3fb00f085f800bdffb589b03d4e0024002506eb851797f83f0018b1012000fa05f004430135052df3dbddf894033649884205d0cdf89413cdf898431f2609e0ddf8980380ea0406002e01d100f04cb8cdf898432d4ff869012801d000f044b87869012801d000f03fb83f69002f01da00f03ab80025012000fa05f0064230d06b461ca111f8012b12b103f8012bf9e715f1300203f8012b3a2203f8012b012000fa05f0044206d06f2203f8012b6e2203f8012b07e06f2203f8012b'), (14445, '667365743d30782578292c2057723d307825782052643d30782578200d0a000d5b53504946204572725d537069634e564d43616c53746f7265', '2203f8012b03f8012b0d2203f8012b0a2203f8012b1a466946521a3846694600230ff0c0f90135052dc6db09b0bde8ff40e069012870477570'), (14503, '2054686520666c617368206d656d6f7279284030782578203d203078257829206973206e6f742061626c6520746f2062652077726974652c20457261736520697420666972737421210d0a', '6576656e743a6f6e6f66663a00b4410510501001111046051010b50df0a5fa962006f07dfc024c2046002119f0d1ff10bd447c051000000000000000000000000000000000000000000000'), (64414, '53', '55'), (64416, '4ff0ff30', 'f3f7fcfd'), (64720, '09f022f9', 'f3f76cfd'), (64868, 'e0690128', 'f3f728fd'), (64908, '53', '55'), (71202, '03f08efa', 'fff7f5fb'), (97830, '01f0dcfb', 'f8f7f3ff'), (101976, '363600', '313130'), (120382, 'f7', 'f3'), (120384, '80ba', 'e7bb'), (131342, '2de9f043', 'fff744bf')]
P110_111_WIRE=[(65618, '2e', '37'), (65620, 'fd02', '1c03'), (67272, 'da282568013c3d3658', 'de25387508233e3a54'), (67284, 'bf9ef188d03e3d760b103e32', 'ad8bbf9ad177747d61455159'), (67657, '79', '78'), (70029, '7b', '7a'), (84788, '220c0807', 'd30bf802'), (84836, '270c0504', 'd30be902'), (101994, '61', '60'), (130532, 'd8fb85f9bd4d51196c4f51593f5a27e8d8fb85f9bd4d51196c4f5159', 'c84e89bff1bf101b6ebf19a02fab26e7db2aa4bfed6f53e92db641e4'), (131362, '8a1d9b86ca09427b568f6c6f57c978a817b8d06b9ddb01e55eeb340e9a14ccad2513e753ca0d473ca2ee1fb05cf1f77bd41fb70485090fb45ce8764e57f1c0adf316d80b240000b54b39d1b7494930a81fa83c0c9805bd69513624f0651f198e9a10c0fb3801a75dd95b709deb11c3ad9d1561b24781b2bfa12a69ddd3213bc86590db698581bfdd50886cde5cf130a84bda10da610e2cb65cb188b879a53b', '27e8c84e8db0b507513a2c4765e6353a2c88deb181ddfd7c111158f05b39343a26d4202a95445d0c5409abee5049c0fb26f8c84e0749bc6d11ef6c5e3b1f3b79d41f0c052d40bdd1f1ab28b91218b7182a39fcf7a4f35dff58019366568820722230dcbd7a0e70b2715f6eff41e41f7e270827df85d942ba95e64c0953e92fe7375d5a4b81bff9bb1259c1b75159b2a225a838b80801be4d50392cb9514855'), (131522, '26c8f09b8d19b2bfa12988b8a0a27ebb681ca28b6c0e43b2a5ee87b25171c88b9a10c2fb3801a55d51002855517b3e7b25c82a0c2d04bd', '23cb2b0c4907a5f4aeeeafb0f11b3c8a27c8270c2306426d53a97cf241ec57201dc0c62a2d838d7558317697551fd720b759d5d39529b7'), (131578, '568924f03ea93e5ec23c6453790ea6b5571964f03ea93d5e6b38c8d985d8b4e5b5ee9db45379b2a202e898093ee956ba2ee4c1b777590f3326802a938f5159ba8fe2686f51c937f12070990983fbfdbbae689fb847a0', '5fc95c7758712f8223035cffe1f999554e356697792227eb2ac0d92b8fd1b99c715f93b890a63c1c3755270c13069d6559a66c6f521f2fe7375d370c8802bd6daeeedeb041e42fefd31fc9017a0e38b241a46cf051e6'), (132497, '6e', '6f'), (140076, '93', '91'), (140078, 'f9a7', '0ba4'), (327940, 'c648', '3e65')]
FILES={'66':SRC66_NAME,'110':'comMTTL-W01_1.0.110_UP.fwr','111':'comMTTL-W01_1.0.111_UP_CHILDLOCK_OTA49217.fwr'}
SHAS={'66':FWR66_SHA,'110':FWR110_SHA,'111':FWR111_SHA}
class PatchError(RuntimeError):pass
def sha(x):return hashlib.sha256(x).hexdigest()
def crypt(x,key):
 y=bytearray(x)
 for i in range(16,len(y)):y[i]^=key[(i-16)&15]
 return bytes(y)
def patch(buf,items,stage):
 for off,old,new in items:
  old,new=bytes.fromhex(old),bytes.fromhex(new)
  if len(old)!=len(new):raise PatchError(f'{stage} fixed-size mismatch at {off:#x}')
  if buf[off:off+len(old)]!=old:raise PatchError(f'{stage} baseline mismatch at {off:#x}')
  buf[off:off+len(old)]=new
def fixcs(p):struct.pack_into('<I',p,len(p)-4,sum(p[:-4])&0xffffffff)
def validate(p):
 if len(p)!=327944 or struct.unpack_from('<II',p,0)!=(0x500F4,0x10006000):raise PatchError('bad FWR header')
 if p[40:54]!=b'LGU+-TAP-HW002':raise PatchError('wrong model')
 if struct.unpack_from('<I',p,len(p)-4)[0]!=(sum(p[:-4])&0xffffffff):raise PatchError('bad checksum')
def inspect_wire(wire):
 if len(wire)!=327944:raise PatchError(f'bad FWR size: {len(wire)}')
 marker=wire[8:16];key=KEYLGU if marker==b'\xff'*8 else KEY105 if marker==b'81958711' else None
 if key is None:raise PatchError(f'unsupported marker: {marker.hex()}')
 p=crypt(wire,key);validate(p);raw=p[16+101976:16+101979]
 return {'sha256':sha(wire),'size':len(wire),'model':p[40:54].decode('ascii'),'version':raw.rstrip(b'\0').decode('ascii','replace')}
def build66(src105):
 if sha(src105)!=SRC_SHA or src105[8:16]!=b'81958711':raise PatchError('source is not pinned Voltra 1.0.105')
 p=bytearray(crypt(src105,KEY105));validate(p);app=bytearray(p[16:])
 if sha(app)!=APP105_SHA:raise PatchError('105 app mismatch')
 patch(app,P105_66,'105->66');p[8:16]=b'\xff'*8;p[16:]=app;fixcs(p);validate(p)
 if sha(p[16:])!=APP66_SHA:raise PatchError('66 app mismatch')
 out=crypt(bytes(p),KEYLGU)
 if sha(out)!=FWR66_SHA:raise PatchError('66 FWR mismatch')
 return out
def validate66(wire66):
 if sha(wire66)!=FWR66_SHA:raise PatchError(f'1.0.66 source SHA256 mismatch: {sha(wire66)}')
 meta=inspect_wire(wire66)
 if meta['version']!='66':raise PatchError('1.0.66 source version mismatch')
 return wire66
def build110_from66(wire66):
 wire66=validate66(wire66);p=bytearray(crypt(wire66,KEYLGU));validate(p);app=bytearray(p[16:])
 if sha(app)!=APP66_SHA:raise PatchError('66 app mismatch')
 patch(app,P66_110,'66->110');p[16:]=app;fixcs(p);validate(p)
 if sha(p[16:])!=APP110_SHA:raise PatchError('110 app mismatch')
 out=crypt(bytes(p),KEYLGU)
 if sha(out)!=FWR110_SHA:raise PatchError('110 FWR mismatch')
 return out
def build111_from66(wire66):
 wire=bytearray(build110_from66(wire66));patch(wire,P110_111_WIRE,'110->111 wire');out=bytes(wire)
 if sha(out)!=FWR111_SHA:raise PatchError(f'111 FWR mismatch: {sha(out)}')
 if inspect_wire(out)['version']!='111':raise PatchError('111 version mismatch')
 return out
def classify_source(data):
 h=sha(data)
 if h==SRC_SHA:return '105'
 if h==FWR66_SHA:return '66'
 raise PatchError(f'unsupported source SHA256: {h}; expected 1.0.105 {SRC_SHA} or LG U+ 1.0.66 {FWR66_SHA}')
def build_from_source(data,version):
 version=str(version);kind=classify_source(data)
 wire66=build66(data) if kind=='105' else validate66(data)
 if version=='66':out=wire66
 elif version=='110':out=build110_from66(wire66)
 elif version=='111':out=build111_from66(wire66)
 else:raise PatchError('version must be 66, 110 or 111')
 if sha(out)!=SHAS[version]:raise PatchError('final SHA mismatch')
 return out
def build110(src):return build_from_source(src,'110')
def build111(src):return build_from_source(src,'111')

def _existing_source(folder):
 folder=Path(folder)
 # Canonical names first, then any user-supplied *.fwr. Generated targets are ignored unless exact 66.
 candidates=[]
 for name in (SRC105_NAME,SRC66_NAME):
  p=folder/name
  if p.is_file():candidates.append(p)
 for p in sorted(folder.glob('*.fwr')):
  if p not in candidates:candidates.append(p)
 invalid=[]
 for p in candidates:
  try:
   kind=classify_source(p.read_bytes());print('[SOURCE CACHE]',p.name,kind,sha(p.read_bytes()));return kind,p
  except Exception as e:invalid.append(f'{p.name}: {e}')
 return None,invalid
def prepare_source(folder):
 folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
 found,detail=_existing_source(folder)
 if found:return found,detail
 errors=list(detail)
 for url in URLS:
  print('[DOWNLOAD]',url)
  try:
   try:
    with urllib.request.urlopen(url,timeout=30) as r:data=r.read()
   except urllib.error.URLError as e:
    reason=str(getattr(e,'reason',e)).lower()
    if 'certificate' not in reason and 'ssl' not in reason:raise
    print('[WARN] legacy HTTPS verification failed; transport retry. Source identity remains exact-SHA pinned.')
    with urllib.request.urlopen(url,timeout=30,context=ssl._create_unverified_context()) as r:data=r.read()
   if classify_source(data)!='105':raise PatchError('downloaded file is not pinned 1.0.105')
   p=folder/SRC105_NAME;p.write_bytes(data);print('[SOURCE SAVED]',p.name,SRC_SHA,'from',url);return '105',p
  except Exception as e:errors.append(f'{url}: {e}')
 raise PatchError('source acquisition failed. Place a user-obtained 1.0.105 or LG U+ 1.0.66 FWR in this folder and retry: '+str(folder)+' | '+' | '.join(errors))
def build_version(folder,version,output_folder=None):
 folder=Path(folder);kind,path=prepare_source(folder);data=path.read_bytes();version=str(version)
 outdir=Path(output_folder) if output_folder is not None else folder;outdir.mkdir(parents=True,exist_ok=True)
 out=outdir/FILES[version];wire=build_from_source(data,version);out.write_bytes(wire)
 print('[OUTPUT]',out.name,sha(wire),'from source',kind,path.name);return out
def build_all(folder,output_folder=None):return [build_version(folder,v,output_folder) for v in ('66','110','111')]
if __name__=='__main__':build_all(Path(__file__).resolve().parent)
