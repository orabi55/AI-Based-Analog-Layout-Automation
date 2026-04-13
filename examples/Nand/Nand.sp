*Custom Compiler Version V-2023.12-6
*Mon Apr 13 02:56:28 2026

*.SCALE METER
*.LDD

********************************************************************************
* Library          : Test
* Cell             : Nand
* View             : schematic
* View Search List : auCdl schematic
* View Stop List   : auCdl
********************************************************************************
.subckt Nand A B VDD VSS Y
*.PININFO A:I B:I VDD:B VSS:B Y:O
MM2 net5 B VSS VSS n08 l=0.014u nf=1 nfin=4
MM1 Y A net5 VSS n08 l=0.014u nf=1 nfin=4
MM4 Y B VDD VDD p08 l=0.014u nf=1 m=1 nfin=4
MM3 Y A VDD VDD p08 l=0.014u nf=1 m=1 nfin=4
.ends Nand


