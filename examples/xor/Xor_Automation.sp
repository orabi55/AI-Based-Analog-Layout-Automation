*Custom Compiler Version V-2023.12-6
*Sun Feb 22 23:50:37 2026

*.SCALE METER
*.LDD

********************************************************************************
* Library          : Test
* Cell             : Xor_Automation
* View             : schematic
* View Search List : auCdl schematic
* View Stop List   : auCdl
********************************************************************************
.subckt Xor_Automation A B OUT VDD VSS
*.PININFO A:I B:I OUT:O VDD:B VSS:B
MM28 Bb B VSS VSS n08 l=0.014u nf=1 nfin=4
MM5 PDN Bb VSS VSS n08 l=0.014u nf=1 nfin=4
MM4 PDN A VSS VSS n08 l=0.014u nf=1 nfin=4
MM3 OUT B PDN VSS n08 l=0.014u nf=1 nfin=4
MM2 OUT Ab PDN VSS n08 l=0.014u nf=1 nfin=4
MM25 Ab A VSS VSS n08 l=0.014u nf=1 nfin=4
MM29 Bb B VDD VDD p08 l=0.014u nf=1 m=1 nfin=4
MM9 PUN Ab VDD VDD p08 l=0.014u nf=1 m=1 nfin=4
MM8 PUN Bb VDD VDD p08 l=0.014u nf=1 m=1 nfin=4
MM7 OUT B PUN VDD p08 l=0.014u nf=1 m=1 nfin=4
MM6 OUT A PUN VDD p08 l=0.014u nf=1 m=1 nfin=4
MM24 Ab A VDD VDD p08 l=0.014u nf=1 m=1 nfin=4
.ends Xor_Automation


