; BetRivers Poker Tracker — Inno Setup Script
; Online installer: downloads Python 3.13 and PostgreSQL 16 if not already present.
;
; Build prerequisites:
;   Inno Setup 6.x  https://jrsoftware.org/isinfo.php
;
; Build command (from this directory):
;   ISCC.exe setup.iss
;
; Output: dist\BetRiversTracker-Setup-<version>.exe

#define AppName      "BetRivers Poker Tracker"
#define AppVersion   "1.0.0"
#define AppPublisher "LWashington11"
#define AppURL       "https://github.com/LWashington11/BetRiversTracker_private"
#define ReleasesURL  "https://github.com/LWashington11/BetRiversTracker_private/releases"

; ── Setup section ─────────────────────────────────────────────────────────────
[Setup]
AppId={{6E4F8D2A-3B1C-4A5D-9E7F-2C3D4E5F6A7B}}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#ReleasesURL}
DefaultDirName={localappdata}\Programs\BetRiversTracker
DefaultGroupName={#AppName}
AllowNoIcons=yes
; PostgreSQL installs system-wide and needs admin.
; net stop/start for the PG service also requires elevation.
PrivilegesRequired=admin
; We install to {localappdata} for the app files while requiring admin only
; for PostgreSQL setup and service management.  The admin running the installer
; IS the intended user (single-user desktop app), so {localappdata} is correct.
UsedUserAreasWarning=no
LicenseFile=..\..\LICENSE
OutputDir=dist
OutputBaseFilename=BetRiversTracker-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
MinVersion=10.0
ArchitecturesInstallIn64BitMode=x64compatible

; ── Languages ─────────────────────────────────────────────────────────────────
[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

; ── Optional tasks ────────────────────────────────────────────────────────────
[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

; ── Files to install ──────────────────────────────────────────────────────────
[Files]
Source: "..\..\app\*";               DestDir: "{app}\app";               Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\.streamlit\*";        DestDir: "{app}\.streamlit";        Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\requirements.txt";    DestDir: "{app}";                   Flags: ignoreversion
Source: "..\..\schema.sql";          DestDir: "{app}";                   Flags: ignoreversion
Source: "..\..\schema_hands_report_indexes.sql"; DestDir: "{app}";       Flags: ignoreversion
Source: "..\..\LICENSE";             DestDir: "{app}";                   Flags: ignoreversion
Source: "..\..\hand_histories\*";    DestDir: "{app}\hand_histories";    Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
Source: "launcher.vbs";              DestDir: "{app}\installer\windows"; Flags: ignoreversion

; ── Shortcuts ─────────────────────────────────────────────────────────────────
[Icons]
Name: "{group}\{#AppName}"; \
    Filename: "{sys}\wscript.exe"; \
    Parameters: """{app}\installer\windows\launcher.vbs"""; \
    WorkingDir: "{app}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; \
    Filename: "{sys}\wscript.exe"; \
    Parameters: """{app}\installer\windows\launcher.vbs"""; \
    WorkingDir: "{app}"; \
    Tasks: desktopicon

; ══════════════════════════════════════════════════════════════════════════════
;  Pascal Script
; ══════════════════════════════════════════════════════════════════════════════
[Code]

function GetTickCount: Cardinal;
  external 'GetTickCount@kernel32.dll stdcall';

var
  DbPassword:             string;
  RandSeed:              Cardinal;
  WasPostgresPreInstalled: Boolean;

{ ─────────────────────────────────────────────────────────────────────────── }
{  Low-level helpers                                                         }
{ ─────────────────────────────────────────────────────────────────────────── }

function NextRand(MaxVal: Integer): Integer;
begin
  RandSeed := RandSeed xor (RandSeed shl 13);
  RandSeed := RandSeed xor (RandSeed shr 17);
  RandSeed := RandSeed xor (RandSeed shl 5);
  Result := Integer(RandSeed mod Cardinal(MaxVal));
end;

{ Generate a 24-char alphanumeric password (safe for cmd.exe quoting). }
function GeneratePassword: string;
var
  Chars: string;
  I: Integer;
begin
  Chars := 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
  RandSeed := GetTickCount;
  if RandSeed = 0 then RandSeed := $DEADBEEF;
  Result := '';
  for I := 1 to 24 do
    Result := Result + Chars[1 + NextRand(Length(Chars))];
end;

{ Run an external process, return True on exit code 0. }
function RunProc(Exe, Params, WorkDir: string): Boolean;
var
  Code: Integer;
begin
  Result := Exec(Exe, Params, WorkDir, SW_HIDE, ewWaitUntilTerminated, Code)
            and (Code = 0);
end;

{ Run a PowerShell one-liner silently. }
function RunPS(Script: string): Boolean;
begin
  Result := RunProc(
    ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NonInteractive -ExecutionPolicy Bypass -Command "' + Script + '"',
    '');
end;

{ Download a URL to a local path. }
function DownloadFile(URL, Dest: string): Boolean;
begin
  Result := RunPS(
    'Invoke-WebRequest -Uri ''' + URL +
    ''' -OutFile ''' + Dest + ''' -UseBasicParsing');
end;

{ Update the wizard status label. }
procedure Status(Msg: string);
begin
  WizardForm.StatusLabel.Caption := Msg;
  WizardForm.Update;
end;

{ ─────────────────────────────────────────────────────────────────────────── }
{  PostgreSQL path helpers                                                   }
{ ─────────────────────────────────────────────────────────────────────────── }

function FindPgBinDir: string;
begin
  if DirExists('C:\Program Files\PostgreSQL\16\bin') then
    Result := 'C:\Program Files\PostgreSQL\16\bin'
  else if DirExists('C:\Program Files\PostgreSQL\15\bin') then
    Result := 'C:\Program Files\PostgreSQL\15\bin'
  else
    Result := '';
end;

function FindPgDataDir: string;
begin
  if DirExists('C:\Program Files\PostgreSQL\16\data') then
    Result := 'C:\Program Files\PostgreSQL\16\data'
  else if DirExists('C:\Program Files\PostgreSQL\15\data') then
    Result := 'C:\Program Files\PostgreSQL\15\data'
  else
    Result := '';
end;

function FindPgServiceName: string;
begin
  if DirExists('C:\Program Files\PostgreSQL\16') then
    Result := 'postgresql-x64-16'
  else if DirExists('C:\Program Files\PostgreSQL\15') then
    Result := 'postgresql-x64-15'
  else
    Result := '';
end;

{ ─────────────────────────────────────────────────────────────────────────── }
{  Dependency detection                                                      }
{ ─────────────────────────────────────────────────────────────────────────── }

function IsPythonInstalled: Boolean;
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\cmd.exe'), '/C where python >nul 2>&1',
       '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := (Code = 0);
end;

function IsPostgresInstalled: Boolean;
begin
  Result := DirExists('C:\Program Files\PostgreSQL\16') or
            DirExists('C:\Program Files\PostgreSQL\15');
end;

{ ─────────────────────────────────────────────────────────────────────────── }
{  Python resolution                                                         }
{ ─────────────────────────────────────────────────────────────────────────── }

function FindPython: string;
var
  Candidates: array[0..2] of string;
  WhereOutAnsi: AnsiString;
  WhereOut, Line: string;
  I, NL: Integer;
begin
  Candidates[0] := ExpandConstant('{localappdata}') +
                    '\Programs\Python\Python313\python.exe';
  Candidates[1] := 'C:\Program Files\Python313\python.exe';
  Candidates[2] := 'C:\Program Files (x86)\Python313\python.exe';

  for I := 0 to 2 do begin
    if FileExists(Candidates[I]) then begin
      Result := Candidates[I];
      Exit;
    end;
  end;

  if RunProc(ExpandConstant('{sys}\cmd.exe'),
     '/C where python > "' + ExpandConstant('{tmp}') + '\pypath.txt" 2>nul',
     '') then begin
    if LoadStringFromFile(
         ExpandConstant('{tmp}') + '\pypath.txt', WhereOutAnsi) then begin
      WhereOut := String(WhereOutAnsi);
      NL := Pos(#13, WhereOut);
      if NL > 0 then Line := Trim(Copy(WhereOut, 1, NL - 1))
      else            Line := Trim(WhereOut);
      if (Line <> '') and (Pos('WindowsApps', Line) = 0) then begin
        Result := Line;
        Exit;
      end;
    end;
  end;

  Result := '';
end;

{ ─────────────────────────────────────────────────────────────────────────── }
{  .env helpers                                                              }
{ ─────────────────────────────────────────────────────────────────────────── }

function ReadEnvPassword: string;
var
  EnvPath: string;
  EnvAnsi: AnsiString;
  EnvContent: string;
  P, NL: Integer;
begin
  Result := '';
  EnvPath := ExpandConstant('{app}\.env');
  if not FileExists(EnvPath) then Exit;
  if not LoadStringFromFile(EnvPath, EnvAnsi) then Exit;
  EnvContent := String(EnvAnsi);
  P := Pos('PGPASSWORD=', EnvContent);
  if P = 0 then Exit;
  P := P + Length('PGPASSWORD=');
  NL := P;
  while (NL <= Length(EnvContent)) and
        (EnvContent[NL] <> #13) and (EnvContent[NL] <> #10) do
    Inc(NL);
  Result := Trim(Copy(EnvContent, P, NL - P));
end;

procedure WriteEnvFile;
var
  EnvPath, Content: string;
begin
  EnvPath := ExpandConstant('{app}\.env');
  Content :=
    '# PostgreSQL connection settings (auto-generated by installer)' + #13#10 +
    '# Edit this file if you need to change database credentials.' + #13#10 +
    'PGUSER=postgres' + #13#10 +
    'PGPASSWORD=' + DbPassword + #13#10 +
    'PGHOST=localhost' + #13#10 +
    'PGPORT=5432' + #13#10 +
    'PGDATABASE=betrivers_tracker' + #13#10 +
    '' + #13#10 +
    '# Hand history directory (absolute or relative to project root)' + #13#10 +
    'HAND_HISTORY_DIR=./hand_histories' + #13#10;
  SaveStringToFile(EnvPath, Content, False);
end;

{ ─────────────────────────────────────────────────────────────────────────── }
{  PostgreSQL service control                                                }
{ ─────────────────────────────────────────────────────────────────────────── }

{ Poll pg_isready until PostgreSQL accepts connections (up to 30 s). }
function WaitForPostgres: Boolean;
var
  PgBin, PgReady: string;
  I, Code: Integer;
begin
  PgBin := FindPgBinDir;
  if PgBin = '' then begin
    Result := False;
    Exit;
  end;
  PgReady := PgBin + '\pg_isready.exe';
  if not FileExists(PgReady) then begin
    { Cannot verify — let psql report the real error later }
    Result := True;
    Exit;
  end;
  Result := False;
  for I := 1 to 30 do begin
    Exec(PgReady, '-h localhost -p 5432 -q', '',
         SW_HIDE, ewWaitUntilTerminated, Code);
    if Code = 0 then begin
      Result := True;
      Exit;
    end;
    Sleep(1000);
  end;
end;

{ Restart the PostgreSQL Windows service, then poll until ready. }
function RestartPostgres: Boolean;
var
  SvcName: string;
begin
  SvcName := FindPgServiceName;
  if SvcName = '' then begin
    Result := False;
    Exit;
  end;
  { Use & not && so start runs even if stop fails (already stopped) }
  RunProc(ExpandConstant('{sys}\cmd.exe'),
    '/C net stop "' + SvcName + '" 2>nul & net start "' + SvcName + '"', '');
  Result := WaitForPostgres;
end;

{ Test whether the given password authenticates as postgres. }
function TestPgAuth(Password: string): Boolean;
var
  PsqlExe: string;
begin
  PsqlExe := FindPgBinDir + '\psql.exe';
  if not FileExists(PsqlExe) then begin
    Result := False;
    Exit;
  end;
  { Use & (not &&) between set and psql so psql always runs.
    -w = never prompt for password.  -h/-p = explicit connection. }
  Result := RunProc(ExpandConstant('{sys}\cmd.exe'),
    '/C set PGPASSWORD=' + Password +
    '& "' + PsqlExe + '" -U postgres -h localhost -p 5432 -w -c ' +
    '"SELECT 1" >nul 2>&1', '');
end;

{ ALTER the postgres superuser password, authenticating with OldPwd. }
function AlterPassword(OldPwd, NewPwd: string): Boolean;
var
  PsqlExe: string;
begin
  PsqlExe := FindPgBinDir + '\psql.exe';
  if not FileExists(PsqlExe) then begin
    Result := False;
    Exit;
  end;
  Result := RunProc(ExpandConstant('{sys}\cmd.exe'),
    '/C set PGPASSWORD=' + OldPwd +
    '& "' + PsqlExe + '" -U postgres -h localhost -p 5432 -w -c ' +
    '"ALTER USER postgres PASSWORD ''' + NewPwd + ''';" 2>nul', '');
end;

{ Force-reset the postgres password by temporarily switching pg_hba.conf
  to local-trust mode so we can ALTER USER without the old password.
  We are guaranteed admin by PrivilegesRequired=admin.

  Sequence:  stop PG  →  write trust config  →  start PG  →
             ALTER USER  →  stop PG  →  restore config  →  start PG }
function ForceResetPgPassword(NewPwd: string): Boolean;
var
  PgDataDir, HbaPath, HbaBackup, PsqlExe, SvcName: string;
begin
  Result := False;
  PgDataDir := FindPgDataDir;
  PsqlExe := FindPgBinDir + '\psql.exe';
  SvcName := FindPgServiceName;
  if (PgDataDir = '') or not FileExists(PsqlExe) or (SvcName = '') then Exit;

  HbaPath   := PgDataDir + '\pg_hba.conf';
  HbaBackup := PgDataDir + '\pg_hba.conf.installer_backup';
  if not FileExists(HbaPath) then Exit;

  { 1. Back up original pg_hba.conf }
  if not CopyFile(HbaPath, HbaBackup, False) then Exit;

  { 2. Stop PG so the config file is not in use }
  RunProc(ExpandConstant('{sys}\cmd.exe'),
    '/C net stop "' + SvcName + '" 2>nul', '');
  Sleep(2000);

  { 3. Write trust-only rules via PowerShell.  Check the return value —
       if the write fails we must not start PG expecting trust mode. }
  if not RunPS(
    'Set-Content -Path ''' + HbaPath + ''' -Value @(' +
    '''local all all trust'',' +
    '''host all all 127.0.0.1/32 trust'',' +
    '''host all all ::1/128 trust'') -Encoding ASCII') then begin
    { Write failed — restore backup, restart PG with old config, bail }
    CopyFile(HbaBackup, HbaPath, False);
    DeleteFile(HbaBackup);
    RunProc(ExpandConstant('{sys}\cmd.exe'),
      '/C net start "' + SvcName + '"', '');
    WaitForPostgres;
    Exit;
  end;

  { 4. Start PG with trust config }
  RunProc(ExpandConstant('{sys}\cmd.exe'),
    '/C net start "' + SvcName + '"', '');
  if not WaitForPostgres then begin
    { PG won't start with trust config — restore and try to recover }
    RunProc(ExpandConstant('{sys}\cmd.exe'),
      '/C net stop "' + SvcName + '" 2>nul', '');
    Sleep(1000);
    CopyFile(HbaBackup, HbaPath, False);
    DeleteFile(HbaBackup);
    RunProc(ExpandConstant('{sys}\cmd.exe'),
      '/C net start "' + SvcName + '"', '');
    WaitForPostgres;
    Exit;
  end;

  { 5. Set new password (no auth needed under trust) }
  Result := RunProc(ExpandConstant('{sys}\cmd.exe'),
    '/C "' + PsqlExe + '" -U postgres -h localhost -p 5432 -w -c ' +
    '"ALTER USER postgres PASSWORD ''' + NewPwd + ''';"', '');

  { 6. Stop PG, restore original pg_hba.conf (security-critical),
       then start PG with the restored secure config }
  RunProc(ExpandConstant('{sys}\cmd.exe'),
    '/C net stop "' + SvcName + '" 2>nul', '');
  Sleep(2000);
  CopyFile(HbaBackup, HbaPath, False);
  DeleteFile(HbaBackup);
  RunProc(ExpandConstant('{sys}\cmd.exe'),
    '/C net start "' + SvcName + '"', '');
  WaitForPostgres;
end;

{ ─────────────────────────────────────────────────────────────────────────── }
{  Credential resolution                                                     }
{ ─────────────────────────────────────────────────────────────────────────── }
{  Determines the correct postgres password for this install:                }
{    UPDATE  — .env exists and its password works → keep it.                 }
{    FRESH   — EnsurePostgres just set --superpassword → verify it.          }
{    REPAIR  — password unknown → try defaults, then force-reset.            }
{  Sets DbPassword to the working password.  Returns True on success.        }

function ResolveCredentials(var SkipEnvWrite: Boolean): Boolean;
var
  OldPwd: string;
begin
  Result := False;
  SkipEnvWrite := False;

  Status('Waiting for PostgreSQL to be ready...');
  if not WaitForPostgres then begin
    MsgBox('PostgreSQL is not responding on localhost:5432.' + #13#10 +
           'Please ensure the PostgreSQL service is running,' + #13#10 +
           'then re-run this installer.', mbError, MB_OK);
    Exit;
  end;

  { 1. Update path: existing .env password still works }
  OldPwd := ReadEnvPassword;
  if (OldPwd <> '') and TestPgAuth(OldPwd) then begin
    DbPassword := OldPwd;
    SkipEnvWrite := True;
    Status('Existing database credentials verified.');
    Result := True;
    Exit;
  end;

  { 2. Fresh install: EnsurePostgres set --superpassword to DbPassword }
  Status('Verifying PostgreSQL credentials...');
  if TestPgAuth(DbPassword) then begin
    Result := True;
    Exit;
  end;

  { 3. Repair: try ALTER with known passwords }
  Status('Configuring PostgreSQL credentials...');
  if (OldPwd <> '') and AlterPassword(OldPwd, DbPassword) then begin
    Result := True;
    Exit;
  end;
  if AlterPassword('postgres', DbPassword) then begin
    Result := True;
    Exit;
  end;

  { 4. Last resort: pg_hba.conf trust-mode reset.
       Only attempted when PostgreSQL was freshly installed by this installer.
       If PG was already present we refuse to overwrite its credentials —
       silently changing the superuser password could break other applications
       on this machine that rely on the existing postgres account. }
  if WasPostgresPreInstalled then begin
    MsgBox(
      'Could not authenticate with your existing PostgreSQL installation.' + #13#10 + #13#10 +
      'To protect other applications on this machine, the installer will not' + #13#10 +
      'reset your PostgreSQL ''postgres'' password automatically.' + #13#10 + #13#10 +
      'Please do ONE of the following, then re-run this installer:' + #13#10 + #13#10 +
      '  Option A — supply the existing password:' + #13#10 +
      '    Create or edit  ' + ExpandConstant('{app}') + '\.env' + #13#10 +
      '    and add:  PGPASSWORD=<your existing postgres password>' + #13#10 + #13#10 +
      '  Option B — set the password yourself via pgAdmin or psql:' + #13#10 +
      '    ALTER USER postgres PASSWORD ''newpassword'';' + #13#10 +
      '    Then add  PGPASSWORD=newpassword  to the .env file above.',
      mbError, MB_OK);
    Exit;
  end;

  Status('Resetting PostgreSQL password via pg_hba.conf...');
  if ForceResetPgPassword(DbPassword) then begin
    Result := True;
    Exit;
  end;

  { All attempts failed }
  MsgBox('Could not configure the PostgreSQL password.' + #13#10 + #13#10 +
         'Please try the following:' + #13#10 +
         '  1. Open pgAdmin and run:' + #13#10 +
         '       ALTER USER postgres PASSWORD ''newpassword'';' + #13#10 +
         '  2. Create or edit the file:' + #13#10 +
         '       ' + ExpandConstant('{app}') + '\.env' + #13#10 +
         '     with the line: PGPASSWORD=newpassword',
         mbError, MB_OK);
end;

{ ─────────────────────────────────────────────────────────────────────────── }
{  Dependency installers                                                     }
{ ─────────────────────────────────────────────────────────────────────────── }

procedure EnsurePython;
var
  Pkg: string;
begin
  if IsPythonInstalled then Exit;

  Status('Downloading Python 3.13...');
  Pkg := ExpandConstant('{tmp}\python_setup.exe');
  if not DownloadFile(
      'https://www.python.org/ftp/python/3.13.0/python-3.13.0-amd64.exe',
      Pkg) or not FileExists(Pkg) then begin
    MsgBox('Failed to download Python 3.13.' + #13#10 +
           'Please install it manually from' + #13#10 +
           'https://www.python.org/downloads/' + #13#10 +
           'then re-run this installer.', mbError, MB_OK);
    Exit;
  end;

  Status('Installing Python 3.13...');
  RunProc(Pkg,
    '/quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_doc=0',
    '');
  DeleteFile(Pkg);
end;

procedure EnsurePostgres;
var
  Pkg: string;
begin
  { Record whether PG existed before we ran — used by ResolveCredentials
    to decide whether a forced password reset is safe. }
  WasPostgresPreInstalled := IsPostgresInstalled;
  if WasPostgresPreInstalled then Exit;

  Status('Downloading PostgreSQL 16 (this can take a while)...');
  Pkg := ExpandConstant('{tmp}\postgresql_setup.exe');
  if not DownloadFile(
      'https://get.enterprisedb.com/postgresql/postgresql-16.6-1-windows-x64.exe',
      Pkg) or not FileExists(Pkg) then begin
    MsgBox('Failed to download PostgreSQL 16.' + #13#10 +
           'Please install it manually from' + #13#10 +
           'https://www.postgresql.org/download/' + #13#10 +
           'then re-run this installer.', mbError, MB_OK);
    Exit;
  end;

  Status('Installing PostgreSQL 16 (this may take several minutes)...');
  RunProc(Pkg,
    '--mode unattended --unattendedmodeui none' +
    ' --superpassword "' + DbPassword + '"' +
    ' --serverport 5432', '');
  DeleteFile(Pkg);
end;

{ ─────────────────────────────────────────────────────────────────────────── }
{  Python venv + packages                                                    }
{ ─────────────────────────────────────────────────────────────────────────── }

procedure SetupVenvAndPackages;
var
  AppDir, VenvPy, PythonExe: string;
  VenvCode: Integer;
  NeedPipBootstrap: Boolean;
begin
  AppDir := ExpandConstant('{app}');
  VenvPy := AppDir + '\venv\Scripts\python.exe';
  NeedPipBootstrap := False;

  { ── Create or validate venv ──────────────────────────────────────── }
  if not FileExists(VenvPy) then begin
    Status('Creating Python virtual environment...');

    PythonExe := FindPython;
    if PythonExe = '' then begin
      MsgBox('Could not find a Python installation.' + #13#10 +
             'Please install Python 3.13 from' + #13#10 +
             'https://www.python.org/downloads/' + #13#10 +
             'then re-run this installer.', mbError, MB_OK);
      Exit;
    end;

    { Clean up partial venv from a failed prior run }
    if DirExists(AppDir + '\venv') then
      DelTree(AppDir + '\venv', True, True, True);

    Status('Creating virtual environment (' + PythonExe + ')...');

    { Call Python directly — cmd.exe /C mangles quoted paths }
    VenvCode := -1;
    Exec(PythonExe, '-m venv "' + AppDir + '\venv"', AppDir,
         SW_HIDE, ewWaitUntilTerminated, VenvCode);

    if (VenvCode <> 0) or not FileExists(VenvPy) then begin
      { Retry without pip — ensurepip can fail on some systems }
      if DirExists(AppDir + '\venv') then
        DelTree(AppDir + '\venv', True, True, True);

      Status('Retrying venv without pip...');
      VenvCode := -1;
      Exec(PythonExe, '-m venv --without-pip "' + AppDir + '\venv"',
           AppDir, SW_HIDE, ewWaitUntilTerminated, VenvCode);

      if (VenvCode <> 0) or not FileExists(VenvPy) then begin
        MsgBox('Failed to create Python virtual environment.' + #13#10 +
               'Exit code: ' + IntToStr(VenvCode) + #13#10 +
               'Python: ' + PythonExe + #13#10 + #13#10 +
               'Manual retry:' + #13#10 +
               '  cd "' + AppDir + '"' + #13#10 +
               '  "' + PythonExe + '" -m venv venv', mbError, MB_OK);
        Exit;
      end;
      NeedPipBootstrap := True;
    end;
  end;

  if not FileExists(VenvPy) then Exit;

  { ── Bootstrap pip if venv was created with --without-pip ─────────── }
  Status('Installing Python packages...');
  if NeedPipBootstrap then begin
    if not RunProc(VenvPy, '-m ensurepip --upgrade', AppDir) then begin
      MsgBox('Failed to bootstrap pip.' + #13#10 +
             'Manual retry:' + #13#10 +
             '  cd "' + AppDir + '"' + #13#10 +
             '  venv\Scripts\python.exe -m ensurepip --upgrade',
             mbError, MB_OK);
      Exit;
    end;
  end;

  { ── Verify pip ───────────────────────────────────────────────────── }
  if not RunProc(VenvPy, '-m pip --version', AppDir) then begin
    MsgBox('pip is not available in the virtual environment.' + #13#10 +
           'Manual retry:' + #13#10 +
           '  cd "' + AppDir + '"' + #13#10 +
           '  venv\Scripts\python.exe -m ensurepip --upgrade',
           mbError, MB_OK);
    Exit;
  end;

  RunProc(VenvPy, '-m pip install --upgrade pip setuptools', AppDir);

  { ── Install all packages ──────────────────────────────────────────── }
  Status('Installing Python packages (this may take a few minutes)...');
  if not RunProc(VenvPy,
      '-m pip install -r "' + AppDir + '\requirements.txt"', AppDir) then begin
    MsgBox('Failed to install Python packages.' + #13#10 +
           'Manual retry:' + #13#10 +
           '  cd "' + AppDir + '"' + #13#10 +
           '  venv\Scripts\pip install -r requirements.txt',
           mbError, MB_OK);
    Exit;
  end;

  { ── Verify streamlit landed ──────────────────────────────────────── }
  if not FileExists(AppDir + '\venv\Scripts\streamlit.exe') then begin
    MsgBox('Streamlit was not installed correctly.' + #13#10 +
           'Manual retry:' + #13#10 +
           '  cd "' + AppDir + '"' + #13#10 +
           '  venv\Scripts\pip install streamlit', mbError, MB_OK);
  end;
end;

{ ─────────────────────────────────────────────────────────────────────────── }
{  Database initialization                                                   }
{ ─────────────────────────────────────────────────────────────────────────── }

procedure InitializeDatabase;
var
  AppDir, VenvPy, PgBinDir: string;
begin
  AppDir := ExpandConstant('{app}');
  VenvPy := AppDir + '\venv\Scripts\python.exe';
  PgBinDir := FindPgBinDir;

  if not FileExists(VenvPy) then Exit;

  Status('Initializing database...');

  { Create the betrivers_tracker database if it does not already exist.
    createdb.exe exits non-zero if the DB is already there — suppress that. }
  if (PgBinDir <> '') and FileExists(PgBinDir + '\createdb.exe') then
    RunProc(ExpandConstant('{sys}\cmd.exe'),
      '/C set PGPASSWORD=' + DbPassword +
      '& "' + PgBinDir + '\createdb.exe"' +
      ' -U postgres -h localhost -p 5432 -w betrivers_tracker 2>nul', '');

  { Run the app''s schema migration / init command }
  if not RunProc(VenvPy, '-m app.cli init', AppDir) then
    MsgBox('Database initialization failed.' + #13#10 +
           'Manual retry:' + #13#10 +
           '  cd "' + AppDir + '"' + #13#10 +
           '  venv\Scripts\python.exe -m app.cli init',
           mbError, MB_OK);
end;

{ ─────────────────────────────────────────────────────────────────────────── }
{  Inno Setup event hooks                                                    }
{ ─────────────────────────────────────────────────────────────────────────── }

procedure InitializeWizard;
begin
  { All work happens in CurStepChanged after files are copied. }
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  SkipEnvWrite: Boolean;
begin
  if CurStep = ssPostInstall then begin
    { Remove stale Python bytecode from prior installs so the
      interpreter always recompiles from the fresh .py files. }
    DelTree(ExpandConstant('{app}\app\__pycache__'), True, True, True);
    DelTree(ExpandConstant('{app}\app\data_access\__pycache__'), True, True, True);
    DelTree(ExpandConstant('{app}\app\ui\__pycache__'), True, True, True);
    DelTree(ExpandConstant('{app}\app\ui\components\__pycache__'), True, True, True);
    DelTree(ExpandConstant('{app}\app\ui\views\__pycache__'), True, True, True);
    DelTree(ExpandConstant('{app}\app\viewmodels\__pycache__'), True, True, True);

    { Pre-generate a password.  For fresh installs this goes to
      EnsurePostgres --superpassword.  For updates, ResolveCredentials
      will replace it with the existing .env password. }
    DbPassword := GeneratePassword;

    { Step 1: System prerequisites }
    EnsurePython;
    EnsurePostgres;

    { Step 2: Credential resolution }
    if not ResolveCredentials(SkipEnvWrite) then Exit;
    if not SkipEnvWrite then WriteEnvFile;

    { Step 3: Python environment + packages }
    SetupVenvAndPackages;

    { Step 4: Database }
    InitializeDatabase;
  end;
end;
