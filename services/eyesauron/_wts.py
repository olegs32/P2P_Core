# services/eyesauron/_wts.py — запуск процессов в интерактивных сессиях (WTS)
#
# Порт launcher.py проекта EyeSauron: узел P2P_Core живёт в session 0 (SYSTEM),
# захват рабочего стола возможен только из сессии пользователя — сервис через
# WTSEnumerateSessions + WTSQueryUserToken + CreateProcessAsUserW запускает
# лёгкий хелпер захвата внутри каждой активной сессии.
#
# Отличия от оригинала: нормальные структуры STARTUPINFO/PROCESS_INFORMATION
# вместо сырых байтовых буферов, флаг CREATE_NO_WINDOW (хелпер не должен
# мигать консолью на рабочем столе пользователя), именованные константы.

import ctypes
import ctypes.wintypes as wt

WTSAPI32 = ctypes.WinDLL('wtsapi32')
KERNEL32 = ctypes.WinDLL('kernel32')
ADVAPI32 = ctypes.WinDLL('advapi32')
USERENV = ctypes.WinDLL('userenv')

WTSActive = 0                            # WTS_CONNECTSTATE_CLASS::WTSActive

CREATE_NEW_PROCESS_GROUP = 0x00000020
CREATE_UNICODE_ENVIRONMENT = 0x00000400
CREATE_NO_WINDOW = 0x08000000
STILL_ACTIVE = 259


class WTS_SESSION_INFO(ctypes.Structure):
    _fields_ = [
        ('SessionId', wt.DWORD),
        ('pWinStationName', ctypes.c_wchar_p),
        ('State', ctypes.c_int),
    ]


class STARTUPINFO(ctypes.Structure):
    _fields_ = [
        ('cb', wt.DWORD),
        ('lpReserved', wt.LPWSTR),
        ('lpDesktop', wt.LPWSTR),
        ('lpTitle', wt.LPWSTR),
        ('dwX', wt.DWORD), ('dwY', wt.DWORD),
        ('dwXSize', wt.DWORD), ('dwYSize', wt.DWORD),
        ('dwXCountChars', wt.DWORD), ('dwYCountChars', wt.DWORD),
        ('dwFillAttribute', wt.DWORD), ('dwFlags', wt.DWORD),
        ('wShowWindow', wt.WORD), ('cbReserved2', wt.BYTE),
        ('lpReserved2', ctypes.c_void_p),
        ('hStdInput', wt.HANDLE), ('hStdOutput', wt.HANDLE),
        ('hStdError', wt.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ('hProcess', wt.HANDLE),
        ('hThread', wt.HANDLE),
        ('dwProcessId', wt.DWORD),
        ('dwSessionId', wt.DWORD),
    ]


def enable_privilege(name: str):
    """Включить привилегию текущего процесса (SeTcbPrivilege и т.п.)."""
    TOKEN_ADJUST_PRIVILEGES = 0x0020
    TOKEN_QUERY = 0x0008
    SE_PRIVILEGE_ENABLED = 0x00000002

    class LUID_AND_ATTR(ctypes.Structure):
        _fields_ = [('Luid', ctypes.c_int64), ('Attributes', wt.DWORD)]

    class TOKEN_PRIVILEGES(ctypes.Structure):
        _fields_ = [('PrivilegeCount', wt.DWORD),
                    ('Privileges', LUID_AND_ATTR * 1)]

    ADVAPI32.OpenProcessToken.argtypes = [
        ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.HANDLE)]

    h = wt.HANDLE()
    if not ADVAPI32.OpenProcessToken(
            ctypes.c_void_p(-1),                      # псевдо-хэндл себя
            TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
            ctypes.byref(h)):
        raise ctypes.WinError()
    luid = ctypes.c_int64()
    ADVAPI32.LookupPrivilegeValueW(None, name, ctypes.byref(luid))
    tp = TOKEN_PRIVILEGES()
    tp.PrivilegeCount = 1
    tp.Privileges[0].Luid = luid.value
    tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED
    ADVAPI32.AdjustTokenPrivileges(h, False, ctypes.byref(tp), 0, None, None)
    KERNEL32.CloseHandle(h)


def current_session_id() -> int:
    """Номер сессии текущего процесса (0 = session 0 SYSTEM)."""
    sid = wt.DWORD()
    KERNEL32.ProcessIdToSessionId(ctypes.windll.kernel32.GetCurrentProcessId(),
                                  ctypes.byref(sid))
    return sid.value


def get_active_sessions() -> list[int]:
    """id активных интерактивных сессий (без session 0)."""
    buf = ctypes.POINTER(WTS_SESSION_INFO)()
    count = wt.DWORD()
    if not WTSAPI32.WTSEnumerateSessionsW(None, 0, 1,
                                          ctypes.byref(buf), ctypes.byref(count)):
        return []
    sessions = [buf[i].SessionId for i in range(count.value)
                if buf[i].State == WTSActive and buf[i].SessionId != 0]
    WTSAPI32.WTSFreeMemory(buf)
    return sessions


def launch_in_session(cmdline: str, session_id: int,
                      directory: str | None = None) -> tuple[int, int]:
    """Запустить cmdline от имени пользователя активной сессии.

    directory — рабочий каталог процесса (нужен dev-режиму для запуска
    скрипта из корня репозитория). Возвращает (pid, process_handle).
    Handle закрывает вызывающий (kill_process / is_alive / CloseHandle).
    """
    token = wt.HANDLE()
    if not WTSAPI32.WTSQueryUserToken(session_id, ctypes.byref(token)):
        raise ctypes.WinError(f'WTSQueryUserToken({session_id})')
    dup = wt.HANDLE()
    # TOKEN_ASSIGN_PRIMARY|DUPLICATE|QUERY|ADJUST_DEFAULT|ADJUST_SESSIONID = 0xF01FF
    # SecurityImpersonation = 2, TokenPrimary = 1
    if not ADVAPI32.DuplicateTokenEx(token, 0xF01FF, None, 2, 1, ctypes.byref(dup)):
        err = ctypes.WinError()
        KERNEL32.CloseHandle(token)
        raise err

    env_block = ctypes.c_void_p()
    USERENV.CreateEnvironmentBlock(ctypes.byref(env_block), dup, False)

    si = STARTUPINFO()
    si.cb = ctypes.sizeof(STARTUPINFO)
    pi = PROCESS_INFORMATION()

    # lpCommandLine мутирует WinAPI — только записываемый буфер
    cmd_buf = ctypes.create_unicode_buffer(cmdline)
    result = ADVAPI32.CreateProcessAsUserW(
        dup, None, cmd_buf, None, None, False,
        CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | CREATE_UNICODE_ENVIRONMENT,
        env_block, directory, ctypes.byref(si), ctypes.byref(pi))

    if env_block:
        USERENV.DestroyEnvironmentBlock(env_block)
    KERNEL32.CloseHandle(dup)
    KERNEL32.CloseHandle(pi.hThread)

    if not result:
        err = ctypes.WinError(f'CreateProcessAsUserW(session {session_id})')
        KERNEL32.CloseHandle(token)
        raise err
    KERNEL32.CloseHandle(token)
    return pi.dwProcessId, pi.hProcess


def is_alive(handle: int) -> bool:
    code = wt.DWORD()
    KERNEL32.GetExitCodeProcess(handle, ctypes.byref(code))
    return code.value == STILL_ACTIVE


def kill_process(handle: int):
    KERNEL32.TerminateProcess(handle, 0)
    KERNEL32.CloseHandle(handle)
