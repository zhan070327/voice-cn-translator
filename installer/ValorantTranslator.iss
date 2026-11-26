#if GetEnv("VALORANT_CPU_PACKAGE") == "1"
  #define MyAppName "同频 CPU 版"
  #define MySetupName "SameFreq_CPU_Beta_Setup"
#else
  #define MyAppName "同频"
  #define MySetupName "SameFreq_Beta_Setup"
#endif
#define MyAppVersion "0.6.0"
#define MyAppExe "ValorantTranslator.exe"

[Setup]
#if GetEnv("VALORANT_CPU_PACKAGE") == "1"
AppId={{0357A036-BE6C-4799-881E-8E8CB660EB3A}
DefaultDirName={localappdata}\Programs\VoiceCNTranslatorCPU
#else
AppId={{32A11F0B-7D49-4C23-846A-D7CAFA048E12}
DefaultDirName={localappdata}\Programs\VoiceCNTranslator
#endif
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
UsePreviousAppDir=yes
CloseApplications=yes
OutputDir=..\dist
OutputBaseFilename={#MySetupName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\assets\app_icon.ico
UninstallDisplayIcon={app}\{#MyAppExe}
ArchitecturesAllowed=x64compatible

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: checkedonce

[Files]
Source: "..\dist\ValorantTranslator\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "立即启动同频"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  OldGlossary, UserGlossary: String;
begin
  if CurStep = ssInstall then
  begin
    OldGlossary := ExpandConstant('{app}\_internal\config\glossary.json');
    UserGlossary := ExpandConstant('{localappdata}\ValorantTranslator\glossary_custom.json');
    if FileExists(OldGlossary) and not FileExists(UserGlossary) then
    begin
      if not DirExists(ExtractFileDir(UserGlossary)) then
        if not CreateDir(ExtractFileDir(UserGlossary)) then
          RaiseException('无法创建用户词库目录；安装已停止以保护原词库。');
      if not CopyFile(OldGlossary, UserGlossary, False) then
        RaiseException('无法备份旧词库；安装已停止以保护原词库。');
    end;
  end;
end;
