<#
Word fills the page numbers in the Index and the Lists of Figures and Tables (PAGEREF fields) and
writes a PDF beside the .docx, for checking the layout. Run after assemble_manuscript.py:

    powershell -ExecutionPolicy Bypass -File docs/research/finalize_manuscript.ps1 [path.docx]
#>
param([string]$Docx = (Join-Path $PSScriptRoot "..\final\TRIAD_Manuscript_26FSPDSCPMID4096.docx"))

$Docx = (Resolve-Path $Docx).Path
$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0
try {
    $doc = $word.Documents.Open($Docx, $false, $false)
    # Twice: replacing the "?" placeholders can move text onto another page.
    foreach ($pass in 1..2) {
        [void]$doc.Fields.Update()
        $doc.Repaginate()
    }
    $doc.Save()
    $pdf = [IO.Path]::ChangeExtension($Docx, ".pdf")
    $doc.ExportAsFixedFormat($pdf, 17)   # wdExportFormatPDF
    "pages: " + $doc.ComputeStatistics(2)   # wdStatisticPages
    $doc.Close($false)
    "wrote $pdf"
}
finally {
    $word.Quit()
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($word)
}
