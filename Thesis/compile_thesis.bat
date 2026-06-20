@echo off
echo ==============================================================================
echo Compilation Script for AI Analog Layout Automation Thesis
echo ==============================================================================

rem Clean previous auxiliary files
del *.aux *.log *.toc *.lof *.lot *.out *.blg *.bbl /q /f 2>nul
del chapters\*.aux /q /f 2>nul
del frontmatter\*.aux /q /f 2>nul
del appendices\*.aux /q /f 2>nul

echo Running PDFlatex (Pass 1)...
pdflatex -interaction=nonstopmode main.tex

echo Running BibTeX...
bibtex main

echo Running PDFlatex (Pass 2)...
pdflatex -interaction=nonstopmode main.tex

echo Running PDFlatex (Pass 3 - Resolving Citations)...
pdflatex -interaction=nonstopmode main.tex

echo Cleaning auxiliary files...
del *.aux *.log *.toc *.lof *.lot *.out *.blg *.bbl *.listing /q /f 2>nul
del chapters\*.aux /q /f 2>nul
del frontmatter\*.aux /q /f 2>nul
del appendices\*.aux /q /f 2>nul

echo ==============================================================================
echo Thesis compilation completed. PDF compiled to main.pdf
echo ==============================================================================


