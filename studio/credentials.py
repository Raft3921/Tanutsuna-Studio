"""Stream keys live only in native Keychain/Credential Manager, never project JSON."""
import ctypes as c
import sys
SERVICE='com.tanutsuna.studio.youtube'


def _windows(action,value=''):
    from ctypes import wintypes as w
    class Credential(c.Structure):
        _fields_=[('Flags',w.DWORD),('Type',w.DWORD),('TargetName',w.LPWSTR),('Comment',w.LPWSTR),('LastWritten',w.FILETIME),('CredentialBlobSize',w.DWORD),('CredentialBlob',c.POINTER(c.c_ubyte)),('Persist',w.DWORD),('AttributeCount',w.DWORD),('Attributes',c.c_void_p),('TargetAlias',w.LPWSTR),('UserName',w.LPWSTR)]
    dll=c.WinDLL('Advapi32',use_last_error=True)
    target=SERVICE+'/stream'
    if action=='get':
        result=c.POINTER(Credential)();dll.CredReadW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,c.POINTER(c.POINTER(Credential))]
        if not dll.CredReadW(target,1,0,c.byref(result)):
            if c.get_last_error()==1168:return ''
            raise OSError('保存したキーを取得できません。')
        try:return c.string_at(result.contents.CredentialBlob,result.contents.CredentialBlobSize).decode('utf-8')
        finally:dll.CredFree(result)
    if action=='delete':
        dll.CredDeleteW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD]
        if not dll.CredDeleteW(target,1,0) and c.get_last_error()!=1168:raise OSError('保存したキーを削除できません。')
        return
    data=value.encode('utf-8');buffer=(c.c_ubyte*len(data)).from_buffer_copy(data)
    credential=Credential();credential.Type=1;credential.TargetName=target;credential.UserName='YouTube Live';credential.Persist=2
    credential.CredentialBlobSize=len(data);credential.CredentialBlob=c.cast(buffer,c.POINTER(c.c_ubyte))
    dll.CredWriteW.argtypes=[c.POINTER(Credential),w.DWORD]
    if not dll.CredWriteW(c.byref(credential),0):raise OSError('キーを資格情報ストアへ保存できません。')


def _mac(action,value=''):
    cf=c.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation');sec=c.CDLL('/System/Library/Frameworks/Security.framework/Security')
    ptr=c.c_void_p;owned=[]
    cf.CFStringCreateWithCString.argtypes=[ptr,c.c_char_p,c.c_uint32];cf.CFStringCreateWithCString.restype=ptr
    cf.CFDataCreate.argtypes=[ptr,c.c_void_p,c.c_long];cf.CFDataCreate.restype=ptr
    cf.CFDictionaryCreate.argtypes=[ptr,c.POINTER(ptr),c.POINTER(ptr),c.c_long,ptr,ptr];cf.CFDictionaryCreate.restype=ptr
    cf.CFRelease.argtypes=[ptr]
    cf.CFDataGetLength.argtypes=[ptr];cf.CFDataGetLength.restype=c.c_long
    cf.CFDataGetBytePtr.argtypes=[ptr];cf.CFDataGetBytePtr.restype=ptr
    sec.SecItemCopyMatching.argtypes=[ptr,c.POINTER(ptr)];sec.SecItemAdd.argtypes=[ptr,c.POINTER(ptr)];sec.SecItemUpdate.argtypes=[ptr,ptr];sec.SecItemDelete.argtypes=[ptr]
    def constant(name):return ptr.in_dll(sec,name).value
    def string(text):
        ref=cf.CFStringCreateWithCString(None,text.encode(),0x08000100);owned.append(ref);return ref
    def dictionary(values):
        keys=(ptr*len(values))(*values.keys());vals=(ptr*len(values))(*values.values())
        ref=cf.CFDictionaryCreate(None,keys,vals,len(values),None,None);owned.append(ref);return ref
    base={constant('kSecClass'):constant('kSecClassGenericPassword'),constant('kSecAttrService'):string(SERVICE),constant('kSecAttrAccount'):string('YouTube Live')}
    try:
        if action=='get':
            query=dict(base);query[constant('kSecReturnData')]=ptr.in_dll(cf,'kCFBooleanTrue').value
            result=ptr();status=sec.SecItemCopyMatching(dictionary(query),c.byref(result))
            if status==-25300:return ''
            if status:raise OSError('キーチェーンからキーを取得できません。')
            try:return c.string_at(cf.CFDataGetBytePtr(result),cf.CFDataGetLength(result)).decode('utf-8')
            finally:cf.CFRelease(result)
        if action=='delete':
            status=sec.SecItemDelete(dictionary(base))
            if status not in (0,-25300):raise OSError('キーチェーンのキーを削除できません。')
            return
        data=value.encode();ref=cf.CFDataCreate(None,c.c_char_p(data),len(data));owned.append(ref)
        status=sec.SecItemUpdate(dictionary(base),dictionary({constant('kSecValueData'):ref}))
        if status==-25300:status=sec.SecItemAdd(dictionary(dict(base,**{} )|{constant('kSecValueData'):ref}),None)
        if status:raise OSError('キーをキーチェーンへ保存できません。')
    finally:
        for ref in reversed(owned):cf.CFRelease(ref)


def stream_key(action,value=''):
    if sys.platform=='darwin':return _mac(action,value)
    if sys.platform=='win32':return _windows(action,value)
    raise OSError('このOSの資格情報ストアには対応していません。')
