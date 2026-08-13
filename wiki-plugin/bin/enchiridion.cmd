@echo off
setlocal

rem The Windows sibling of `bin/enchiridion` (see that file for the full
rem contract). Resolves the platform binary -- lazy-fetching it on first use
rem via bootstrap\install.ps1 (docs/adr/0013) -- and execs it with every
rem argument passed through untouched.
rem
rem Two escape hatches, both for development against an unreleased binary:
rem
rem   ENCHIRIDION_BIN      absolute path to a binary to use as-is; nothing is
rem                        downloaded. Set this to `go build`'s output when
rem                        working on enchiridion-go.
rem   ENCHIRIDION_VERSION  release version to fetch, overriding plugin.json.

set "plugin_root=%~dp0.."

if defined ENCHIRIDION_BIN (
    "%ENCHIRIDION_BIN%" %*
    exit /b %ERRORLEVEL%
)

set "version=%ENCHIRIDION_VERSION%"
if not defined version (
    set "manifest=%plugin_root%\.claude-plugin\plugin.json"
    if not exist "%manifest%" (
        echo enchiridion: %manifest% not found -- cannot determine which release to fetch 1>&2
        exit /b 1
    )
    for /f "usebackq tokens=* delims=" %%v in (`powershell -NoProfile -Command "(Get-Content -Raw '%manifest%' | Select-String -Pattern '\"version\"\s*:\s*\"([^\"]*)\"').Matches[0].Groups[1].Value"`) do set "version=%%v"
    if not defined version (
        echo enchiridion: no "version" in %manifest% 1>&2
        exit /b 1
    )
)

for /f "usebackq tokens=* delims=" %%b in (`powershell -NoProfile -ExecutionPolicy Bypass -File "%plugin_root%\bootstrap\install.ps1" -PluginRoot "%plugin_root%" -Version "%version%"`) do set "binary=%%b"
if not defined binary (
    echo enchiridion: bootstrap failed to resolve a binary path 1>&2
    exit /b 1
)

"%binary%" %*
exit /b %ERRORLEVEL%
