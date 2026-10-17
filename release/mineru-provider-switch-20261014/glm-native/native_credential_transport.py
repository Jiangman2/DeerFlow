"""Pinned native encryption transport; credentials memory-only, no dependency install."""
import base64
import hashlib
import json
import re

# Set by the immutable delivery packager. No executable downloaded at runtime.
HELPER = None
HELPER_SHA = None

LAUNCHER = r'''
import base64,hashlib,json,os,resource,stat,subprocess,sys,tempfile
resource.setrlimit(resource.RLIMIT_CORE,(0,0))
step='identity_and_input'
try:
 if os.geteuid()!=1000 or os.getegid()!=1000: raise ValueError('identity')
 raw=sys.stdin.buffer.read(8388609)
 if len(raw)>8388608: raise ValueError('bound')
 packet=json.loads(raw)
 if set(packet)!={'helper','request'}: raise ValueError('shape')
 binary=base64.b64decode(packet['helper'],validate=True)
 if not 1024<len(binary)<5242880 or hashlib.sha256(binary).hexdigest()!=sys.argv[1]: raise ValueError('hash')
 if binary[:4]!=b'\x7fELF' or binary[4:6]!=b'\x02\x01' or int.from_bytes(binary[18:20],'little')!=183: raise ValueError('arch')
 if len(os.environ.get('SYSTEM_AES_KEY','').encode())!=32: raise ValueError('aes')
 if os.environ.get('AUTO_MIGRATE')!='false': raise ValueError('migration')
 # Only the public pinned executable is written into a fresh private directory.
 # No request, environment, plaintext or ciphertext is written to this directory.
 step='private_executable_stage'
 directory=tempfile.mkdtemp(prefix='.weknora-credential-',dir='/tmp')
 path=directory+'/helper'
 fd=None
 try:
  fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_RDWR|os.O_NOFOLLOW,0o500)
  with os.fdopen(os.dup(fd),'wb') as f: f.write(binary); f.flush(); os.fsync(f.fileno())
  os.fchmod(fd,0o500)
  meta=os.fstat(fd)
  if not stat.S_ISREG(meta.st_mode) or meta.st_uid!=1000 or meta.st_nlink!=1: raise ValueError('file')
  os.lseek(fd,0,os.SEEK_SET)
  with os.fdopen(os.dup(fd),'rb') as f:
   if hashlib.sha256(f.read(5242881)).hexdigest()!=sys.argv[1]: raise ValueError('staged_hash')
  os.close(fd); fd=None
  env={'SYSTEM_AES_KEY':os.environ['SYSTEM_AES_KEY'],'GOTRACEBACK':'none','GOMAXPROCS':'1'}
  step='native_execution'
  child=subprocess.run([path],input=json.dumps(packet['request']).encode(),
   stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,timeout=15)
  if child.returncode or child.stderr or len(child.stdout)>8192: raise ValueError('helper')
  step='native_receipt'
  answer=json.loads(child.stdout)
  if answer.get('credential_helper')!='PASS': raise ValueError('result')
  print(json.dumps(answer))
 finally:
  if fd is not None: os.close(fd)
  if os.path.lexists(path): os.unlink(path)
  os.rmdir(directory)
except BaseException:
 print(json.dumps({'credential_transport':'UNCONFIRMED','step':step,'raw_error_printed':False}))
 sys.exit(1)
'''


def valid_key(value):
    return isinstance(value,str) and 0<len(value)<=4096 and not value.startswith('enc:v1:') and all(33<=ord(c)<=126 for c in value)


def valid_cipher(value):
    if not isinstance(value,str) or not re.fullmatch(r'enc:v1:[A-Za-z0-9_-]{39,5500}',value): return False
    try:
        raw=base64.urlsafe_b64decode(value[7:]+'='*((-len(value[7:]))%4))
        return 29<=len(raw)<=4124 and base64.urlsafe_b64encode(raw).decode().rstrip('=')==value[7:]
    except Exception: return False


def packet(request):
    if not isinstance(HELPER,bytes) or hashlib.sha256(HELPER).hexdigest()!=HELPER_SHA:
        raise RuntimeError('native_helper_hash_changed')
    return json.dumps({'helper':base64.b64encode(HELPER).decode(),'request':request}).encode()


def run(native,pod,request):
    code,raw=native.call(['exec','-i','-n','deer-flow',pod['metadata']['name'],'-c','app',
        '--','gosu','1000:1000','python3','-I','-B','-c',LAUNCHER,HELPER_SHA],
        data=packet(request),timeout=60,limit=8192,checked=False)
    try: value=json.loads(raw)
    except Exception: raise RuntimeError('native_credential_transport_no_safe_receipt') from None
    if code:
        step=value.get('step')
        if step in ('identity_and_input','private_executable_stage','native_execution','native_receipt'):
            raise RuntimeError('native_credential_failed:'+step)
        raise RuntimeError('native_credential_transport_failed')
    mode=request['mode']
    if set(value)!={'credential_helper','mode','native_roundtrip'}|({'ciphertext'} if mode=='encrypt' else set()):
        raise RuntimeError('native_credential_response_shape')
    if value.get('credential_helper')!='PASS' or value.get('mode')!=mode or value.get('native_roundtrip') is not True:
        raise RuntimeError('native_credential_roundtrip_failed')
    if mode=='encrypt' and not valid_cipher(value.get('ciphertext')):
        raise RuntimeError('native_ciphertext_invalid')
    return value
