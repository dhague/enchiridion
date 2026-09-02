@echo off
setlocal

rem The Windows sibling of `bin/enchiridion` (see that file for the full
rem contract). A thin shim over the esbuild bundle (ADR-0017, issue #254):
rem execs `node` against the bundle, forwarding every argument untouched.
rem
rem One escape hatch, for development against unbundled source or an
rem alternate runtime:
rem
rem   ENCHIRIDION_BIN   an executable to run as-is instead of `node <bundle>`;
rem                     nothing else in this script runs. Point it at a
rem                     `tsx`/`ts-node` invocation of
rem                     enchiridion-ts\src\cli.ts to iterate without
rem                     rebuilding.

set "plugin_root=%~dp0.."

if defined ENCHIRIDION_BIN (
    "%ENCHIRIDION_BIN%" %*
    exit /b %ERRORLEVEL%
)

rem Artifact resolution order after packaging (D3 #288):
rem   1. In-plugin bundle %plugin_root%\scripts\cli.cjs if present — the
rem      shipped, committed artifact a marketplace install sees
rem      (enchiridion-ts absent).
rem   2. Sibling dev path %plugin_root%\..\enchiridion-ts\dist\cli.cjs — the
rem      pre-release monorepo build output (scripts\ has no bundle yet).
rem In a release both exist and the in-plugin bundle wins.
if exist "%plugin_root%\scripts\cli.cjs" (
    set "bundle=%plugin_root%\scripts\cli.cjs"
) else (
    set "bundle=%plugin_root%\..\enchiridion-ts\dist\cli.cjs"
)

node "%bundle%" %*
exit /b %ERRORLEVEL%
