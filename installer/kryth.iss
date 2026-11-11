#define AppName "KRYTH"
#define AppVersion "0.0.38"
#define WheelName "kryth-" + AppVersion + "-py3-none-any.whl"

[Setup]
AppId={{B3AE2267-2C14-4D28-9C09-5C3BA3D3A08D}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=KRYTH
DefaultDirName={tmp}\KRYTH-Setup
Uninstallable=no
CreateAppDir=no
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist\installer
OutputBaseFilename=KRYTH-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Files]
Source: "..\dist\{#WheelName}"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Code]
var
  PythonSelector: String;

function FindSupportedPython(): Boolean;
var
  Minor: Integer;
  ExitCode: Integer;
  Candidate: String;
begin
  Result := False;
  for Minor := 14 downto 10 do
  begin
    Candidate := '-3.' + IntToStr(Minor);
    if Exec('py.exe', Candidate + ' -m pip --version', '', SW_HIDE,
      ewWaitUntilTerminated, ExitCode) and (ExitCode = 0) then
    begin
      PythonSelector := Candidate;
      Result := True;
      Exit;
    end;
  end;
end;

function InitializeSetup(): Boolean;
begin
  Result := FindSupportedPython();
  if not Result then
    MsgBox('KRYTH requires Python 3.10–3.14 and pip. Install a supported Python version and run this installer again.',
      mbError, MB_OK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ExitCode: Integer;
  WheelPath: String;
begin
  if CurStep <> ssPostInstall then
    Exit;

  WheelPath := ExpandConstant('{tmp}\{#WheelName}');
  if not Exec('py.exe', PythonSelector + ' -m pip install --user --upgrade "' + WheelPath + '"',
    '', SW_SHOWNORMAL, ewWaitUntilTerminated, ExitCode) or (ExitCode <> 0) then
    RaiseException('KRYTH wheel installation failed. Check that pip can install wheels for the selected Python version, then retry.');

  if not Exec('py.exe', PythonSelector + ' -m kryth setup --path', '', SW_SHOWNORMAL,
    ewWaitUntilTerminated, ExitCode) or (ExitCode <> 0) then
    RaiseException('KRYTH could not configure the user PATH. Run `python -m kryth setup --path` from a new terminal.');

  if not Exec('py.exe', PythonSelector + ' -m kryth --version', '', SW_SHOWNORMAL,
    ewWaitUntilTerminated, ExitCode) or (ExitCode <> 0) then
    RaiseException('KRYTH installation validation failed.');
end;
