# C:\RAG\scripts\weekly_run.ps1
# Runs the full RAG pipeline weekly (or on demand) on Windows.
# Steps: fetch -> process -> chunk -> embed -> update_index (ingest+prune+snapshot)
param(
  [int]$DaysBack = 7,
  [int]$PmcDays = 30,
  [int]$GuidelineDays = 30,
  [int]$PdfMaxMB = 8
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Write-Step($msg) { Write-Host "[$(Get-Date -Format HH:mm:ss)] $msg" }

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = Split-Path -Parent $root
$python = Join-Path $repo 'ragvenv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }

$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$runDir = Join-Path $repo "runs\$stamp"
New-Item -ItemType Directory -Force -Path $runDir | Out-Null

function Run-Tool([string]$name, [string[]]$toolArgs) {
  $log = Join-Path $runDir ("$name.log")
  Write-Step "Running $name"
  Push-Location $repo
  try {
    Write-Host "  -> $python $($toolArgs -join ' ')"
    & $python @toolArgs 2>&1 | Tee-Object -FilePath $log | Out-Null
    $exit = $LASTEXITCODE
  }
  finally {
    Pop-Location
  }
  if ($exit -ne 0) {
    throw "Step '$name' failed with exit code $exit. See $log"
  }
}

try {
  # 1) Fetch new items (past $DaysBack days)
  Run-Tool 'fetch_sources' @('fetch_sources.py','--days', "$DaysBack")
  # PMC OA subset (limit the window by years)
  Run-Tool 'pmc_fetcher' @('pmc_fetcher.py','--oa-subset','--days', "$PmcDays", '--max','2000')
  # Guidelines crawl
  $gArgs = @('guidelines_fetcher.py','--days', "$GuidelineDays", '--limit-per-source','120','--depth','2','--timeout','45','--fetch-pdf','--pdf-max-mb', "$PdfMaxMB")
  Run-Tool 'guidelines_fetcher' $gArgs

  # 2) Process to decision-useful corpus (run across new raw files)
  $rawDir = Join-Path $repo 'raw_docs'
  $cleanDir = Join-Path $repo 'clean_corpus'
  New-Item -ItemType Directory -Force -Path $cleanDir | Out-Null
  $cutoff = (Get-Date).AddDays(-$DaysBack)
  Get-ChildItem $rawDir -File -Include *.json,*.jsonl | Where-Object { $_.LastWriteTime -ge $cutoff } | ForEach-Object {
    $outName = ($_.BaseName + '.processed.jsonl')
    $outPath = Join-Path $cleanDir $outName
    Run-Tool "process_clinical_corpus_$($_.BaseName)" @('process_clinical_corpus.py','--in', $_.FullName, '--out', $outPath, '--fulltext')
  }

  # 3) Chunk
  Run-Tool 'chunking_pipeline' @('chunking_pipeline.py','--input','./clean_corpus','--pattern','*.processed.jsonl','--output','./chunks')

  # 4) Embed (portable artifacts only; do not upsert here)
  Run-Tool 'embed_chunks' @('embed_chunks.py','--input','./chunks','--output','./embeddings','--batch','64')

  # 5) Update index (ingest + prune + snapshot)
  Run-Tool 'update_index' @('update_index.py','--emb-dir','./embeddings','--chunk-dir','./chunks','--snapshots','both')

  # 6) Generate clinician-facing document summaries (cached for UI)
  Run-Tool 'summarize_updates' @('summarize_recent_updates.py','--max-docs','800')

  Write-Step "Weekly run completed. Logs in $runDir"
}
catch {
  Write-Error $_.Exception.Message
  Write-Host "Logs at: $runDir"
  exit 1
}
