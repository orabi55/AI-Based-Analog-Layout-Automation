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
MM21 VOUTN net15 net20 gnd! n08 l='LNLAT' nf=1 nfin='NNLAT'
MM20 VOUTP net20 net15 gnd! n08 l='LNLAT' nf=1 nfin='NNLAT'
MM19 net9 CLK net11 net11 n08 l='LCLK' nf=1 nfin='NCLK'
MM18 net15 VINN net9 gnd! n08 l='Lin' nf=1 nfin='Nin'
MM17 net20 VINP net9 gnd! n08 l='Lin' nf=1 nfin='Nin'
MM4 VOUTN net15 net20 gnd! n08 l='LNLAT' nf=1 nfin='NNLAT'
MM3 VOUTP net20 net15 gnd! n08 l='LNLAT' nf=1 nfin='NNLAT'
MM2 net9 CLK net11 net11 n08 l='LCLK' nf=1 nfin='NCLK'
MM1 net15 VINN net9 gnd! n08 l='Lin' nf=1 nfin='Nin'
MM0 net20 VINP net9 gnd! n08 l='Lin' nf=1 nfin='Nin'
MM27 net20 CLK VDD VDD p08 l='LSW' nf=1 m=1 nfin='NSW'
MM26 VOUTN CLK VDD VDD p08 l='LSW' nf=1 m=1 nfin='NSW'
MM25 VOUTP CLK VDD VDD p08 l='LSW' nf=1 m=1 nfin='NSW'
MM24 net15 CLK VDD VDD p08 l='LSW' nf=1 m=1 nfin='NSW'
MM23 VOUTN VOUTP VDD VDD p08 l='LPLAT' nf=1 m=1 nfin='NPLAT'
MM22 VOUTP VOUTN VDD VDD p08 l='LPLAT' nf=1 m=1 nfin='NPLAT'
MM10 net20 CLK VDD VDD p08 l='LSW' nf=1 m=1 nfin='NSW'
MM9 VOUTN CLK VDD VDD p08 l='LSW' nf=1 m=1 nfin='NSW'
MM8 VOUTP CLK VDD VDD p08 l='LSW' nf=1 m=1 nfin='NSW'
MM7 net15 CLK VDD VDD p08 l='LSW' nf=1 m=1 nfin='NSW'
MM6 VOUTN VOUTP VDD VDD p08 l='LPLAT' nf=1 m=1 nfin='NPLAT'
MM5 VOUTP VOUTN VDD VDD p08 l='LPLAT' nf=1 m=1 nfin='NPLAT'
.ends Comparator


