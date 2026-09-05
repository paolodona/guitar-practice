# Fetch the pinned Rubber Band WASM build and verify it.
#
# The binary is GPLv2+, so it is not committed here — the repo does not distribute
# it until the licence decision in the plan's Decision 1 is confirmed and A2 vendors
# it into web/vendor/rubberband/. This script puts a copy next to the probe pages.
#
#   powershell -ExecutionPolicy Bypass -File .\fetch-wasm.ps1

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

$version   = '3.3.0'
$tarball   = "https://registry.npmjs.org/rubberband-wasm/-/rubberband-wasm-$version.tgz"
$tgzSha    = 'dc7f414101aa88ca26af241b6bfb4ea020bab80c32f81d0f52244a0d1d2f2cbc'
$wasmSha   = '496d880b4b585e2afb67be56cf8e14f14d8e1c911f732af0be60b826f07c04dc'

$tmp = Join-Path $here '.fetch'
if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
New-Item -ItemType Directory -Path $tmp | Out-Null

$tgz = Join-Path $tmp "rubberband-wasm-$version.tgz"
Write-Output "downloading $tarball"
Invoke-WebRequest -Uri $tarball -OutFile $tgz

$got = (Get-FileHash -Algorithm SHA256 $tgz).Hash.ToLower()
if ($got -ne $tgzSha) { throw "tarball sha256 mismatch: got $got, expected $tgzSha" }

tar -xzf $tgz -C $tmp
$src = Join-Path $tmp 'package/dist/rubberband.wasm'
if (-not (Test-Path $src)) { throw "dist/rubberband.wasm missing from the tarball" }

$got = (Get-FileHash -Algorithm SHA256 $src).Hash.ToLower()
if ($got -ne $wasmSha) { throw "wasm sha256 mismatch: got $got, expected $wasmSha" }

Copy-Item $src (Join-Path $here 'rubberband.wasm') -Force
# Keep the build recipe and the C shim: they are the corresponding source for the
# binary, which is what GPLv2 section 3 asks us to be able to hand over.
Copy-Item (Join-Path $tmp 'package/build.sh')       (Join-Path $here 'upstream-build.sh')   -Force
Copy-Item (Join-Path $tmp 'package/src/rubberband.c') (Join-Path $here 'upstream-shim.c')   -Force
Copy-Item (Join-Path $tmp 'package/LICENSE')        (Join-Path $here 'upstream-LICENSE')    -Force
Remove-Item -Recurse -Force $tmp

Write-Output "rubberband.wasm $version verified and in place"
