@echo off
setlocal EnableDelayedExpansion

rem The Windows sibling of `bin/enchiridion` (see that file for the full
rem contract). A thin shim over the TypeScript port's esbuild bundle
rem (ADR-0017, issue #254): execs `node` against enchiridion-ts\dist\cli.js,
rem forwarding every argument untouched. This is exactly the fix ADR-0017
rem exists to ship on Windows -- Defender ASR rule 01443614 blocks the
rem unsigned, low-prevalence enchiridion.exe this file used to lazy-fetch,
rem but node.exe is prevalent, signed, and IT-trusted, so it clears the
rem rule while the payload rides inside it as data.
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

rem enchiridion-ts\ is a sibling of wiki-plugin\ in this monorepo; packaging
rem the bundle inside a distributed plugin directory (ADR-0017's "ships
rem inside the plugin directory") is a later distribution ticket, not yet
rem wired up here.
set "bundle=%plugin_root%\..\enchiridion-ts\dist\cli.js"

node "%bundle%" %*
exit /b %ERRORLEVEL%
