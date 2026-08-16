@echo off
setlocal EnableDelayedExpansion

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
rem
rem And one for callers that must not pay for a download:
rem
rem   ENCHIRIDION_NO_FETCH set to anything to use the cached binary if there is
rem                        one and fail immediately if there isn't, instead of
rem                        fetching.
rem
rem Before falling back to the lazy-fetch bootstrap, prefer an already
rem PATH-installed binary (docs/adr/0014 -- brew/choco distribution as an
rem optional accelerator over the fetch, never a signing substitute): if
rem `enchiridion.exe` resolves on PATH (deliberately `.exe`, not a bare
rem `where enchiridion` -- so this never matches this .cmd file itself and
rem never depends on PATHEXT ordering), its `version` output must match
rem plugin.json's version exactly, or this halts rather than silently
rem fetching a second, divergent binary.

set "plugin_root=%~dp0.."

if defined ENCHIRIDION_BIN (
    "%ENCHIRIDION_BIN%" %*
    exit /b %ERRORLEVEL%
)

set "manifest=%plugin_root%\.claude-plugin\plugin.json"

set "path_bin="
for /f "usebackq tokens=* delims=" %%p in (`where enchiridion.exe 2^>nul`) do (
    if not defined path_bin set "path_bin=%%p"
)

if defined path_bin (
    set "path_version="
    for /f "usebackq tokens=* delims=" %%v in (`"!path_bin!" version 2^>nul`) do set "path_version=%%v"
    if defined path_version (
        call :read_plugin_version
        set "path_version_norm=!path_version!"
        if "!path_version_norm:~0,1!"=="v" set "path_version_norm=!path_version_norm:~1!"
        set "plugin_version_norm=!plugin_version!"
        if "!plugin_version_norm:~0,1!"=="v" set "plugin_version_norm=!plugin_version_norm:~1!"
        if "!path_version_norm!"=="!plugin_version_norm!" (
            "!path_bin!" %*
            exit /b !ERRORLEVEL!
        ) else (
            echo enchiridion !path_version_norm! is behind plugin !plugin_version_norm!; run: choco upgrade enchiridion 1>&2
            exit /b 1
        )
    )
)

set "version=%ENCHIRIDION_VERSION%"
if not defined version (
    call :read_plugin_version
    set "version=!plugin_version!"
)

for /f "usebackq tokens=* delims=" %%b in (`powershell -NoProfile -ExecutionPolicy Bypass -File "%plugin_root%\bootstrap\install.ps1" -PluginRoot "%plugin_root%" -Version "%version%"`) do set "binary=%%b"
if not defined binary (
    echo enchiridion: bootstrap failed to resolve a binary path 1>&2
    exit /b 1
)

"%binary%" %*
exit /b %ERRORLEVEL%

:read_plugin_version
if not exist "%manifest%" (
    echo enchiridion: %manifest% not found -- cannot determine which release to fetch 1>&2
    exit /b 1
)
set "plugin_version="
for /f "usebackq tokens=* delims=" %%v in (`powershell -NoProfile -Command "(Get-Content -Raw '%manifest%' | Select-String -Pattern '\"version\"\s*:\s*\"([^\"]*)\"').Matches[0].Groups[1].Value"`) do set "plugin_version=%%v"
if not defined plugin_version (
    echo enchiridion: no "version" in %manifest% 1>&2
    exit /b 1
)
goto :eof
