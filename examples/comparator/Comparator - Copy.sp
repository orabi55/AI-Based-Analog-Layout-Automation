*Custom Compiler Version V-2023.12-6
*Wed Mar 11 23:43:55 2026

*.SCALE METER
*.LDD
.GLOBAL gnd!
********************************************************************************
* Library          : Design
* Cell             : Comparator
* View             : schematic
* View Search List : auCdl schematic
* View Stop List   : auCdl
********************************************************************************
.subckt Comparator CLK VDD VINN VINP VOUTN VOUTP
*.PININFO CLK:I VDD:I VINN:I VINP:I VOUTN:O VOUTP:O
MM21 VOUTN net15 net20 gnd! n08 l=0.014u nf=1 nfin=5
MM20 VOUTP net20 net15 gnd! n08 l=0.014u nf=1 nfin=5
MM19 net9 CLK net11 net11 n08 l=0.014u nf=1 nfin=5
MM18 net15 VINN net9 gnd! n08 l=0.014u nf=1 nfin=5
MM17 net20 VINP net9 gnd! n08 l=0.014u nf=1 nfin=5
MM4 VOUTN net15 net20 gnd! n08 l=0.014u nf=1 nfin=5
MM3 VOUTP net20 net15 gnd! n08 l=0.014u nf=1 nfin=5
MM2 net9 CLK net11 net11 n08 l=0.014u nf=1 nfin=5
MM1 net15 VINN net9 gnd! n08 l=0.014u nf=1 nfin=5
MM0 net20 VINP net9 gnd! n08 l=0.014u nf=1 nfin=5
MM27 net20 CLK VDD VDD p08 l=0.014u nf=1 m=1 nfin=5
MM26 VOUTN CLK VDD VDD p08 l=0.014u nf=1 m=1 nfin=5
MM25 VOUTP CLK VDD VDD p08 l=0.014u nf=1 m=1 nfin=5
MM24 net15 CLK VDD VDD p08 l=0.014u nf=1 m=1 nfin=5
MM23 VOUTN VOUTP VDD VDD p08 l=0.014u nf=1 m=1 nfin=5
MM22 VOUTP VOUTN VDD VDD p08 l=0.014u nf=1 m=1 nfin=5
MM10 net20 CLK VDD VDD p08 l=0.014u nf=1 m=1 nfin=5
MM9 VOUTN CLK VDD VDD p08 l=0.014u nf=1 m=1 nfin=5
MM8 VOUTP CLK VDD VDD p08 l=0.014u nf=1 m=1 nfin=5
MM7 net15 CLK VDD VDD p08 l=0.014u nf=1 m=1 nfin=5
MM6 VOUTN VOUTP VDD VDD p08 l=0.014u nf=1 m=1 nfin=5
MM5 VOUTP VOUTN VDD VDD p08 l=0.014u nf=1 m=1 nfin=5
.ends Comparator


