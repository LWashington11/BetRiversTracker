' BetRivers Poker Tracker - Unofficial — Windows Daily Launcher
' -------------------------------------------------------
' Called by the Start Menu / Desktop shortcut.
' 1. Starts the PostgreSQL 16 service (idempotent — safe to call when already running).
' 2. Launches Streamlit bound to localhost:8501.
' 3. Opens the browser after a short delay so Streamlit has time to start.
'
' Requirements: venv must already exist (created by the installer).
' -------------------------------------------------------

Option Explicit

Dim fso, wsh
Set fso = CreateObject("Scripting.FileSystemObject")
Set wsh = CreateObject("WScript.Shell")

' ── Resolve app root (two levels up from installer\windows\) ──────────────────
Dim scriptDir, appDir
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)   ' …\installer\windows
appDir    = fso.GetParentFolderName(fso.GetParentFolderName(scriptDir))  ' app root

' ── Kill any existing app instance (prevents process accumulation) ─────────────
' streamlit.exe is a thin wrapper that spawns python.exe as a child.  Killing
' only streamlit.exe orphans the python.exe processes.  We use WMI to find
' every python.exe whose command line references our app directory and
' terminate its entire process tree (children first, then parent).
On Error Resume Next
Dim objWMI, col, proc
Set objWMI = GetObject("winmgmts:\\.\root\cimv2")

' 1. Kill streamlit.exe processes from our venv
Set col = objWMI.ExecQuery( _
    "SELECT ProcessId FROM Win32_Process WHERE Name = 'streamlit.exe' " & _
    "AND CommandLine LIKE '%" & Replace(appDir, "\", "\\") & "%'")
For Each proc In col
    ' /T = tree kill (children first), /F = force
    wsh.Run "taskkill /F /T /PID " & proc.ProcessId, 0, True
Next
Set col = Nothing

' 2. Kill any remaining python.exe processes from our venv that reference main.py
Set col = objWMI.ExecQuery( _
    "SELECT ProcessId FROM Win32_Process WHERE Name = 'python.exe' " & _
    "AND CommandLine LIKE '%" & Replace(appDir, "\", "\\") & "%'")
For Each proc In col
    wsh.Run "taskkill /F /T /PID " & proc.ProcessId, 0, True
Next
Set col = Nothing

Set objWMI = Nothing
WScript.Sleep 500   ' brief pause to let processes fully exit
On Error GoTo 0

' ── Start PostgreSQL 16 service (ignore errors — already running is fine) ─────
' EDB installer registers the service as "postgresql-x64-16".
wsh.Run "net start postgresql-x64-16", 0, True

' ── Locate the venv Streamlit entry-point ─────────────────────────────────────
Dim streamlitExe, mainPy
streamlitExe = appDir & "\venv\Scripts\streamlit.exe"
mainPy       = appDir & "\app\main.py"

If Not fso.FileExists(streamlitExe) Then
    MsgBox "Streamlit not found at:" & vbCrLf & streamlitExe & vbCrLf & vbCrLf & _
           "Please re-run the BetRivers Poker Tracker - Unofficial installer.", _
           vbCritical, "BetRivers Poker Tracker - Unofficial"
    WScript.Quit 1
End If

' ── Launch Streamlit in the background (hidden console window) ─────────────────
' Set working directory to app root so relative paths in the app resolve correctly.
wsh.CurrentDirectory = appDir

Dim cmd
cmd = Chr(34) & streamlitExe & Chr(34) & " run " & Chr(34) & mainPy & Chr(34) & _
      " --server.headless true"
wsh.Run cmd, 0, False   ' 0 = hidden, False = don't wait

' ── Give Streamlit ~3 seconds to start, then open the browser ─────────────────
WScript.Sleep 3000
wsh.Run "http://localhost:8501", 1, False

Set fso = Nothing
Set wsh = Nothing
